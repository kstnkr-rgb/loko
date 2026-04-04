import os, re, logging, asyncio
import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["BOT_TOKEN"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

import urllib3
urllib3.disable_warnings()

def is_acestream(url):
    return "acestream://" in url or re.match(r'^[a-f0-9]{40}$', url.strip())

# ── transliteration ───────────────────────────────────────────────────────────

RU_TO_LAT = {
    'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'yo','ж':'zh',
    'з':'z','и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o',
    'п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts',
    'ч':'ch','ш':'sh','щ':'shch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu',
    'я':'ya',
}
LAT_TO_RU = {v: k for k, v in RU_TO_LAT.items() if v}

def translit_ru_to_lat(text):
    result = []
    for ch in text.lower():
        result.append(RU_TO_LAT.get(ch, ch))
    return "".join(result)

def expand_search_terms(term):
    """Return list of search variants: original + transliterated if different."""
    terms = [term]
    lat = translit_ru_to_lat(term)
    if lat != term.lower() and lat not in [t.lower() for t in terms]:
        terms.append(lat)
    return terms

def extract_ace_hashes(text):
    """Find all acestream hashes in raw text."""
    found = []
    seen = set()
    for m in re.finditer(r'acestream://([a-f0-9]{40})', text):
        h = m.group(1)
        if h not in seen:
            seen.add(h)
            found.append(h)
    for m in re.finditer(r'["\']([a-f0-9]{40})["\']', text):
        h = m.group(1)
        if h not in seen:
            seen.add(h)
            found.append(h)
    return found

# ── livetv.sx ────────────────────────────────────────────────────────────────

LIVETV_BASE = "https://livetv.sx"

STREAM_URL_RE = re.compile(
    r'(https?://[^\s\'"<>]+(?:webplayer|player\.php|embed\.php|stream\.php|/play/|/embed/|\.m3u8)[^\s\'"<>]*)',
    re.IGNORECASE
)
STATIC_EXT_RE = re.compile(r'\.(gif|png|jpg|jpeg|ico|svg|webp|css|js|woff2?|ttf|eot)(\?|$)', re.IGNORECASE)

def scrape_livetv(team):
    result = {"ace": [], "browser": []}
    try:
        r = requests.get(f"{LIVETV_BASE}/enx/allupcomingsports/1/",
                         headers=HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(r.text, "html.parser")
        team_lower = team.lower()

        event_urls = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/eventinfo/" not in href:
                continue
            title = a.get_text(strip=True)
            if team_lower in title.lower():
                full = href if href.startswith("http") else LIVETV_BASE + href
                event_urls.append((title, full))

        seen_ace = set()
        seen_browser = set()
        for title, url in event_urls:
            try:
                r2 = requests.get(url, headers=HEADERS, timeout=10, verify=False)
                soup2 = BeautifulSoup(r2.text, "html.parser")

                # acestream
                for h in extract_ace_hashes(r2.text):
                    if h not in seen_ace:
                        seen_ace.add(h)
                        result["ace"].append({"title": title, "hash": h})

                # browser: iframes
                for iframe in soup2.find_all("iframe", src=True):
                    src = iframe["src"].strip()
                    if src.startswith("//"):
                        src = "https:" + src
                    if src.startswith("http") and not STATIC_EXT_RE.search(src) and src not in seen_browser:
                        seen_browser.add(src)
                        result["browser"].append({"title": title, "url": src})

                # browser: player URLs in raw text
                for m in STREAM_URL_RE.finditer(r2.text):
                    u = m.group(1).rstrip("',;\"\\")
                    if not STATIC_EXT_RE.search(u) and u not in seen_browser:
                        seen_browser.add(u)
                        result["browser"].append({"title": title, "url": u})

            except Exception as e:
                logging.warning(f"livetv event error {url}: {e}")
    except Exception as e:
        logging.warning(f"livetv error: {e}")
    return result

# ── pimpletv.ru ───────────────────────────────────────────────────────────────

PIMPLE_BASE = "https://www.pimpletv.ru"
PIMPLE_MATCH_RE = re.compile(r'^/(?:football|hockey)/\d+-.+/$')

def scrape_pimpletv(team):
    result = {"ace": [], "browser": []}
    try:
        r = requests.get(f"{PIMPLE_BASE}/category/football/",
                         headers=HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(r.text, "html.parser")
        team_lower = team.lower()

        seen_pages = set()
        seen_hashes = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            title = a.get_text(" ", strip=True)
            if not PIMPLE_MATCH_RE.match(href):
                continue
            if team_lower not in title.lower():
                continue
            full = PIMPLE_BASE + href
            if full in seen_pages:
                continue
            seen_pages.add(full)
            try:
                pr = requests.get(full, headers=HEADERS, timeout=10, verify=False)
                ps = BeautifulSoup(pr.text, "html.parser")
                for pa in ps.find_all("a", href=True):
                    phref = pa["href"].strip()
                    if phref.startswith("acestream://"):
                        h = phref.replace("acestream://", "")
                        if h not in seen_hashes:
                            seen_hashes.add(h)
                            result["ace"].append({"title": title, "hash": h})
                for h in extract_ace_hashes(pr.text):
                    if h not in seen_hashes:
                        seen_hashes.add(h)
                        result["ace"].append({"title": title, "hash": h})
            except Exception as e:
                logging.warning(f"pimpletv match error {full}: {e}")
    except Exception as e:
        logging.warning(f"pimpletv error: {e}")
    return result

# ── sportnet.live ─────────────────────────────────────────────────────────────

SPORTNET_BASE = "https://sportnet.live"
SPORTNET_EVENT_RE = re.compile(r'^/football/event/\d+/')

def scrape_sportnet(team):
    result = {"ace": [], "browser": []}
    try:
        r = requests.get(f"{SPORTNET_BASE}/football",
                         headers=HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(r.text, "html.parser")
        team_lower = team.lower()

        seen_pages = set()
        seen_hashes = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            title = a.get_text(" ", strip=True)
            if not SPORTNET_EVENT_RE.match(href):
                continue
            if team_lower not in title.lower():
                continue
            full = href if href.startswith("http") else SPORTNET_BASE + href
            if full in seen_pages:
                continue
            seen_pages.add(full)
            try:
                pr = requests.get(full, headers=HEADERS, timeout=10, verify=False)
                ps = BeautifulSoup(pr.text, "html.parser")

                el = ps.find(attrs={"data-stream": True})
                if el:
                    stream_val = el["data-stream"]
                    aj = requests.post(
                        f"{SPORTNET_BASE}/_ajax_set_player.php",
                        data={"stream": stream_val},
                        headers=HEADERS, timeout=10, verify=False
                    )
                    for h in extract_ace_hashes(aj.text):
                        if h not in seen_hashes:
                            seen_hashes.add(h)
                            result["ace"].append({"title": title, "hash": h})

                for h in extract_ace_hashes(pr.text):
                    if h not in seen_hashes:
                        seen_hashes.add(h)
                        result["ace"].append({"title": title, "hash": h})
            except Exception as e:
                logging.warning(f"sportnet match error {full}: {e}")
    except Exception as e:
        logging.warning(f"sportnet error: {e}")
    return result

# ── myfootball.cc ────────────────────────────────────────────────────────────

MYFOOTBALL_BASE = "https://myfootball.cc"

def scrape_myfootball(team):
    result = {"ace": [], "browser": []}
    try:
        r = requests.get(MYFOOTBALL_BASE + "/", headers=HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(r.text, "html.parser")
        team_lower = team.lower()

        match_urls = []
        seen_urls = set()

        for div in soup.find_all("div", class_="rewievs_tab1"):
            for a in div.find_all("a", href=True):
                title = a.get("title", "") + " " + a.get_text(" ", strip=True)
                if team_lower not in title.lower():
                    continue
                href = a["href"]
                if href not in seen_urls:
                    seen_urls.add(href)
                    match_urls.append((title.strip(), href))

        for card in soup.find_all("div", class_="top-match-card"):
            href = card.get("data-link", "")
            if not href or href in seen_urls:
                continue
            title = card.get_text(" ", strip=True)
            if team_lower not in title.lower():
                continue
            seen_urls.add(href)
            match_urls.append((title[:60], href))

        seen_hashes = set()
        for title, url in match_urls:
            # add the match page itself as browser link
            result["browser"].append({"title": title, "url": url})
            try:
                pr = requests.get(url, headers=HEADERS, timeout=10, verify=False)
                ps = BeautifulSoup(pr.text, "html.parser")
                for a in ps.find_all("a", href=True):
                    href = a["href"].strip()
                    if not href.startswith("acestream://"):
                        continue
                    h = href.replace("acestream://", "")
                    if h not in seen_hashes:
                        seen_hashes.add(h)
                        result["ace"].append({"title": title, "hash": h})
            except Exception as e:
                logging.warning(f"myfootball match error {url}: {e}")
    except Exception as e:
        logging.warning(f"myfootball error: {e}")
    return result

# ── rplnews.online ───────────────────────────────────────────────────────────

def scrape_rplnews(team):
    result = {"ace": [], "browser": []}
    try:
        r = requests.get("https://rplnews.online/", headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        team_lower = team.lower()
        for a in soup.find_all("a", href=True):
            classes = a.get("class", [])
            if "imatch" not in classes:
                continue
            title = a.get_text(" ", strip=True)
            if team_lower not in title.lower():
                continue
            href = a["href"]
            if href.startswith("/"):
                href = "https://rplnews.online" + href
            result["browser"].append({"title": title, "url": href})
    except Exception as e:
        logging.warning(f"rplnews error: {e}")
    return result

# ── search across all sources ─────────────────────────────────────────────────

SOURCES = [
    ("livetv.sx",      scrape_livetv),
    ("pimpletv.ru",    scrape_pimpletv),
    ("sportnet.live",  scrape_sportnet),
    ("myfootball.cc",  scrape_myfootball),
    ("rplnews.online", scrape_rplnews),
]

def search_by_source(terms):
    expanded = []
    for t in terms:
        for v in expand_search_terms(t):
            if v not in expanded:
                expanded.append(v)

    result = {name: {"ace": [], "browser": [], "seen_ace": set(), "seen_browser": set()} for name, _ in SOURCES}
    for team in expanded:
        for name, scraper in SOURCES:
            s = result[name]
            scraped = scraper(team)
            for item in scraped.get("ace", []):
                if item["hash"] not in s["seen_ace"]:
                    s["seen_ace"].add(item["hash"])
                    s["ace"].append(item)
            for item in scraped.get("browser", []):
                if item["url"] not in s["seen_browser"]:
                    s["seen_browser"].add(item["url"])
                    s["browser"].append(item)
    return result

# ── formatting ────────────────────────────────────────────────────────────────

def format_by_source(label, data):
    lines = [f"📺 <b>{label}</b> — трансляции\n"]
    for name, _ in SOURCES:
        s = data[name]
        lines.append(f"<b>{name}:</b>")
        if not s["ace"] and not s["browser"]:
            lines.append("На данном ресурсе трансляция не найдена")
        else:
            for item in s["ace"][:6]:
                lines.append(f"• <code>acestream://{item['hash']}</code>")
                if item.get("title"):
                    lines[-1] += f" <i>({item['title'][:40]})</i>"
            for item in s["browser"][:3]:
                lines.append(f'• <a href="{item["url"]}">Stream</a>')
        lines.append("")
    return "\n".join(lines).strip()

# ── handlers ──────────────────────────────────────────────────────────────────

LOKO_BUTTON = InlineKeyboardMarkup([[
    InlineKeyboardButton("🔍 Найти Локомотив", callback_data="loko")
]])

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я ищу трансляции футбольных матчей.\n\n"
        "Просто напиши название команды, например:\n"
        "<b>Локомотив</b>\n\n"
        "Или нажми кнопку ниже:",
        parse_mode="HTML",
        reply_markup=LOKO_BUTTON,
    )

async def loko_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    msg = await query.message.reply_text("🔍 Ищу трансляции Локомотива...", parse_mode="HTML")
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, search_by_source, ["Локомотив", "Lokomotiv", "Lokomotive"])
    text = format_by_source("Локомотив", data)
    await msg.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)

async def find_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    team = " ".join(ctx.args) if ctx.args else "Локомотив"
    await do_search(update, team)

async def message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    team = update.message.text.strip()
    if team:
        await do_search(update, team)

async def do_search(update: Update, team: str):
    msg = await update.message.reply_text(f"🔍 Ищу трансляции для <b>{team}</b>...", parse_mode="HTML")
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, search_by_source, [team])
    text = format_by_source(team, data)
    await msg.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("find", find_cmd))
app.add_handler(CallbackQueryHandler(loko_callback, pattern="^loko$"))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

if __name__ == "__main__":
    app.run_polling()
