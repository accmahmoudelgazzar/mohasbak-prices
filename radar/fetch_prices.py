#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Daily prices radar for the «محاسبك» Blogger page (mohasbak.blogspot.com).
Free, stdlib-only Python. Run by GitHub Actions twice daily (09:00 + 16:00 Cairo).

What it does:
  1. Fetches XAUS (gold/silver spot, EGP/gram), open.er-api (USD mid-market),
     iSagha (Egypt gold/silver buy/sell, HTML), CBE (best effort, WAF may block),
     CoinGecko (BTC/ETH, Demo key if present else keyless).
  2. Reads manual fields (CBE buy/sell, bank rates, food, EGX close, fuel)
     from config.yaml — the operator updates them; if absent, last good values
     are kept and their date is NOT advanced.
  3. Validates everything (sanity ranges, gold change < 5%, USD change < 2%,
     iSagha vs theoretical (XAUS x USD) within 3%). On validation failure it
     exits non-zero and publishes NOTHING.
  4. Renders the Blogger page HTML (id-based replacement in pages/daily-prices-page.html),
     archives data/prices-YYYY-MM-DD.json, and (unless --dry-run) updates the
     Blogger page via Blogger API v3 pages.update.

Usage:
  python3 fetch_prices.py --dry-run        # fetch + validate + write JSON only
  python3 fetch_prices.py --publish        # also update the Blogger page
  Add --force to bypass the Friday/Saturday afternoon skip (manual runs).
Env (publish mode): BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET,
  BLOGGER_REFRESH_TOKEN, BLOG_ID, PAGE_ID, COINGECKO_DEMO_KEY (optional).
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

CAIRO = timezone(timedelta(hours=2))  # EET winter; Egypt DST (UTC+3) Apr-Oct — see README
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(BASE_DIR)
DATA_DIR = os.path.join(REPO_DIR, "data")
PAGES_DIR = os.path.join(REPO_DIR, "pages")
CONFIG_PATH = os.path.join(REPO_DIR, "config.yaml")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
      "Accept": "application/json, text/html, */*",
      "Accept-Language": "en-US,en;q=0.9,ar;q=0.8"}

# Sanity ranges (EGP) — values outside these are never published.
RANGES = {
    "g21_buy": (4000, 9000), "g21_sell": (4000, 9000),
    "g24_buy": (4500, 10500), "g24_sell": (4500, 10500),
    "usd_mid": (40, 70), "usd_cbe_buy": (40, 70), "usd_cbe_sell": (40, 70),
    "btc_usd": (10000, 300000), "eth_usd": (100, 20000),
    "egx30": (10000, 100000),
}

MAX_GOLD_MOVE_PCT = 5.0     # vs previous snapshot
MAX_USD_MOVE_PCT = 2.0      # vs previous snapshot
MAX_THEORY_DEVIATION_PCT = 3.0  # iSagha vs XAUS theoretical


def log(msg):
    print(f"[{datetime.now(CAIRO).strftime('%H:%M:%S')}] {msg}", flush=True)


def get_json(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_text(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        for enc in ("utf-8", "windows-1256", "iso-8859-6"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="ignore")


def fetch_xaus():
    d = get_json("https://xaus.com/api/v1/spot?currency=EGP&unit=gram")
    return {
        "gold_usd_oz": round(float(d["spot_usd_oz"]), 2),
        "gold_egp_gram_24k": round(float(d["xau"]["price"]), 2),
        "silver_usd_oz": round(float(d["silver_usd_oz"]), 2),
        "btc_usd_xaus": int(d["btc_usd"]),
        "usd_egp_mid": float(d["fx_rate"]),
    }


def fetch_erapi():
    d = get_json("https://open.er-api.com/v6/latest/USD")
    r = d["rates"]
    usd = float(r["EGP"])
    return {
        "usd_egp_mid": usd,
        "eur_mid": round(usd / float(r["EUR"]), 2),
        "gbp_mid": round(usd / float(r["GBP"]), 2),
        "sar_mid": round(usd / float(r["SAR"]), 2),
        "aed_mid": round(usd / float(r["AED"]), 2),
        "kwd_mid": round(usd / float(r["KWD"]), 2),
        "updated_utc": d.get("time_last_update_utc"),
    }


def _num_after(text, label, window=400):
    """First number (with optional comma/decimal) shortly after a label."""
    i = text.find(label)
    if i < 0:
        return None
    seg = text[i:i + window]
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)", seg)
    return float(m.group(1).replace(",", "")) if m else None


def fetch_isagha():
    """Parse market.isagha.com price table (buy/sell per carat + silver).
    The table order on the page is: 24, 21, 18, gold pound, silver 999/925/800.
    Verified against live HTML 2026-11-12; if the layout changes the parser
    raises and the run keeps last good values instead of guessing.
    NOTE (2026-11-12 18:28): a news headline above the table ("عيار 21 يفقد
    60 جنيهًا...") made the old first-occurrence search grab 21/60. The parser
    now anchors on the table itself: an "عيار 24" followed by "عيار 21" within
    300 chars, and every pair must be in a plausible range with sell >= buy."""
    html = get_text("https://market.isagha.com")
    # Strip tags to a flat text with markers preserved.
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    # Find the price-table region (carat rows in sequence), not a headline.
    table = None
    start = 0
    while True:
        i = text.find("عيار 24", start)
        if i < 0:
            raise ValueError("iSagha: price table anchor not found")
        j = text.find("عيار 21", i)
        if 0 < j - i < 300:
            table = text[i:i + 600]
            break
        start = i + len("عيار 24")

    def pair(label, lo, hi):
        i = table.find(label)
        if i < 0:
            raise ValueError(f"iSagha: label {label} not found in price table")
        # Numbers AFTER the label — the label itself contains a number
        # ("عيار 24" -> 24) which must not be read as a price.
        nums = re.findall(r"(\d[\d,]*(?:\.\d+)?)", table[i + len(label):i + 200])
        if len(nums) < 2:
            raise ValueError(f"iSagha: too few numbers after {label}")
        buy = float(nums[0].replace(",", ""))
        sell = float(nums[1].replace(",", ""))
        if not (lo <= buy <= hi and lo <= sell <= hi and sell >= buy):
            raise ValueError(
                f"iSagha: implausible values after {label}: {buy}/{sell}")
        return buy, sell

    out = {}
    for carat in ("24", "21", "18"):
        out[f"g{carat}_buy"], out[f"g{carat}_sell"] = pair(
            f"عيار {carat}", 1000, 12000)
    out["gp_buy"], out["gp_sell"] = pair("جنيه ذهب", 10000, 100000)
    for code in ("999", "925", "800"):
        out[f"s{code}_buy"], out[f"s{code}_sell"] = pair(
            f"عيار {code}", 10, 300)
    return out


def fetch_cbe():
    """CBE homepage HTML. Usually WAF-blocked for bots — returns None on any
    failure; the caller keeps last good values (manual field in config.yaml)."""
    try:
        html = get_text("https://www.cbe.org.eg/", timeout=20)
    except Exception as e:
        log(f"CBE fetch failed (expected, WAF): {e}")
        return None
    if "Request Rejected" in html or "Support ID" in html:
        log("CBE fetch rejected by WAF")
        return None
    # CBE homepage tables are hard to parse reliably; do not guess.
    return {"note": "cbe html fetched but rates not auto-parsed; use manual field"}


def fetch_coingecko(demo_key=None):
    base = ("https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin,ethereum&vs_currencies=usd,egp")
    if demo_key:
        base += f"&x_cg_demo_api_key={demo_key}"
    d = get_json(base)
    return {
        "btc_usd": int(d["bitcoin"]["usd"]),
        "btc_egp": int(d["bitcoin"]["egp"]),
        "eth_usd": float(d["ethereum"]["usd"]),
        "eth_egp": int(d["ethereum"]["egp"]),
    }


def load_config():
    """Minimal YAML reader for the flat config (no PyYAML dependency)."""
    cfg = {"manual": {}}
    if not os.path.exists(CONFIG_PATH):
        return cfg
    section = None
    for line in open(CONFIG_PATH, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip() or line.strip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")) and line.rstrip().endswith(":"):
            section = line.rstrip()[:-1]
            cfg.setdefault(section, {})
            continue
        if ":" in line:
            k, v = line.strip().split(":", 1)
            v = v.strip().strip('"').strip("'")
            target = cfg[section] if section else cfg
            try:
                target[k.strip()] = float(v) if re.match(r"^-?\d+(\.\d+)?$", v) else v
            except ValueError:
                target[k.strip()] = v
    return cfg


def load_previous():
    """Latest data/*.json snapshot, or None."""
    if not os.path.isdir(DATA_DIR):
        return None
    files = sorted(f for f in os.listdir(DATA_DIR)
                   if f.startswith("prices-") and f.endswith(".json"))
    if not files:
        return None
    with open(os.path.join(DATA_DIR, files[-1]), encoding="utf-8") as f:
        return json.load(f)


def check_range(key, value):
    lo, hi = RANGES[key]
    if not (lo <= value <= hi):
        raise ValueError(f"Sanity range failed: {key}={value} not in [{lo},{hi}]")


def check_move(key, new, prev, max_pct):
    if prev is None:
        return
    old = prev.get(key)
    if old in (None, 0):
        return
    pct = abs(new - old) / old * 100
    if pct > max_pct:
        raise ValueError(f"Move check failed: {key} {old} -> {new} ({pct:.2f}% > {max_pct}%)")


def render_html(snapshot):
    """Replace values inside elements by id in the stored page HTML."""
    page_path = os.path.join(PAGES_DIR, "daily-prices-page.html")
    with open(page_path, encoding="utf-8") as f:
        html = f.read()
    vals = snapshot["values"]

    def fmt(v):
        if isinstance(v, float):
            return f"{v:,.2f}" if v < 1000 else f"{v:,.0f}" if v == int(v) else f"{v:,.2f}"
        if isinstance(v, int):
            return f"{v:,}"
        return str(v)

    def set_cell(el_id, text):
        nonlocal html
        pat = re.compile(r'(<[^>]*id="' + re.escape(el_id) + r'"[^>]*>)(.*?)(</[^>]+>)',
                         re.DOTALL)
        new_html, n = pat.subn(lambda m: m.group(1) + text + m.group(3), html, count=1)
        if n == 0:
            log(f"WARNING: element id={el_id} not found in page HTML")
        html = new_html

    # Quick cards
    set_cell("card-g21", f"{fmt(vals['g21_sell'])} ج")
    set_cell("card-usd", f"{vals['usd_cbe_sell']} ج")
    set_cell("card-btc", f"${fmt(vals['btc_usd'])}")
    # Gold
    for c in ("24", "21", "18"):
        set_cell(f"g{c}-buy", fmt(vals[f"g{c}_buy"]))
        set_cell(f"g{c}-sell", fmt(vals[f"g{c}_sell"]))
    set_cell("gp-buy", fmt(vals["gp_buy"]))
    set_cell("gp-sell", fmt(vals["gp_sell"]))
    set_cell("xau-oz", f"${fmt(vals['gold_usd_oz'])}")
    set_cell("xau-gram-egp", f"{fmt(vals['gold_egp_gram_24k'])} جنيه")
    set_cell("calc-price", fmt(vals["g21_sell"]))
    # FX
    set_cell("usd-cbe-buy", str(vals["usd_cbe_buy"]))
    set_cell("usd-cbe-sell", str(vals["usd_cbe_sell"]))
    set_cell("usd-bank-buy", str(vals["usd_bank_buy"]))
    set_cell("usd-bank-sell", str(vals["usd_bank_sell"]))
    for cur in ("eur", "gbp", "sar", "aed", "kwd"):
        set_cell(f"{cur}-mid", f"{fmt(vals[cur + '_mid'])} (وسطي)")
    # Silver
    for code in ("999", "925", "800"):
        set_cell(f"s{code}-buy", fmt(vals[f"s{code}_buy"]))
        set_cell(f"s{code}-sell", fmt(vals[f"s{code}_sell"]))
    set_cell("xau-silver-oz", f"${fmt(vals['silver_usd_oz'])}")
    # Crypto
    set_cell("btc-usd", fmt(vals["btc_usd"]))
    set_cell("btc-egp", fmt(vals["btc_egp"]))
    set_cell("eth-usd", fmt(vals["eth_usd"]))
    set_cell("eth-egp", fmt(vals["eth_egp"]))
    # EGX (yesterday's close until the 16:00 snapshot)
    set_cell("egx30", fmt(vals["egx30"]))
    set_cell("egx70", fmt(vals["egx70"]))
    set_cell("egx100", fmt(vals["egx100"]))
    # Food
    set_cell("food-rice", fmt(vals["food_rice"]))
    set_cell("food-sugar", fmt(vals["food_sugar"]))
    set_cell("food-sunflower", fmt(vals["food_sunflower"]))
    set_cell("food-corn", fmt(vals["food_corn"]))
    set_cell("food-flour", fmt(vals["food_flour"]))
    set_cell("food-beans", fmt(vals["food_beans"]))
    # Stamp
    stamp = snapshot["stamp_cairo"]
    pat = re.compile(r'(<div class="dp-stamp" id="dp-stamp">).*?(</div>)', re.DOTALL)
    html, n = pat.subn(
        lambda m: m.group(1) + f"آخر تحديث: <b>{stamp}</b> بتوقيت القاهرة &nbsp;|&nbsp; التحديث التالي: <b>{snapshot['next_update']}</b> &nbsp;|&nbsp; الأسعار بدون مصنعية أو دمغة أو ضريبة" + m.group(2),
        html, count=1)
    if n == 0:
        log("WARNING: stamp div not found")
    return html


def blogger_access_token(client_id, client_secret, refresh_token):
    import urllib.parse
    data = urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh_token, "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token",
                                 data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode())["access_token"]


def blogger_update_page(blog_id, page_id, html, token):
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/pages/{page_id}"
    body = json.dumps({"content": html}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="PUT", headers={
        **UA, "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode())


def main():
    dry_run = "--dry-run" in sys.argv
    publish = "--publish" in sys.argv
    force = "--force" in sys.argv
    if not (dry_run or publish):
        print("Usage: fetch_prices.py --dry-run | --publish [--force]")
        return 2
    now_cairo = datetime.now(CAIRO)
    hour = now_cairo.hour
    # Friday (4) / Saturday (5): morning snapshot only — skip the 16:00 run.
    # --force bypasses this (explicit manual runs only).
    if not force and now_cairo.weekday() in (4, 5) and hour >= 12:
        log("Friday/Saturday afternoon run skipped (morning snapshot only).")
        return 0

    cfg = load_config()
    manual = cfg.get("manual", {})
    prev = load_previous()
    prev_vals = prev["values"] if prev else {}
    vals = {}
    notes = []

    # --- Fetch auto sources (each failure keeps last good value) ---
    try:
        x = fetch_xaus()
        vals.update(x)
        log(f"XAUS ok: gold ${x['gold_usd_oz']}/oz, {x['gold_egp_gram_24k']} EGP/g")
    except Exception as e:
        log(f"XAUS FAILED: {e} — keeping last good values")
        for k in ("gold_usd_oz", "gold_egp_gram_24k", "silver_usd_oz"):
            vals[k] = prev_vals.get(k)
        notes.append("XAUS failed")

    try:
        e = fetch_erapi()
        vals["usd_egp_mid"] = e["usd_egp_mid"]
        for cur in ("eur", "gbp", "sar", "aed", "kwd"):
            vals[cur + "_mid"] = e[cur + "_mid"]
        log(f"er-api ok: USD/EGP mid {e['usd_egp_mid']}")
    except Exception as ex:
        log(f"er-api FAILED: {ex} — keeping last good values")
        for k in ("usd_egp_mid", "eur_mid", "gbp_mid", "sar_mid", "aed_mid", "kwd_mid"):
            vals[k] = prev_vals.get(k)
        notes.append("er-api failed")

    isagha_fresh = False
    try:
        g = fetch_isagha()
        vals.update(g)
        isagha_fresh = True
        log(f"iSagha ok: 21 buy {g['g21_buy']} / sell {g['g21_sell']}")
    except Exception as ex:
        log(f"iSagha FAILED: {ex} — keeping last good values")
        for k in ("g24_buy", "g24_sell", "g21_buy", "g21_sell", "g18_buy",
                  "g18_sell", "gp_buy", "gp_sell", "s999_buy", "s999_sell",
                  "s925_buy", "s925_sell", "s800_buy", "s800_sell"):
            vals[k] = prev_vals.get(k)
        notes.append("iSagha failed")

    try:
        c = fetch_coingecko(os.environ.get("COINGECKO_DEMO_KEY"))
        vals.update(c)
        log(f"CoinGecko ok: BTC ${c['btc_usd']}")
    except Exception as ex:
        log(f"CoinGecko FAILED: {ex} — keeping last good values")
        for k in ("btc_usd", "btc_egp", "eth_usd", "eth_egp"):
            vals[k] = prev_vals.get(k)
        notes.append("CoinGecko failed")

    fetch_cbe()  # best effort only; CBE buy/sell come from the manual field.

    # --- Manual fields (operator updates config.yaml) ---
    for key in ("usd_cbe_buy", "usd_cbe_sell", "usd_bank_buy", "usd_bank_sell",
                "egx30", "egx70", "egx100",
                "food_rice", "food_sugar", "food_sunflower", "food_corn",
                "food_flour", "food_beans"):
        if key in manual and manual[key] not in ("", None):
            vals[key] = manual[key]
        else:
            vals[key] = prev_vals.get(key)
            notes.append(f"manual field missing, kept last good: {key}")

    # --- Validation (a failure here stops the publish entirely) ---
    if vals.get("g21_buy") is None or vals.get("gold_egp_gram_24k") is None:
        log("FATAL: no gold values at all (first run needs a baseline snapshot).")
        return 1
    for key in ("g21_buy", "g21_sell", "g24_buy", "g24_sell",
                "usd_cbe_buy", "usd_cbe_sell", "btc_usd", "eth_usd", "egx30"):
        if vals.get(key) is not None:
            check_range(key, vals[key])
    check_move("g21_sell", vals["g21_sell"], prev_vals, MAX_GOLD_MOVE_PCT)
    check_move("usd_cbe_sell", vals["usd_cbe_sell"], prev_vals, MAX_USD_MOVE_PCT)
    # iSagha vs theoretical (XAUS gram x purity) — only when iSagha was
    # fetched FRESH this run. On a fallback to last-good values the check
    # would compare stale iSagha vs live XAUS and false-fail on a normal
    # intraday gold move (seen 2026-11-12 18:29: 3.65% on stale data).
    theo_24 = vals["gold_egp_gram_24k"]
    if isagha_fresh and theo_24:
        dev = abs(vals["g24_sell"] - theo_24) / theo_24 * 100
        if dev > MAX_THEORY_DEVIATION_PCT:
            raise ValueError(
                f"Theory deviation failed: iSagha 24 sell {vals['g24_sell']} vs "
                f"theoretical {theo_24} ({dev:.2f}% > {MAX_THEORY_DEVIATION_PCT}%)")

    stamp = now_cairo.strftime("%A %d %B %Y — %I:%M %p").replace("AM", "ص").replace("PM", "م")
    # Arabic weekday/month names for the stamp:
    wd = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"][now_cairo.weekday()]
    mo = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو",
          "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"][now_cairo.month - 1]
    hh = now_cairo.strftime("%I:%M")
    ap = "ص" if now_cairo.hour < 12 else "م"
    stamp = f"{wd} {now_cairo.day} {mo} {now_cairo.year} — {hh} {ap}"
    next_update = "16:00" if hour < 12 else "09:00 (غداً)"
    snapshot = {
        "date": now_cairo.strftime("%Y-%m-%d"),
        "stamp_cairo": stamp,
        "next_update": next_update,
        "values": vals,
        "notes": notes,
    }

    # Archive JSON
    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, f"prices-{now_cairo.strftime('%Y-%m-%d')}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    log(f"Snapshot archived: {out_path}")
    if notes:
        log("Notes: " + "; ".join(notes))

    html = render_html(snapshot)
    if dry_run:
        log("DRY RUN: HTML rendered, Blogger NOT updated.")
        return 0

    # Publish via Blogger API v3 pages.update
    token = blogger_access_token(
        os.environ["BLOGGER_CLIENT_ID"], os.environ["BLOGGER_CLIENT_SECRET"],
        os.environ["BLOGGER_REFRESH_TOKEN"])
    res = blogger_update_page(os.environ["BLOG_ID"], os.environ["PAGE_ID"],
                              html, token)
    log(f"Blogger page updated: {res.get('url', res.get('id'))}")
    # Keep the rendered copy in the repo as well.
    os.makedirs(PAGES_DIR, exist_ok=True)
    with open(os.path.join(PAGES_DIR, "daily-prices-page.html"), "w",
              encoding="utf-8") as f:
        f.write(html)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as e:
        log(f"VALIDATION FAILED — nothing published: {e}")
        sys.exit(1)
    except Exception as e:
        log(f"ERROR — nothing published: {type(e).__name__}: {e}")
        sys.exit(1)
