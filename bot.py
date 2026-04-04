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

def scrape_livetv(team):
    ace = []
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

        seen = set()
        for title, url in event_urls:
            try:
                r2 = requests.get(url, headers=HEADERS, timeout=10, verify=False)
                for h in extract_ace_hashes(r2.text):
                    if h not in seen:
                        seen.add(h)
                        ace.append({"title": title, "hash": h})
            except Exception as e:
                logging.warning(f"livetv event error {url}: {e}")
    except Exception as e:
        logging.warning(f"livetv error: {e}")
    return ace

# ── pimpletv.ru ───────────────────────────────────────────────────────────────

PIMPLE_BASE = "https://www.pimpletv.ru"
PIMPLE_MATCH_RE = re.compile(r'^/(?:football|hockey)/\d+-.+/$')

def scrape_pimpletv(team):
    ace = []
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
                for h in extract_ace_hashes(pr.text):
                    if h not in seen_hashes:
                        seen_hashes.add(h)
                        ace.append({"title": title, "hash": h})
                # also grab acestream:// hrefs directly
                ps = BeautifulSoup(pr.text, "html.parser")
                for pa in ps.find_all("a", href=True):
                    phref = pa["href"].strip()
                    if phref.startswith("acestream://"):
                        h = phref.replace("acestream://", "")
                        if h not in seen_hashes:
                            seen_hashes.add(h)
                            ace.append({"title": title, "hash": h})
            except Exception as e:
                logging.warning(f"pimpletv match error {full}: {e}")
    except Exception as e:
        logging.warning(f"pimpletv error: {e}")
    return ace

# ── sportnet.live ─────────────────────────────────────────────────────────────

SPORTNET_BASE = "https://sportnet.live"
SPORTNET_EVENT_RE = re.compile(r'^/football/event/\d+/')

def scrape_sportnet(team):
    ace = []
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

                # Try AJAX: find data-stream attribute
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
                            ace.append({"title": title, "hash": h})

                # Also try raw text
                for h in extract_ace_hashes(pr.text):
                    if h not in seen_hashes:
                        seen_hashes.add(h)
                        ace.append({"title": title, "hash": h})
            except Exception as e:
                logging.warning(f"sportnet match error {full}: {e}")
    except Exception as e:
        logging.warning(f"sportnet error: {e}")
    return ace

# ── myfootball.cc ────────────────────────────────────────────────────────────

MYFOOTBALL_BASE = "https://myfootball.cc"

def scrape_myfootball(team):
    ace = []
    try:
        r = requests.get(MYFOOTBALL_BASE + "/", headers=HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(r.text, "html.parser")
        team_lower = team.lower()

        match_urls = []
        seen_urls = set()

        # List items: div.rewievs_tab1 > a
        for a in soup.find_all("a", href=True):
            if not a.find_parent(class_="rewievs_tab1"):
                continue
            title = a.get("title", "") + " " + a.get_text(" ", strip=True)
            if team_lower not in title.lower():
                continue
            href = a["href"]
            if href not in seen_urls:
                seen_urls.add(href)
                match_urls.append((title.strip(), href))

        # Featured cards: div.top-match-card[data-link]
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
                        ace.append({"title": title, "hash": h})
            except Exception as e:
                logging.warning(f"myfootball match error {url}: {e}")
    except Exception as e:
        logging.warning(f"myfootball error: {e}")
    return ace

# ── rplnews.online ───────────────────────────────────────────────────────────

def scrape_rplnews(team):
    browser = []
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
            browser.append({"title": title, "url": href})
    except Exception as e:
        logging.warning(f"rplnews error: {e}")
    return browser

# ── search across all sources ─────────────────────────────────────────────────

SOURCES = [
    ("livetv.sx",      scrape_livetv,      "ace"),
    ("pimpletv.ru",    scrape_pimpletv,    "ace"),
    ("sportnet.live",  scrape_sportnet,    "ace"),
    ("myfootball.cc",  scrape_myfootball,  "ace"),
    ("rplnews.online", scrape_rplnews,     "browser"),
]

def search_by_source(terms):
    result = {name: [] for name, _, _ in SOURCES}
    seen_global = {name: set() for name, _, _ in SOURCES}
    for team in terms:
        for name, scraper, kind in SOURCES:
            for item in scraper(team):
                key = item.get("hash") or item.get("url", "")
                if key not in seen_global[name]:
                    seen_global[name].add(key)
                    result[name].append(item)
    return result

# ── formatting ────────────────────────────────────────────────────────────────

def format_by_source(label, data):
    lines = [f"📺 <b>{label}</b> — трансляции\n"]
    for name, _, kind in SOURCES:
        items = data[name]
        lines.append(f"<b>{name}:</b>")
        if not items:
            lines.append("На данном ресурсе трансляция не найдена")
        else:
            for item in items[:6]:
                if kind == "ace":
                    lines.append(f"• <code>acestream://{item['hash']}</code>")
                    if item.get("title"):
                        lines[-1] += f" <i>({item['title'][:40]})</i>"
                else:
                    title = item.get("title", "Смотреть")[:50]
                    lines.append(f'• <a href="{item["url"]}">{title}</a>')
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
