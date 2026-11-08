# AI Flow Watch — plan

A static, open-source dashboard that tracks whether **passive fund flows into the AI trade are
reversing**. US first (S&P 500 / Nasdaq-100 / semis), then the global AI supply chain
(Taiwan, Korea, Japan). Data refreshes at most once a day via GitHub Actions; the site is plain
HTML on GitHub Pages.

## Thesis being tested

Michael Green's argument (and Gabaix & Koijen's *Inelastic Markets Hypothesis*): passive
vehicles are price-insensitive and cap-weighted, so every inflow buys the largest names — today
the AI complex. If markets are inelastic, $1 of flow moves prices by several dollars, so a
reversal in flows should show up as outsized price moves in exactly those names.

The site does not assume the thesis is right. It shows the evidence for and against.

## Signals (traffic lights on the overview)

| # | Signal | Data | Reversal looks like |
|---|---|---|---|
| 1 | Broad passive flows | SPY, SPYM, IVV, QQQ, QQQM, VOO, VTI (Δshares × NAV) | 20d and 60d net flow both negative |
| 2 | AI-theme flows | XLK, SOXX, SMH, IGV, VGT | Outflows, esp. while broad flows are still positive |
| 3 | Leveraged long AI | TQQQ, QLD, USD, ROM, SOXL, TECL | Shares outstanding shrinking |
| 4 | Cap vs equal weight | SPY/RSP, QQQ/QQEW price ratios | Ratio below 50d / 200d moving average |
| 5 | AI basket vs market | Equal-weight basket of NVDA, MSFT, AVGO, AMD, TSM, GOOGL, AMZN, META vs RSP; SPY top-10 weight | Relative strength breaks trend; concentration rolls over |
| 6 | Flow price-impact | Rolling 60d regression of SPY return on broad flow (% of AUM) | Slope rising = market more fragile per dollar (informational) |
| 7 | Futures positioning | CFTC TFF: asset-manager net long in E-mini S&P + Nasdaq-100 | Net long ≥1 std below 52w median **and** down ≥3 pts over 4 weeks (roll weeks skipped); hedge-fund net shown for context |
| 8 | Taiwan foreign flows | TWSE net buy by investor group + TAIEX | 20d foreign net selling **and** TAIEX down over the same 20d (selling not absorbed); selling with the index up = weakening |
| 9 | Korea foreign flows | KOSPI net buy by investor group (Naver Finance) + KOSPI | Same rule as Taiwan |
| 10 | Yen carry stress | USD/JPY (ECB), CFTC leveraged-fund net yen futures, US 2y (FRED) − JGB 2y (MOF) | USD/JPY −5% in 20d = reversing; −3% or yen shorts in most-crowded 20% of 3y = weakening |

Each signal is green / amber / red with a one-line reason. Rules live in `afw/derive.py`.

## Data sources (verified 2026-11-08)

| Source | What | Access | History |
|---|---|---|---|
| SSGA `navhist-us-en-{t}.xlsx` | NAV + shares outstanding (SPY, SPYM, XLK) | Direct xlsx | Full, since inception |
| ProShares `ByFund/{T}-historical_nav.csv` | NAV + shares (000) (TQQQ, QLD, USD, ROM) | Direct CSV | Full |
| Direxion `holdings/{T}.csv` | Shares outstanding (SOXL, TECL) | Direct CSV | Snapshot only — collected daily |
| iShares product pages | Shares outstanding, NAV (IVV, SOXX, IGV, EWT, EWY, EWJ) | HTML scrape | Snapshot only |
| Nasdaq `api/quote/{T}/summary` | Fund market cap → implied shares (QQQ, QQQM, VOO, VTI, VGT, SMH) | Unofficial JSON | Snapshot only |
| Cboe delayed quotes `charts/historical/{T}.json` | Daily OHLC for ETFs, stocks, indices | Unofficial JSON, no key | Full |
| CFTC Socrata `gpe5-46if` | Traders in Financial Futures | Official API | Full (2006+) |
| FRED `fredgraph.csv` | 10y yield, HY spread, broad USD | Official, no key | Full |
| TWSE `rwd/en/fund/BFI82U` | Institutional investor net buy | Official, throttle ≤1 req/2s | Full (backfill script) |
| Naver `m.stock.naver.com/api/index/KOSPI/trend?bizdate=` | KOSPI net buy by foreign / institution / individual (100M KRW); "other corporations" (incl. buybacks) is the residual | Unofficial JSON, 1 req/s | Full (backfill script) |
| Naver `fchart.stock.naver.com/sise.nhn?symbol=KOSPI` | KOSPI daily closes | Unofficial XML, one request | ~800 days |
| TWSE `rwd/en/afterTrading/FMTQIK` | TAIEX daily closes, one month per request | Official | Full |
| ECB SDMX `EXR/D.JPY+USD.EUR.SP00.A` | USD/JPY (EUR/JPY ÷ EUR/USD), ~1 week fresher than FRED's DEXJPUS | Official CSV | Full |
| MOF `jgbcme_all.csv` + `jgbcme.csv` | JGB yields by maturity (2y used) | Official CSV | Full |

**Blocked / not usable (as of 2026-11-08):** Yahoo (429 from scripts), Stooq (JS challenge),
Invesco site/API (406), Vanguard (only monthly, rounded AUM), ICI (403), VanEck (cookie wall),
KRX data marketplace (login required, so `pykrx` no longer works), Naver desktop investor pages (410).
QQQ and VOO flows therefore come from the Nasdaq market-cap snapshot — noisier, flagged as
"estimated" on the site.

**Later:** JPX weekly
investor flows, FINRA margin debt, SEC N-PORT monthly holdings to validate snapshot estimates.

## Architecture

```
ai-flow-watch/
├─ config/universe.yml     # every ticker, its source and group (broad / ai_theme / leveraged / intl)
├─ afw/
│  ├─ http.py              # shared session: UA, retries, timeouts
│  ├─ store.py             # CSV upsert keyed on (date, ticker)
│  ├─ sources/*.py         # one module per source; each returns a tidy DataFrame
│  ├─ fetch.py             # runs every source, isolates failures, writes data/status.json
│  └─ derive.py            # flows, rolling sums, ratios, regression, signal states → site JSON
├─ site/                   # index.html + app.js + style.css, Observable Plot from CDN, no build
├─ data/                   # (data branch) raw/*.csv + status.json; derived/*.json
└─ .github/workflows/daily.yml
```

* **Schedule:** 12:00 UTC Tue–Sat (issuers publish T-1 overnight) + manual dispatch.
* **Data branch:** raw CSVs are committed to a `data` branch so `main` history stays clean.
  Snapshot-only sources accumulate history there day by day.
* **Deploy:** the workflow assembles `site/` + `data/derived/` and deploys with
  `actions/deploy-pages`.
* **Failure isolation:** each source runs in its own try/except; `status.json` records last
  success per source and the site shows a staleness badge.
* **Flows:** `flow_t = (shares_t − shares_{t−1}) × NAV_t`. Shown as 5/20/60-day sums, in USD
  and as % of group AUM. Single-day spikes around index rebalances (heartbeat trades) are
  smoothed by the rolling sums, not removed.

## Caveats (shown on the methodology section)

* Foreign selling in Taiwan/Korea is always matched by domestic buying (flows net to zero), so the
  signal is the price at which it was absorbed, not the size of the selling. In 2024–26 foreigners
  sold KOSPI almost continuously while it rose from ~2,700 to ~9,000 and back to ~5,000.

* Creations/redemptions ≠ end-investor demand day-to-day (AP inventory, heartbeat trades).
* 401(k)/target-date money in mutual funds and CITs is invisible at daily frequency.
* Flow ↔ return causality runs both ways; signal 6 is correlation only.
* Unofficial endpoints can change without notice; snapshot estimates are flagged.

## Phases

1. **Done in this pass:** fetchers for all sources above, history backfill where available,
   derive + 10 signals, static site, daily workflow.
2. Add Japan (JPX), FINRA margin, per-stock TWSE foreign flows for TSMC.
3. Validate snapshot estimates against SEC N-PORT; add "data quality" page.
4. Historical drawdown comparison (2020, 2022, 2025) overlaid on the current episode.


## Signal 11: how passive is the market (context)

Not a traffic light. Three tiers by confidence: **high** = US ETF equity holdings ÷ market value of corporate equities (Fed Z.1 via FRED: BOGZ1LM563064100Q / BOGZ1LM893064105Q), a floor; **medium** = (ETF + mutual fund equity holdings) × ICI index share of domestic-equity fund assets (hand-updated in `config/universe.yml`, ICI blocks scripts); **low** = medium × Chinco & Sammon's 2020 ratio of total passive ownership to index-fund holdings (37.8% / 15%). Quarterly, ~10-week lag.
