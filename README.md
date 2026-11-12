# mohasbak-prices — daily prices radar for «محاسبك»

Twice a day (09:00 + 15:30 Cairo) this fetches gold (iSagha), FX (er-api),
world ounce (XAUS) and crypto (CoinGecko), validates everything, and updates
the Blogger page «أسعار اليوم في مصر» via Blogger API v3 `pages.update`.
On ANY validation failure it publishes NOTHING and the run fails visibly.

Cost: $0 — GitHub Actions free for public repos; Blogger API free; all sources free.

## Layout (do not rearrange — the script resolves paths from the repo root)

- `radar/fetch_prices.py` — the radar script (stdlib only, no pip installs)
- `config.yaml` — REPO ROOT: manual fields (CBE buy/sell, bank rates, food,
  EGX close) + blog/page ids + validation thresholds
- `data/prices-YYYY-MM-DD.json` — daily snapshot archive
- `pages/daily-prices-page.html` — the rendered page HTML (updated each run)
- `.github/workflows/prices.yml` — GitHub Actions schedule (07:00 + 13:30 UTC
  = 09:00 + 15:30 Cairo in winter; 06:00 + 12:30 UTC in summer)

## Required repo secrets (Settings → Secrets → Actions)

- `BLOGGER_CLIENT_ID`, `BLOGGER_CLIENT_SECRET`, `BLOGGER_REFRESH_TOKEN`
  (Google Cloud OAuth 2.0, Desktop app, scope `https://www.googleapis.com/auth/blogger`)
- `BLOG_ID` = 1302262100063666035
- `PAGE_ID` = the Blogger page id of «أسعار اليوم في مصر»
- `COINGECKO_DEMO_KEY` (free Demo plan key; keyless works but the key is the
  production-correct route)

## Manual routine

CBE buy/sell, bank rates, food prices and EGX close are MANUAL fields in
`config.yaml` (CBE blocks bots; no official gold-division feed). Update them
when they change; fields not updated keep their last good value and their date
is not advanced. NEVER publish parallel-market rates (Law 194/2020 Art. 233:
3–10 years prison + 1–5M EGP fine).

## Safety

- Sanity ranges on every value; gold move < 5%, USD move < 2% vs previous
  snapshot; iSagha vs XAUS theoretical within 3% (fresh iSagha fetch only).
- Friday/Saturday: morning snapshot only (the 15:30 run is skipped).
- iSagha parser anchors on the price table (carat rows in sequence) and
  validates every pair — a news headline mentioning a carat must never be
  read as a price (fixed 2026-11-12 after a headline broke the first version).
