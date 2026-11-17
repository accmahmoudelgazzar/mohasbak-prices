# mohasbak-prices — daily prices radar for «محاسبك» + «دليل كل حاجة»

Twice a day (09:00 + 16:00 Cairo) this fetches gold (iSagha), FX (er-api),
world ounce (XAUS) and crypto (CoinGecko), validates everything, and updates
TWO Blogger pages from ONE snapshot via Blogger API v3 `pages.update`:
«أسعار اليوم في مصر» (محاسبك) and «أسعار اليوم في القاهرة» (الدليل).
On ANY validation failure it publishes NOTHING to either page and the run
fails visibly.

Cost: $0 — GitHub Actions free for public repos; Blogger API free; all sources free.

## Layout (do not rearrange — the script resolves paths from the repo root)

- `radar/fetch_prices.py` — the radar script (stdlib only, no pip installs)
- `config.yaml` — REPO ROOT: manual fields (CBE buy/sell, bank rates, food,
  EGX close, Obour wholesale) + blog/page ids + validation thresholds.
  NOTE: only the `manual:` section is live — the `blog:` and `guide:` blocks
  are inert records; the live ids come from the repo secrets.
- `data/prices-YYYY-MM-DD.json` — daily snapshot archive (one file feeds both pages)
- `pages/daily-prices-page.html` — the rendered محاسبك page HTML (updated each run)
- `pages/guide-daily-prices-page.html` — the rendered Guide page HTML (updated each run)
- `.github/workflows/prices.yml` — GitHub Actions schedule (07:00 + 14:00 UTC
  = 09:00 + 16:00 Cairo in winter; 06:00 + 13:00 UTC in summer)

## Required repo secrets (Settings → Secrets → Actions)

- `BLOGGER_CLIENT_ID`, `BLOGGER_CLIENT_SECRET`, `BLOGGER_REFRESH_TOKEN`
  (Google Cloud OAuth 2.0, Desktop app, scope `https://www.googleapis.com/auth/blogger`)
  — shared by both pages: the same Google account owns both blogs, and the
  Blogger OAuth scope is account-wide, not per-blog.
- `BLOG_ID` = 1302262100063666035 (محاسبك)
- `PAGE_ID` = the Blogger page id of «أسعار اليوم في مصر»
- `GUIDE_BLOG_ID` = 3576572750368597138 (دليل كل حاجة)
- `GUIDE_PAGE_ID` = the Blogger page id of «أسعار اليوم في القاهرة»
- `COINGECKO_DEMO_KEY` (free Demo plan key; keyless works but the key is the
  production-correct route) — used by the محاسبك page only; the Guide page
  has no crypto section and its live strip fetches er-api + XAUS only.

## Manual routine

CBE buy/sell, bank rates, food prices, EGX close and Obour wholesale are
MANUAL fields in `config.yaml` (CBE blocks bots; no official gold-division
feed; the Obour official site URL is still unconfirmed, so Obour has no
auto-scraper yet). Update them when they change; fields not updated keep
their last good value and their date is not advanced. NEVER publish
parallel-market rates (Law 194/2020 Art. 233: 3–10 years prison + 1–5M EGP fine).

Obour routine (21 fields): `obour_date` (YYYY-MM-DD) + 10 items × `_lo`/`_hi`
(tomato, potato, white_onion, red_onion, zucchini, baladi_cucumber,
romaine_pepper, baladi_orange, baladi_lemon, tilapia) — wholesale EGP/kg,
from the market's official site as carried daily by Masrawy. Retail runs
~5–10 EGP/kg higher; the Guide page labels the section «أسعار جملة».

## Two-target setup

One fetch → one snapshot → two renders → two PUTs. The Guide template
(`pages/guide-daily-prices-page.html`) is ordered Obour → transport (static
tables: metro / monorail / CTA) → gold → FX → fuel → food, with the EGX and
crypto sections removed and the live strip reduced to er-api + XAUS. The
script renders each page from its own cell list (`render_html(snapshot,
template_path, page_kind)`). Transport and fuel are static values hardcoded
in the templates — they change only by editing the template + committing
(e.g. the next fuel-committee decision, expected December 2026).

## Safety

- Sanity ranges on every value; gold move < 5%, USD move < 2%, Obour move
  < 40% vs previous snapshot; iSagha vs XAUS theoretical within 3% (fresh
  iSagha fetch only).
- Friday/Saturday: morning snapshot only (the 16:00 run is skipped).
- iSagha parser anchors on the price table (carat rows in sequence) and
  validates every pair — a news headline mentioning a carat must never be
  read as a price (fixed 2026-11-12 after a headline broke the first version).
