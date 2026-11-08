"""Run every fetcher, isolating failures. Usage: python -m afw.fetch [--only funds,prices,...] [--backfill-max N]"""
import argparse
import json
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

from afw import store
from afw.sources import funds, market

ROOT = Path(__file__).resolve().parent.parent
CONFIG = yaml.safe_load((ROOT / "config" / "universe.yml").read_text())
STATUS_PATH = store.DATA / "status.json"
BACKFILL_START = date(2024, 1, 2)  # Taiwan / Korea daily-flow history starts here


def _load_status() -> dict:
    return json.loads(STATUS_PATH.read_text()) if STATUS_PATH.exists() else {}


def _run(status: dict, key: str, fn) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = status.setdefault(key, {})
    entry["last_attempt"] = now
    try:
        rows = fn()
        entry.update(ok=True, last_success=now, rows=int(rows), error=None)
        print(f"  ok   {key:<22} {rows} rows")
    except Exception as e:  # noqa: BLE001 — one broken source must not stop the rest
        entry.update(ok=False, error=f"{type(e).__name__}: {e}"[:300])
        print(f"  FAIL {key:<22} {entry['error']}")
        traceback.print_exc(limit=1)


def fetch_prices(status: dict) -> None:
    tickers = list(dict.fromkeys(CONFIG["prices"] + [f["ticker"] for f in CONFIG["funds"]]))
    for t in tickers:
        def go(t=t):
            n = store.upsert("prices", market.cboe(t), ["date", "ticker"])
            try:  # top up the last day or two that Cboe hasn't published yet
                n += store.upsert("prices", market.nasdaq_recent(t), ["date", "ticker"])
            except Exception as e:  # noqa: BLE001
                print(f"       {t}: nasdaq top-up skipped ({e})")
            return n

        _run(status, f"prices:{t}", go)


def fetch_funds(status: dict) -> None:
    prices = store.read("prices")  # nasdaq snapshots are dated by matching against these
    for f in CONFIG["funds"]:
        fetcher = funds.FETCHERS[f["source"]]
        _run(
            status,
            f"fund:{f['ticker']}",
            lambda f=f, fetcher=fetcher: store.upsert(
                "fund_shares", fetcher(f["ticker"], path=f.get("path"), prices=prices), ["date", "ticker"]
            ),
        )
    _run(
        status,
        "concentration:SPY",
        lambda: store.upsert("concentration", funds.spy_concentration(CONFIG["ai_basket"]), ["date"]),
    )


def fetch_cftc(status: dict) -> None:
    for c in CONFIG["cftc"]:
        _run(status, f"cftc:{c['code']}", lambda c=c: store.upsert("cftc", market.cftc(c["code"], c["name"]), ["date", "contract"]))


def fetch_fred(status: dict) -> None:
    for s in CONFIG["fred"]:
        _run(status, f"fred:{s}", lambda s=s: store.upsert("fred", market.fred(s), ["date", "series"]))
    # Fresher than FRED for the yen-carry signal; stored alongside FRED series as "macro".
    _run(status, "ecb:USDJPY", lambda: store.upsert("fred", market.ecb_usdjpy(), ["date", "series"]))
    _run(status, "mof:JGB2Y", lambda: store.upsert("fred", market.mof_jgb2y(), ["date", "series"]))
    # Fed Z.1 equity holdings (quarterly) for the passive-share context signal; kept out of macro.
    for s in CONFIG.get("passive_share", {}).get("z1", {}).values():
        _run(status, f"fred:{s}", lambda s=s: store.upsert("z1", market.fred(s), ["date", "series"]))


def fetch_asia_indices(status: dict) -> None:
    """KOSPI (one request) and TAIEX (one request per month, refetching the latest stored month)."""
    _run(status, "prices:KOSPI", lambda: store.upsert("prices", market.kospi_index(), ["date", "ticker"]))

    def taiex():
        have = store.read("prices")
        have = have[have["ticker"] == "TAIEX"] if not have.empty else have
        d = have["date"].max().date().replace(day=1) if not have.empty else BACKFILL_START.replace(day=1)
        today, n = date.today(), 0
        while d <= today:
            n += store.upsert("prices", market.taiex_month(d), ["date", "ticker"])
            d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        return n

    _run(status, "prices:TAIEX", taiex)


def fetch_daily_flows(status: dict, name: str, day_fn, utc_offset: int, max_days: int) -> None:
    """Incremental one-request-per-day fetch, saved every 20 days so an interrupted backfill keeps its progress."""

    def go():
        end = datetime.now(timezone(timedelta(hours=utc_offset))).date()  # local exchange date
        total = 0
        while total < max_days:
            have = store.read(name)
            start = (have["date"].max().date() + timedelta(days=1)) if not have.empty else BACKFILL_START
            if start > end:
                break
            chunk = market.daily_range(day_fn, start, end, min(20, max_days - total))
            if chunk.empty:
                break
            total += store.upsert(name, chunk, ["date"])
            print(f"       {name}: through {chunk['date'].max():%Y-%m-%d}", flush=True)
        return total

    _run(status, name, go)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="prices,funds,cftc,fred,twse,kospi")
    ap.add_argument("--backfill-max", type=int, default=60, help="max days per run for TWSE (2.5s each) and KOSPI (1s each)")
    args = ap.parse_args()
    only = set(args.only.split(","))

    status = _load_status()
    if "prices" in only:
        fetch_prices(status)
        fetch_asia_indices(status)
    if "funds" in only:
        fetch_funds(status)
    if "cftc" in only:
        fetch_cftc(status)
    if "fred" in only:
        fetch_fred(status)
    if "twse" in only:
        fetch_daily_flows(status, "twse", market.twse_day, 8, args.backfill_max)
    if "kospi" in only:
        fetch_daily_flows(status, "kospi", market.kospi_day, 9, args.backfill_max)

    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, indent=1, sort_keys=True))
    failed = [k for k, v in status.items() if not v.get("ok")]
    print(f"done; {len(failed)} failing: {failed}")


if __name__ == "__main__":
    main()
