# AI Flow Watch

Static dashboard tracking whether **passive fund flows into the AI trade are reversing**:
ETF creations/redemptions, cap- vs equal-weight, AI-basket relative strength, asset-manager
futures positioning and Taiwan / Korea foreign flows, condensed into nine traffic-light signals.
Refreshed once a day by GitHub Actions, served by GitHub Pages. Free public data only.

See [PLAN.md](PLAN.md) for the thesis, signal rules, data sources and roadmap.

## Run locally

```bash
uv venv && uv pip install -r requirements.txt   # or python -m venv .venv && pip install ...
.venv/bin/python -m afw.fetch            # all sources → data/raw/*.csv (~2 min)
.venv/bin/python -m afw.derive           # → data/derived/dashboard.json
.venv/bin/python -m afw.build_site       # → _site/
python3 -m http.server 8765 -d _site
```

`afw.fetch --only prices,funds` runs a subset; `--backfill-max 800` backfills Taiwan and Korea history.

## Deploy

1. Push `main`. Data is kept on a separate `data` branch, which the workflow creates on its first run.
2. Settings → Pages → Source: **GitHub Actions**.
3. Actions → *daily* → *Run workflow* (set `backfill_max` to `800` on the first run to backfill Taiwan and Korea).

## Layout

| Path | What |
|---|---|
| `config/universe.yml` | Funds (source + group), price tickers, AI basket, CFTC contracts, FRED series |
| `afw/sources/` | One fetcher per source |
| `afw/fetch.py` | Runs fetchers with failure isolation, writes `data/status.json` |
| `afw/derive.py` | Flows, rolling sums, ratios, regression and signal rules |
| `site/` | Static page (Observable Plot via CDN, no build step) |

Not investment advice.
