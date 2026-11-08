"""Turn data/raw/*.csv into data/derived/dashboard.json for the static site.

Usage: python -m afw.derive
"""
import json
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from afw import store
from afw.fetch import CONFIG, STATUS_PATH

SINCE = "2019-01-01"
GROUPS = {
    "broad": "Broad passive (S&P 500 / Nasdaq-100)",
    "ai_theme": "AI theme (tech, semis, software)",
    "leveraged": "Leveraged long tech / semis",
    "intl": "International AI supply chain (US-listed)",
}


# ---------- helpers ----------

# Signal 7 thresholds, calibrated on 2015–2026 data: a 4-week fall of 3+ pts is roughly the
# bottom 20% of moves; 1 std below the 52-week median is roughly the bottom 15–20% of levels.
CFTC_Z = -1.0
CFTC_DROP = -3.0


def third_friday(y: int, m: int) -> pd.Timestamp:
    d = pd.Timestamp(y, m, 15)
    return d + pd.Timedelta(days=(4 - d.weekday()) % 7)


def is_roll_week(d: pd.Timestamp) -> bool:
    """CFTC report dates from 14 days before to 3 days after a quarterly equity-index expiry."""
    if d.month not in (3, 6, 9, 12) and not (d.month in (4, 7, 10, 1) and d.day <= 3):
        return False
    for y, m in ((d.year, d.month), (d.year - (d.month == 1), 12 if d.month == 1 else d.month - 1)):
        if m in (3, 6, 9, 12) and -3 <= (third_friday(y, m) - d).days <= 14:
            return True
    return False

def _clean(v):
    if isinstance(v, (float, np.floating)):
        return None if math.isnan(v) or math.isinf(v) else round(float(v), 6)
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


def columns(df: pd.DataFrame, cols: list[str]) -> dict:
    """DataFrame indexed by date → {'date': [...], col: [...]} with NaN → null."""
    df = df.loc[df.index >= SINCE, cols]
    out = {"date": [d.strftime("%Y-%m-%d") for d in df.index]}
    for c in cols:
        out[c] = [_clean(v) for v in df[c].to_numpy()]
    return out


def signal(sid, name, state, value, detail, chart):
    return {"id": sid, "name": name, "state": state, "value": value, "detail": detail, "chart": chart}


def flow_state(s20: float, s60: float) -> str:
    if any(map(lambda x: x is None or (isinstance(x, float) and math.isnan(x)), (s20, s60))):
        return "grey"
    if s20 >= 0:
        return "green"
    return "red" if s60 < 0 else "amber"


def trend_state(x: pd.Series) -> tuple[str, float, float, float]:
    x = x.dropna()
    if len(x) < 200:
        return "grey", np.nan, np.nan, np.nan
    last, ma50, ma200 = x.iloc[-1], x.tail(50).mean(), x.tail(200).mean()
    below = int(last < ma50) + int(last < ma200)
    return ["green", "amber", "red"][below], last, ma50, ma200


def ordinal(n: float) -> str:
    n = int(round(n))
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def pct(v, digits=2):
    return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:+.{digits}f}%"


def usd(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    a = abs(v)
    s = f"{a / 1e9:.1f}B" if a >= 1e9 else f"{a / 1e6:.0f}M"
    return ("−$" if v < 0 else "+$") + s


# ---------- flows ----------

def fund_flows(shares: pd.DataFrame, closes: pd.DataFrame) -> pd.DataFrame:
    """Per-fund daily flow = Δshares × NAV (NAV falls back to market close)."""
    fund_cfg = {f["ticker"]: f for f in CONFIG["funds"]}
    shares = shares[shares["ticker"].isin(fund_cfg)].copy()
    px = closes.stack().rename("close").reset_index().rename(columns={"level_1": "ticker"})
    shares = shares.merge(px, on=["date", "ticker"], how="left")
    shares["nav"] = shares["nav"].fillna(shares["close"])
    shares = shares.sort_values(["ticker", "date"])
    g = shares.groupby("ticker")
    shares["flow"] = g["shares"].diff() * shares["nav"]
    # Splits / reverse splits: shares jump while NAV moves inversely, so AUM is ~unchanged.
    # Treat those days as zero flow instead of a fake creation/redemption.
    r_sh = shares["shares"] / g["shares"].shift()
    r_nav = shares["nav"] / g["nav"].shift()
    split = ((r_sh - 1).abs() > 0.2) & ((r_sh * r_nav - 1).abs() < 0.15)
    shares.loc[split, "flow"] = 0.0
    shares["aum_prev"] = g["shares"].shift() * g["nav"].shift()
    shares["aum"] = shares["shares"] * shares["nav"]
    shares = pd.concat([_net_heartbeats(f) for _, f in shares.groupby("ticker")])
    shares["group"] = shares["ticker"].map(lambda t: fund_cfg[t]["group"])
    return shares


def _net_heartbeats(f: pd.DataFrame, big: float = 0.02, window: int = 5) -> pd.DataFrame:
    """Zero out 'heartbeat' trades: a creation (or redemption) above `big` of AUM that is reversed
    by at least 80% within `window` reporting days. These are tax-driven in-kind swaps around index
    rebalances, not investor demand, and they leave ugly artifacts in rolling sums."""
    f = f.copy()
    flow = f["flow"].to_numpy(copy=True)
    aum = f["aum_prev"].to_numpy()
    for i in range(len(flow)):
        if not (aum[i] > 0) or abs(flow[i]) < big * aum[i]:
            continue
        for j in range(i + 1, min(i + 1 + window, len(flow))):
            if flow[j] * flow[i] < 0 and abs(flow[j]) >= 0.8 * abs(flow[i]):
                # keep any residual so the cumulative total is unchanged
                flow[j] += flow[i]
                flow[i] = 0.0
                break
    f["flow"] = flow
    return f


def group_flows(ff: pd.DataFrame, group: str) -> pd.DataFrame:
    g = ff[(ff["group"] == group) & ff["flow"].notna()]
    if g.empty:
        return pd.DataFrame(columns=["flow", "aum_prev", "pct", "s5", "s20", "s60", "cum"])
    # Stop at the last date every fund with real history has reported, so a lagging issuer
    # file (SSGA publishes a day after ProShares) doesn't make the latest day look like an outflow.
    counts = g.groupby("ticker")["date"].agg(["count", "max"])
    established = counts[counts["count"] >= 20]
    end = established["max"].min() if not established.empty else counts["max"].max()
    d = g[g["date"] <= end].groupby("date")[["flow", "aum_prev"]].sum()
    d["pct"] = d["flow"] / d["aum_prev"] * 100
    for n in (5, 20, 60):
        d[f"s{n}"] = d["pct"].rolling(n, min_periods=n).sum()
    d["flow20"] = d["flow"].rolling(20, min_periods=20).sum()
    d["cum"] = d.loc[d.index >= SINCE, "flow"].cumsum()
    return d


def flow_signal(sid, group, d: pd.DataFrame, coverage: list[dict]):
    tickers = [c["ticker"] for c in coverage if c["group"] == group and c["days"] >= 20]
    if d.empty or d["s60"].dropna().empty:
        return signal(sid, GROUPS[group], "grey", "collecting", "Not enough history yet.", f"flows_{group}")
    last = d.iloc[-1]
    st = flow_state(last["s20"], last["s60"])
    detail = (
        f"20d net flow {usd(last['flow20'])} ({pct(last['s20'])} of AUM); 60d {pct(last['s60'])}. "
        f"Based on {', '.join(tickers)} through {d.index[-1]:%d %b}."
    )
    return signal(sid, GROUPS[group], st, pct(last["s20"]), detail, f"flows_{group}")


# Investor groups per market. Naver (Korea) omits "other corporations" (incl. company buybacks) and
# TWSE omits retail, so each market gets a residual group: every trade has a buyer and a seller,
# so all groups must net to zero.
GROUPS_ASIA = {
    "kospi": lambda d: {
        "foreign": d["foreign_net"],
        "individuals": d["individual_net"],
        "institutions": d["institution_net"],
        "other": -(d["foreign_net"] + d["individual_net"] + d["institution_net"]),
    },
    "twse": lambda d: {
        "foreign": d["foreign_net"],
        "trusts": d["trust_net"],
        "dealers": d["total_net"] - d["foreign_net"] - d["trust_net"],
        "other": -d["total_net"],
    },
}


GROUP_LABELS = {
    "kospi": {"individuals": "individuals", "institutions": "institutions", "other": "other corporations (incl. buybacks)"},
    "twse": {"trusts": "investment trusts", "dealers": "dealers", "other": "retail and others"},
}


def foreign_flow_signal(closes, series, sid, name, table, index, scale, unit, digits, note):
    """Foreign selling only counts as a reversal when the index fell over the same window.

    Domestic buyers always absorb foreign selling in aggregate (net flows sum to zero), so the
    informative part is the price it took: rising = domestic demand soaked it up, falling = buyers
    only stepped in lower.
    """
    df = store.read(table)
    if df.empty or "foreign_net" not in df:
        return signal(sid, name, "grey", "collecting", "No data yet.", table)
    df = df.dropna(subset=["foreign_net"]).set_index("date").sort_index()
    g = pd.DataFrame({k: v / scale for k, v in GROUPS_ASIA[table](df).items()})
    g["close"] = closes[index].reindex(g.index) if index in closes else np.nan
    g["f20"] = g["foreign"].rolling(20, min_periods=20).sum()
    g["r20"] = (g["close"] / g["close"].shift(20) - 1) * 100
    series[table] = columns(g, list(g.columns))
    last = g.iloc[-1]
    if math.isnan(last["f20"]) or math.isnan(last["r20"]):
        return signal(sid, name, "grey", "collecting", f"Backfilling: {len(g)} days so far.", table)
    v = lambda x: f"{x:+,.{digits}f}{unit.split()[0]} {unit.split()[1]}"  # e.g. "+1.2tn KRW"
    domestic = g[[c for c in GROUPS_ASIA[table](df) if c != "foreign"]].tail(20).sum()
    top = GROUP_LABELS[table][domestic.idxmax()]
    if last["f20"] >= 0:
        st, how = "green", "Foreigners are net buyers"
    elif last["r20"] >= 0:
        st, how = "amber", f"Foreign selling absorbed with the index still up, mostly by {top} ({v(domestic.max())})"
    else:
        st, how = "red", f"Foreign selling not absorbed: the index fell while {top} bought most ({v(domestic.max())})"
    return signal(
        sid, name, st, v(last["f20"]),
        f"{how}. 20d foreign net {v(last['f20'])}; {index} {last['r20']:+.1f}% over the same 20 days, "
        f"through {g.index[-1]:%d %b}. {note}",
        table,
    )


CARRY_UNWIND = -5.0   # USD/JPY 20-day % change that marks an unwind in progress
CARRY_WARN = -3.0
CARRY_CROWDED = 20    # leveraged-fund net yen position in its most-short 20% of the last 3 years


def carry_signal(cf_all: pd.DataFrame, series: dict) -> dict:
    """Yen carry stress. Backtest 2016–2026: red on ~3% of days (2016, Jan 2019, Mar 2020,
    late 2022, Aug 2024, Apr 2025), amber ~23%."""
    fr = store.read("fred").pivot(index="date", columns="series", values="value").sort_index()
    if "USDJPY" not in fr:
        return signal(10, "Yen carry trade stress", "grey", "collecting", "No USD/JPY data yet.", "carry")
    fx = fr["USDJPY"].dropna()
    y = cf_all[cf_all["contract"] == "Japanese yen"].set_index("date").sort_index()
    lev = (y["lev_money_positions_long"] - y["lev_money_positions_short"]) / y["open_interest_all"] * 100
    pct = lev.rolling(156, min_periods=52).rank(pct=True) * 100
    gap = (fr["DGS2"] - fr["JGB2Y"]).dropna() if {"DGS2", "JGB2Y"} <= set(fr) else pd.Series(dtype=float)

    d = pd.DataFrame({"usdjpy": fx})
    d["r20"] = (d["usdjpy"] / d["usdjpy"].shift(20) - 1) * 100
    d["lev"] = lev.reindex(d.index, method="ffill")
    d["lev_pct"] = pct.reindex(d.index, method="ffill")
    d["gap"] = gap.reindex(d.index, method="ffill")
    series["carry"] = columns(d, ["usdjpy", "r20", "lev", "gap"])

    last = d.iloc[-1]
    crowded = last["lev_pct"] <= CARRY_CROWDED
    if last["r20"] <= CARRY_UNWIND:
        st, how = "red", "Yen rallying fast: an unwind looks under way"
    elif crowded or last["r20"] <= CARRY_WARN:
        st, how = "amber", ("Hedge funds are crowded short the yen" if crowded else "Yen strengthening") + ": fuel for an unwind"
    else:
        st, how = "green", "Yen shorts not crowded and the yen is steady"
    gap_txt = f" US–Japan 2-year gap {last['gap']:.2f} pts." if not math.isnan(last["gap"]) else ""
    return signal(
        10, "Yen carry trade stress", st, f"{last['r20']:+.1f}% USD/JPY",
        f"{how}. USD/JPY {last['usdjpy']:.1f}, {last['r20']:+.1f}% over 20 days (reversing at ≤{CARRY_UNWIND:.0f}%). "
        f"Hedge funds net {last['lev']:+.1f}% of yen futures OI ({ordinal(last['lev_pct'])} percentile, 3 years; "
        f"crowded at ≤{CARRY_CROWDED}th).{gap_txt} FX through {d.index[-1]:%d %b}.",
        "carry",
    )


def passive_share_signal(series: dict) -> dict:
    """Context, not a traffic light: share of the US stock market held passively, in three tiers
    from measured (a floor) to a broad academic estimate."""
    cfg = CONFIG.get("passive_share", {})
    z1 = store.read("z1")
    ids = cfg.get("z1", {})
    name = "How passive is the market"
    if z1.empty or not set(ids.values()) <= set(z1["series"]):
        return signal(11, name, "grey", "collecting", "No Fed Z.1 data yet.", "passive")
    p = z1.pivot(index="date", columns="series", values="value").sort_index().rename(columns={v: k for k, v in ids.items()})
    p = p.dropna(subset=["total", "etf", "mutual_funds"])
    p["etf_pct"] = p["etf"] / p["total"] * 100
    p["funds_pct"] = (p["etf"] + p["mutual_funds"]) / p["total"] * 100
    series["passive"] = columns(p, ["etf_pct", "funds_pct"])

    last, q = p.iloc[-1], p.index[-1]
    qtr = f"Q{(q.month - 1) // 3 + 1} {q.year}"
    ici, cs = cfg["ici_index_share"], cfg["chinco_sammon"]
    high = last["etf_pct"]
    mid = last["funds_pct"] * ici["value"] / 100
    multiple = cs["passive"] / cs["index_funds"]
    low = mid * multiple
    yr = p["etf_pct"].iloc[-5] if len(p) > 4 else math.nan
    yr_txt = f", {high - yr:+.1f} pts in a year" if not math.isnan(yr) else ""
    out = signal(
        11, name, "info", f"{high:.0f}% to {low:.0f}%",
        f"Share of US stocks held by passive investors, {qtr} (Fed Z.1, quarterly). Context only, not part of the reversal count.",
        "passive",
    )
    out["tiers"] = [
        {"confidence": "High", "label": "ETFs alone (a floor)", "value": round(high, 1),
         "note": f"Measured: ETF stock holdings ÷ market value{yr_txt}. Includes a small active-ETF slice."},
        {"confidence": "Medium", "label": "All index funds", "value": round(mid, 1),
         "note": f"ETF + mutual fund stock holdings ({last['funds_pct']:.0f}% of the market) × ICI index share {ici['value']:.1f}% ({ici['as_of']})."},
        {"confidence": "Low", "label": "All passive, incl. non-fund indexers", "value": round(low, 1),
         "note": f"Index funds × {multiple:.1f}, from Chinco & Sammon's {cs['year']} estimate ({cs['passive']:.1f}% vs {cs['index_funds']:.0f}%): adds pensions, SMAs and benchmark-hugging mandates."},
    ]
    return out


# ---------- main ----------

def build() -> dict:
    shares = store.read("fund_shares")
    prices = store.read("prices")
    closes = prices.pivot(index="date", columns="ticker", values="close").sort_index().ffill(limit=3)
    ff = fund_flows(shares, closes)

    coverage = []
    for t, g in ff.groupby("ticker"):
        f = next(x for x in CONFIG["funds"] if x["ticker"] == t)
        coverage.append(
            {
                "ticker": t,
                "group": f["group"],
                "source": f["source"],
                "estimated": bool(g["estimated"].any()),
                "first": g["date"].min().strftime("%Y-%m-%d"),
                "last": g["date"].max().strftime("%Y-%m-%d"),
                "days": int(g["flow"].notna().sum()),
                "aum": _clean(g["aum"].iloc[-1]),
            }
        )

    series, signals = {}, []

    # 1–3 flows (+ intl, shown but not a headline signal until it has history)
    for i, grp in enumerate(["broad", "ai_theme", "leveraged"], start=1):
        d = group_flows(ff, grp)
        series[f"flows_{grp}"] = columns(d, ["flow", "pct", "s20", "s60", "cum"]) if not d.empty else None
        signals.append(flow_signal(i, grp, d, coverage))
    d_intl = group_flows(ff, "intl")
    series["flows_intl"] = columns(d_intl, ["flow", "pct", "s20", "s60", "cum"]) if not d_intl.empty else None

    # per-fund cumulative flows for the drill-down chart
    per_fund = {}
    for t, g in ff[ff["flow"].notna()].groupby("ticker"):
        g = g.set_index("date")
        g = g[g.index >= SINCE]
        per_fund[t] = {"date": [x.strftime("%Y-%m-%d") for x in g.index], "cum": [_clean(v) for v in g["flow"].cumsum()]}
    series["per_fund"] = per_fund

    # 4 cap vs equal weight
    rat = pd.DataFrame({"spy_rsp": closes["SPY"] / closes["RSP"], "qqq_qqew": closes["QQQ"] / closes["QQEW"]}).dropna()
    for c in list(rat.columns):
        rat[f"{c}_ma50"] = rat[c].rolling(50).mean()
        rat[f"{c}_ma200"] = rat[c].rolling(200).mean()
    series["cap_vs_equal"] = columns(rat, list(rat.columns))
    s1, *_ = trend_state(rat["spy_rsp"])
    s2, *_ = trend_state(rat["qqq_qqew"])
    worst = max([s1, s2], key=["grey", "green", "amber", "red"].index)
    chg = (rat["spy_rsp"].iloc[-1] / rat["spy_rsp"].iloc[-21] - 1) * 100
    signals.append(
        signal(
            4, "Cap-weight vs equal-weight", worst, pct(chg, 1),
            f"SPY/RSP {s1}, QQQ/QQEW {s2} vs 50d/200d averages. SPY/RSP {pct(chg, 1)} over 20 days "
            "(falling = mega-caps lagging the average stock).",
            "cap_vs_equal",
        )
    )

    # 5 AI basket vs equal-weight market + concentration
    basket = [t for t in CONFIG["ai_basket"] if t in closes]
    rets = closes[basket].pct_change(fill_method=None).mean(axis=1, skipna=True)
    idx = (1 + rets.fillna(0)).cumprod()
    ai = pd.DataFrame({"ai_vs_rsp": idx / closes["RSP"]}).dropna()
    ai["ai_vs_rsp"] /= ai.loc[ai.index >= SINCE, "ai_vs_rsp"].iloc[0]
    ai["ma50"] = ai["ai_vs_rsp"].rolling(50).mean()
    ai["ma200"] = ai["ai_vs_rsp"].rolling(200).mean()
    series["ai_basket"] = columns(ai, ["ai_vs_rsp", "ma50", "ma200"])
    st5, *_ = trend_state(ai["ai_vs_rsp"])
    conc = store.read("concentration")
    conc_txt = ""
    if not conc.empty:
        c = conc.sort_values("date").iloc[-1]
        conc_txt = f" AI names are {c['ai_basket_weight']:.1f}% of SPY, top 10 = {c['top10_weight']:.1f}%."
        series["concentration"] = columns(conc.set_index("date"), ["top10_weight", "ai_basket_weight"])
    chg5 = (ai["ai_vs_rsp"].iloc[-1] / ai["ai_vs_rsp"].iloc[-21] - 1) * 100
    signals.append(
        signal(
            5, "AI basket vs equal-weight market", st5, pct(chg5, 1),
            f"Equal-weight {'/'.join(basket)} relative to RSP is {st5} vs its 50d/200d averages; "
            f"{pct(chg5, 1)} over 20 days.{conc_txt}",
            "ai_basket",
        )
    )

    # 6 flow price-impact (weekly, flows shifted one day for T+1 share reporting)
    broad = ff[(ff["group"] == "broad") & ff["flow"].notna()]
    hist = broad.groupby("ticker")["date"].count()
    broad = broad[broad["ticker"].isin(hist[hist > 250].index)]
    daily = broad.groupby("date")[["flow", "aum_prev"]].sum()
    fp = (daily["flow"] / daily["aum_prev"] * 100).shift(-1)
    ret = closes["SPY"].pct_change(fill_method=None) * 100
    wk = pd.DataFrame({"flow": fp, "ret": ret}).dropna().resample("W-FRI").sum(min_count=1).dropna()
    win = 26
    slope = wk["ret"].rolling(win).cov(wk["flow"]) / wk["flow"].rolling(win).var()
    corr = wk["ret"].rolling(win).corr(wk["flow"])
    imp = pd.DataFrame({"slope": slope, "corr": corr, "flow": wk["flow"], "ret": wk["ret"]})
    series["impact"] = columns(imp, ["slope", "corr", "flow", "ret"])
    sl = imp["slope"].dropna()
    pctile = (sl.rank(pct=True).iloc[-1] * 100) if len(sl) else np.nan
    st6 = "grey" if sl.empty else ("amber" if pctile >= 80 else "green")
    signals.append(
        signal(
            6, "Flow price-impact (inelasticity)", st6, f"{sl.iloc[-1]:.1f}×" if len(sl) else "n/a",
            f"26-week slope of weekly SPY return on weekly broad passive flow (% AUM): {sl.iloc[-1]:.2f}, "
            f"{ordinal(pctile)} percentile since 2015; correlation {corr.dropna().iloc[-1]:+.2f}. "
            "Amber above the 80th percentile. Correlation, not causation."
            if len(sl) else "Not enough history.",
            "impact",
        )
    )

    # 7 CFTC asset-manager positioning
    cf_all = store.read("cftc")
    equity = [c["name"] for c in CONFIG["cftc"] if c.get("use") == "equity"]
    cf = cf_all[cf_all["contract"].isin(equity)].copy()
    oi = cf["open_interest_all"]
    cf["am"] = (cf["asset_mgr_positions_long"] - cf["asset_mgr_positions_short"]) / oi * 100
    cf["lev"] = (cf["lev_money_positions_long"] - cf["lev_money_positions_short"]) / oi * 100
    piv = cf.pivot(index="date", columns="contract", values="am").sort_index()
    contracts = list(piv.columns)
    piv["combined"] = piv[contracts].mean(axis=1)
    piv["lev_combined"] = cf.pivot(index="date", columns="contract", values="lev").sort_index().mean(axis=1)
    # Quarterly rolls swell open interest and dent the percentage for a week or two; hold the last
    # clean reading through those weeks so the rule compares like with like.
    piv["roll"] = [is_roll_week(d) for d in piv.index]
    clean = piv["combined"].where(~piv["roll"]).ffill()
    piv["median52"] = clean.rolling(52).median()
    std52 = clean.rolling(52).std()
    z = (clean - piv["median52"]) / std52
    chg4 = clean.diff(4)
    last_z, last_chg, last_val = z.iloc[-1], chg4.iloc[-1], clean.iloc[-1]
    weak, falling = bool(last_z <= CFTC_Z), bool(last_chg <= CFTC_DROP)
    st7 = ["green", "amber", "red"][int(weak) + int(falling)]
    series["cftc"] = columns(piv, contracts + ["combined", "median52", "lev_combined"])
    lev_pct = piv["lev_combined"].rolling(156).rank(pct=True).iloc[-1] * 100
    roll_note = " Latest week is a contract roll, so the prior clean week is used." if piv["roll"].iloc[-1] else ""
    signals.append(
        signal(
            7, "Asset-manager futures positioning", st7, f"{last_val:.1f}% OI",
            f"Asset managers net long {last_val:.1f}% of open interest (avg of E-mini S&P & Nasdaq-100), "
            f"{last_z:+.1f} std vs 52-week median {piv['median52'].iloc[-1]:.1f}%; 4-week change {last_chg:+.1f} pts. "
            f"Reversing needs both ≤{CFTC_Z:.0f} std and ≤{CFTC_DROP:.0f} pts. "
            f"Hedge funds net {piv['lev_combined'].iloc[-1]:+.1f}% OI ({ordinal(lev_pct)} percentile, 3 years). "
            f"Week of {piv.index[-1]:%d %b}.{roll_note}",
            "cftc",
        )
    )

    # 8–9 foreign investors in the AI hardware supply chain's home markets
    signals.append(foreign_flow_signal(
        closes, series, 8, "Taiwan foreign investor flows", "twse", "TAIEX", 1e9, "bn TWD", 0,
        "TSMC is roughly 40% of the index."))
    signals.append(foreign_flow_signal(
        closes, series, 9, "Korea foreign investor flows", "kospi", "KOSPI", 1e12, "tn KRW", 1,
        "Samsung Electronics and SK Hynix are roughly a third of the index."))

    # 10 yen carry: crowding in yen shorts (fuel) and the speed of any yen rally (the unwind)
    signals.append(carry_signal(cf_all, series))

    # 11 context: how passive the market already is (not counted in the summary)
    signals.append(passive_share_signal(series))

    # context: macro
    fr = store.read("fred").pivot(index="date", columns="series", values="value").sort_index()
    series["macro"] = columns(fr, list(fr.columns))
    series["index"] = columns(closes[["SPY", "QQQ", "SMH"]].dropna(), ["SPY", "QQQ", "SMH"])

    status = json.loads(STATUS_PATH.read_text()) if STATUS_PATH.exists() else {}
    states = [s["state"] for s in signals]
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "summary": {k: states.count(k) for k in ("red", "amber", "green", "grey", "info")},
        "signals": signals,
        "coverage": coverage,
        "status": {k: {"ok": v.get("ok"), "last_success": v.get("last_success"), "error": v.get("error")} for k, v in status.items()},
        "series": series,
    }


def main() -> None:
    out = build()
    store.DERIVED.mkdir(parents=True, exist_ok=True)
    path = store.DERIVED / "dashboard.json"
    path.write_text(json.dumps(out, separators=(",", ":"), allow_nan=False))
    print(f"wrote {path} ({path.stat().st_size / 1e6:.2f} MB): {out['summary']}")
    for s in out["signals"]:
        print(f"  [{s['state']:>5}] {s['id']} {s['name']}: {s['value']} — {s['detail']}")


if __name__ == "__main__":
    main()
