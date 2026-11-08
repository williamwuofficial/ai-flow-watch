"""ETF shares-outstanding fetchers. Each returns rows of (date, ticker, shares, nav, source, estimated).

`nav` may be NaN; derive.py falls back to the Cboe close for that day.
"""
import html
import io
import re
from datetime import date

import pandas as pd

from afw import http

COLS = ["date", "ticker", "shares", "nav", "source", "estimated"]


def _num(s) -> float:
    return float(re.sub(r"[^0-9.\-]", "", str(s)))


def ssga(ticker: str, **_) -> pd.DataFrame:
    """Full NAV history incl. shares outstanding, since inception."""
    url = f"https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/navhist-us-en-{ticker.lower()}.xlsx"
    raw = pd.read_excel(io.BytesIO(http.get(url).content), header=None)
    hdr = raw.index[raw[0].astype(str).str.strip() == "Date"][0]
    df = raw.iloc[hdr + 1 :, :4]
    df.columns = ["date", "nav", "shares", "tna"]
    df = df[pd.to_datetime(df["date"], format="%d-%b-%Y", errors="coerce").notna()]
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(df["date"], format="%d-%b-%Y"),
            "ticker": ticker,
            "shares": pd.to_numeric(df["shares"], errors="coerce"),
            "nav": pd.to_numeric(df["nav"], errors="coerce"),
            "source": "ssga",
            "estimated": False,
        }
    )
    return out.dropna(subset=["shares"])[COLS]


def proshares(ticker: str, **_) -> pd.DataFrame:
    """Full NAV history; shares are reported in thousands."""
    url = f"https://accounts.profunds.com/etfdata/ByFund/{ticker}-historical_nav.csv"
    df = pd.read_csv(io.StringIO(http.get(url).text))
    return pd.DataFrame(
        {
            "date": pd.to_datetime(df["Date"], format="%m/%d/%Y"),
            "ticker": ticker,
            "shares": pd.to_numeric(df["Shares Outstanding (000)"], errors="coerce") * 1000,
            "nav": pd.to_numeric(df["NAV"], errors="coerce"),
            "source": "proshares",
            "estimated": False,
        }
    ).dropna(subset=["shares"])[COLS]


def direxion(ticker: str, **_) -> pd.DataFrame:
    """Snapshot: holdings CSV header carries shares outstanding; first holding row carries the date."""
    text = http.get(f"https://www.direxion.com/holdings/{ticker}.csv").text
    shares = _num(re.search(r"Shares Outstanding:\s*([\d,]+)", text).group(1))
    d = re.search(r'^"(\d{1,2}/\d{1,2}/\d{4})', text, re.M).group(1)
    return pd.DataFrame(
        [[pd.to_datetime(d, format="%m/%d/%Y"), ticker, shares, float("nan"), "direxion", False]], columns=COLS
    )


def ishares(ticker: str, path: str, **_) -> pd.DataFrame:
    """Snapshot: product page embeds key facts JSON (HTML-escaped)."""
    page = html.unescape(http.get(f"https://www.ishares.com/us/products/{path}").text)

    def fact(name: str) -> tuple[float, str]:
        m = re.search(rf'"{name}":\{{([^{{}}]*)\}}', page)
        v = m and re.search(r'"formattedValue":"([^"]+)"', m.group(1))
        d = m and re.search(r'"formattedAsOfDate":"([^"]+)"', m.group(1))
        if not (v and d):
            raise ValueError(f"{ticker}: '{name}' not found on iShares page")
        return _num(v.group(1)), d.group(1)

    shares, asof = fact("sharesOutstanding")
    nav, _ = fact("navAmount")
    return pd.DataFrame(
        [[pd.to_datetime(asof, format="%b %d, %Y"), ticker, shares, nav, "ishares", False]], columns=COLS
    )


def nasdaq(ticker: str, prices: pd.DataFrame, **_) -> pd.DataFrame:
    """Snapshot estimate: fund market cap / previous close.

    The payload has no as-of date, so the row is dated by matching the close against stored
    prices. Market cap is cross-checked against the separately reported AUM; for some issuers
    (Vanguard) market cap is nonsense and AUM is a stale monthly figure, so those are rejected.
    """
    j = http.get(f"https://api.nasdaq.com/api/quote/{ticker}/summary", params={"assetclass": "etf"}).json()
    s = j["data"]["summaryData"]
    mcap, close = _num(s["MarketCap"]["value"]), _num(s["PreviousClose"]["value"])
    aum = _num(s["AUM"]["value"]) * 1000  # reported in thousands
    if abs(mcap / aum - 1) > 0.10:
        raise ValueError(f"{ticker}: market cap {mcap:.3g} disagrees with AUM {aum:.3g}; unusable")
    recent = prices[prices["ticker"] == ticker].sort_values("date").tail(10)
    match = recent[(recent["close"] - close).abs() < 0.006]
    if match.empty:
        raise ValueError(f"{ticker}: previous close {close} not found in recent prices")
    asof = match["date"].iloc[-1]
    return pd.DataFrame([[asof, ticker, mcap / close, close, "nasdaq", True]], columns=COLS)


def spy_concentration(ai_basket: list[str]) -> pd.DataFrame:
    """Snapshot of S&P 500 concentration from SPY's daily holdings file (stand-in for licensed index weights)."""
    url = "https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx"
    raw = pd.read_excel(io.BytesIO(http.get(url).content), header=None)
    asof = pd.to_datetime(str(raw.iloc[2, 1]).replace("As of ", ""), format="%d-%b-%Y")
    hdr = raw.index[raw[0].astype(str).str.strip() == "Name"][0]
    h = raw.iloc[hdr + 1 :, [1, 4]].dropna()
    h.columns = ["ticker", "weight"]
    h["weight"] = pd.to_numeric(h["weight"], errors="coerce")
    h = h.dropna().sort_values("weight", ascending=False)
    # SPY lists GOOGL and GOOG separately; TSM is not an S&P member.
    ai = h[h["ticker"].isin(ai_basket + ["GOOG"])]["weight"].sum()
    return pd.DataFrame(
        [{"date": asof, "top10_weight": h["weight"].head(10).sum(), "ai_basket_weight": ai, "top10": " ".join(h["ticker"].head(10))}]
    )


FETCHERS = {"ssga": ssga, "proshares": proshares, "direxion": direxion, "ishares": ishares, "nasdaq": nasdaq}
