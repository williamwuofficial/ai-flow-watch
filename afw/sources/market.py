"""Prices (Cboe), futures positioning (CFTC), macro (FRED), Taiwan investor flows (TWSE)."""
import io
from datetime import date, timedelta

import pandas as pd

from afw import http

START = "2015-01-01"


def cboe(ticker: str) -> pd.DataFrame:
    j = http.get(f"https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/{ticker}.json").json()
    df = pd.DataFrame(j["data"])
    df = df[df["date"] >= START]
    return pd.DataFrame(
        {"date": pd.to_datetime(df["date"]), "ticker": ticker.lstrip("_"), "close": pd.to_numeric(df["close"])}
    )


def nasdaq_recent(ticker: str, days: int = 30) -> pd.DataFrame:
    """Recent closes from Nasdaq, which is ~1 day fresher than Cboe's historical file."""
    end = date.today()
    for assetclass in ("etf", "stocks"):
        j = http.get(
            f"https://api.nasdaq.com/api/quote/{ticker}/historical",
            params={"assetclass": assetclass, "fromdate": (end - timedelta(days=days)).isoformat(), "todate": end.isoformat(), "limit": 100},
        ).json()
        rows = ((j.get("data") or {}).get("tradesTable") or {}).get("rows")
        if rows:
            return pd.DataFrame(
                {
                    "date": pd.to_datetime([r["date"] for r in rows], format="%m/%d/%Y"),
                    "ticker": ticker,
                    "close": [float(r["close"].replace("$", "").replace(",", "")) for r in rows],
                }
            )
    raise ValueError(f"{ticker}: no Nasdaq history")


def cftc(code: str, name: str) -> pd.DataFrame:
    fields = [
        "report_date_as_yyyy_mm_dd",
        "open_interest_all",
        "asset_mgr_positions_long",
        "asset_mgr_positions_short",
        "lev_money_positions_long",
        "lev_money_positions_short",
    ]
    j = http.get(
        "https://publicreporting.cftc.gov/resource/gpe5-46if.json",
        params={
            "$select": ",".join(fields),
            "$where": f"cftc_contract_market_code='{code}' AND report_date_as_yyyy_mm_dd >= '{START}'",
            "$order": "report_date_as_yyyy_mm_dd",
            "$limit": 5000,
        },
        ua=http.PLAIN_UA,
    ).json()
    df = pd.DataFrame(j)
    out = pd.DataFrame({"date": pd.to_datetime(df.pop("report_date_as_yyyy_mm_dd")), "contract": name})
    for c in fields[1:]:
        out[c] = pd.to_numeric(df[c])
    return out


def fred(series: str) -> pd.DataFrame:
    text = http.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series, "cosd": START}, ua=http.PLAIN_UA).text
    df = pd.read_csv(io.StringIO(text))
    return pd.DataFrame(
        {"date": pd.to_datetime(df.iloc[:, 0]), "series": series, "value": pd.to_numeric(df.iloc[:, 1], errors="coerce")}
    ).dropna()


TWSE_ROWS = {
    "Foreign Investors include Mainland Area Investors(Foreign Dealers excluded)": "foreign_net",
    "Securities Investment Trust Companies": "trust_net",
    "Total": "total_net",
}


def twse_day(d: date) -> dict:
    """Net buy (TWD) by investor type for one day. Returns {'date': d} only on non-trading days."""
    j = http.get(
        "https://www.twse.com.tw/rwd/en/fund/BFI82U",
        params={"type": "day", "dayDate": d.strftime("%Y%m%d"), "response": "json"},
        delay=2.5,  # TWSE blocks clients making more than ~3 requests / 5s
    ).json()
    row = {"date": pd.Timestamp(d)}
    if j.get("stat") == "OK":
        for item, _buy, _sell, diff in j["data"]:
            if item in TWSE_ROWS:
                row[TWSE_ROWS[item]] = float(diff.replace(",", ""))
    return row


def kospi_day(d: date) -> dict:
    """KOSPI net buy (KRW) by investor type from Naver's mobile API. Values arrive in units of
    100M KRW (억원); a non-trading day comes back as all zeros, stored as a date-only row."""
    j = http.get("https://m.stock.naver.com/api/index/KOSPI/trend", params={"bizdate": d.strftime("%Y%m%d")}, delay=1.0).json()
    vals = {k: float(j[f"{k}Value"].replace(",", "")) * 1e8 for k in ("foreign", "institutional", "personal")}
    row = {"date": pd.Timestamp(d)}
    if any(vals.values()):
        row.update(foreign_net=vals["foreign"], institution_net=vals["institutional"], individual_net=vals["personal"])
    return row


def daily_range(day_fn, start: date, end: date, max_days: int) -> pd.DataFrame:
    rows, d = [], start
    while d <= end and len(rows) < max_days:
        if d.weekday() < 5:
            rows.append(day_fn(d))
        d += timedelta(days=1)
    return pd.DataFrame(rows)
