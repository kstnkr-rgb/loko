import os, re, logging, asyncio
import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["BOT_TOKEN"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

def is_acestream(url):
    return "acestream://" in url or re.match(r'^[a-f0-9]{40}$', url.strip())

def extract_streams(links):
    browser, ace = [], []
    for text, url in links:
        url = url.strip()
        if not url or url == "#":
            continue
        if is_acestream(url):
            hash_ = url.replace("acestream://", "").strip()
            ace.append((text or "Ace Stream", hash_))
        elif url.startswith("http"):
            browser.append((text or "Смотреть", url))
    return browser, ace

import urllib3
urllib3.disable_warnings()

BASE = "https://livetv.sx"

def get(url):
    return requests.get(url, headers=HEADERS, timeout=10, verify=False)

def find_event_pages(team):
    pages = []
    try:
        r = get(f"{BASE}/dex/")
        soup = BeautifulSoup(r.text, "html.parser")
        team_lower = team.lower()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/eventinfo/" not in href:
                continue
            title = a.get_text(strip=True)
            if team_lower in title.lower():
                full = href if href.startswith("http") else BASE + href
                pages.append((title, full))
    except Exception as e:
        logging.warning(f"livetv main page error: {e}")
    return pages

STATIC_EXT_RE = re.compile(
    r'\.(gif|png|jpg|jpeg|ico|svg|webp|css|js|woff|woff2|ttf|eot)(\?|$)',
    re.IGNORECASE
)
SKIP_DOMAINS = {"adobe.com", "get.adobe.com", "google.com", "facebook.com", "twitter.com"}
PLAYER_URL_RE = re.compile(
    r'(https?://[^\s\'"<>]+(?:webplayer|player\.php|embed\.php|stream\.php|/play/|/embed/|\.m3u8)[^\s\'"<>]*)',
    re.IGNORECASE
)

def is_stream_url(url):
    if STATIC_EXT_RE.search(url):
        return False
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if any(parsed.netloc.endswith(d) for d in SKIP_DOMAINS):
        return False
    has_path = parsed.path not in ("", "/")
    has_query = bool(parsed.query)
    return has_path or has_query

def scrape_event_page(title, url):
    links = []
    seen = set()
    try:
        r = get(url)
        soup = BeautifulSoup(r.text, "html.parser")

        def normalize(u):
            if u.startswith("//"):
                return "https:" + u
            return u

        for a in soup.find_all("a", href=True):
            href = normalize(a["href"].strip())
            label = a.get_text(strip=True) or "Stream"
            if not href or href == "#" or href in seen:
                continue
            seen.add(href)
            if "acestream://" in href:
                links.append((label, href))
            elif re.match(r'^[a-f0-9]{40}$', href):
                links.append((label, "acestream://" + href))
            elif href.startswith("http") and is_stream_url(href) and any(
                x in href.lower() for x in ["player", "stream", "embed", "watch", "webplayer"]
            ):
                links.append((label, href))

        for iframe in soup.find_all("iframe", src=True):
            src = normalize(iframe["src"].strip())
            if not src or src in seen:
                continue
            seen.add(src)
            if src.startswith("http") and is_stream_url(src):
                links.append(("Player", src))

        for m in PLAYER_URL_RE.finditer(r.text):
            u = m.group(1).rstrip("',;\"\\")
            if u not in seen and is_stream_url(u):
                seen.add(u)
                links.append(("Stream", u))

        for m in re.finditer(r'acestream://([a-f0-9]{40})', r.text):
            ace_url = "acestream://" + m.group(1)
            if ace_url not in seen:
                seen.add(ace_url)
                links.append(("Ace Stream", ace_url))

        for m in re.finditer(r'["\']([a-f0-9]{40})["\']', r.text):
            ace_url = "acestream://" + m.group(1)
            if ace_url not in seen:
                seen.add(ace_url)
                links.append(("Ace Stream", ace_url))

    except Exception as e:
        logging.warning(f"event page error {url}: {e}")
    return links

PIMPLE_BASE = "https://www.pimpletv.ru"

def scrape_pimpletv(team):
    results = []
    try:
        r = requests.get(PIMPLE_BASE, params={"s": team}, headers=HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(r.text, "html.parser")

        ace_links = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith("acestream://"):
                if href not in seen:
                    seen.add(href)
                    ace_links.append(("Ace Stream", href))

        for m in re.finditer(r'acestream://([a-f0-9]{40})', r.text):
            h = "acestream://" + m.group(1)
            if h not in seen:
                seen.add(h)
                ace_links.append(("Ace Stream", h))

        match_page_re = re.compile(r'^/(?:football|hockey)/\d+-.+/$')
        seen_pages = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not match_page_re.match(href):
                continue
            full = PIMPLE_BASE + href
            if full in seen_pages:
                continue
            seen_pages.add(full)
            try:
                pr = requests.get(full, headers=HEADERS, timeout=10, verify=False)
                for m in re.finditer(r'acestream://([a-f0-9]{40})', pr.text):
                    h = "acestream://" + m.group(1)
                    if h not in seen:
                        seen.add(h)
                        ace_links.append(("Ace Stream", h))
            except Exception as e:
                logging.warning(f"pimpletv match page error {full}: {e}")

        if ace_links:
            results.append({"title": team, "links": ace_links, "source": "pimpletv.ru"})
    except Exception as e:
        logging.warning(f"pimpletv error: {e}")
    return results

def scrape_rplnews(team):
    results = []
    try:
        r = requests.get("http://rplnews.online/", headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        team_lower = team.lower()
        for row in soup.find_all(["article", "div", "tr", "li"]):
            text = row.get_text(" ", strip=True).lower()
            if team_lower not in text:
                continue
            links = []
            for a in row.find_all("a", href=True):
                href = a["href"]
                if href.startswith("/"):
                    href = "http://rplnews.online" + href
                links.append((a.get_text(strip=True), href))
            if links:
                title = row.get_text(" ", strip=True)[:80]
                results.append({"title": title, "links": links, "source": "rplnews.online"})
    except Exception as e:
        logging.warning(f"rplnews error: {e}")
    return results

SOURCES = ["livetv.sx", "pimpletv.ru", "rplnews.online"]

def search_by_source(terms):
    """Search multiple terms across all sources, return dict {source: {browser, ace}}."""
    result = {s: {"browser": [], "ace": [], "seen": set()} for s in SOURCES}

    for team in terms:
        # livetv.sx
        for title, url in find_event_pages(team):
            links = scrape_event_page(title, url)
            for label, href in links:
                s = result["livetv.sx"]
                if href in s["seen"]:
                    continue
                s["seen"].add(href)
                if "acestream://" in href or re.match(r'^[a-f0-9]{40}$', href):
                    s["ace"].append((title, label, href.replace("acestream://", "")))
                else:
                    s["browser"].append((title, label, href))

        # pimpletv.ru
        for match in scrape_pimpletv(team):
            b, a = extract_streams(match["links"])
            s = result["pimpletv.ru"]
            for item in b:
                if item[1] not in s["seen"]:
                    s["seen"].add(item[1])
                    s["browser"].append((match["title"], item[0], item[1]))
            for item in a:
                if item[1] not in s["seen"]:
                    s["seen"].add(item[1])
                    s["ace"].append((match["title"], item[0], item[1]))

        # rplnews.online
        for match in scrape_rplnews(team):
            b, a = extract_streams(match["links"])
            s = result["rplnews.online"]
            for item in b:
                if item[1] not in s["seen"]:
                    s["seen"].add(item[1])
                    s["browser"].append((match["title"], item[0], item[1]))
            for item in a:
                if item[1] not in s["seen"]:
                    s["seen"].add(item[1])
                    s["ace"].append((match["title"], item[0], item[1]))

    return result

def search_streams(team):
    """Legacy: returns (browser_all, ace_all) for backward compat."""
    data = search_by_source([team])
    browser_all, ace_all = [], []
    for source, s in data.items():
        for title, label, url in s["browser"]:
            browser_all.append((title, label, url, source))
        for title, label, hash_ in s["ace"]:
            ace_all.append((title, label, hash_, source))
    return browser_all, ace_all

def format_by_source(label, data):
    lines = [f"📺 <b>{label}</b> — трансляции\n"]
    for source in SOURCES:
        s = data[source]
        lines.append(f"<b>{source}:</b>")
        if not s["browser"] and not s["ace"]:
            lines.append("На данном ресурсе трансляция не найдена")
        else:
            for _, lbl, url in s["browser"][:5]:
                lines.append(f'• <a href="{url}">{lbl}</a>')
            for _, lbl, hash_ in s["ace"][:5]:
                lines.append(f"• <code>acestream://{hash_}</code>")
        lines.append("")
    return "\n".join(lines).strip()

def format_response(team, browser, ace):
    if not browser and not ace:
        return f"❌ Трансляции для <b>{team}</b> не найдены.\n\nПопробуйте в день матча или другое написание."

    lines = [f"📺 <b>{team}</b> — трансляции\n"]

    if browser:
        lines.append("🌐 <b>В браузере:</b>")
        for title, label, url, source in browser[:8]:
            lines.append(f'• <a href="{url}">{label}</a> — <i>{source}</i>')
        lines.append("")

    if ace:
        lines.append("⚡ <b>Ace Stream</b> (скопируй хэш и вставь в плеер):")
        for title, label, hash_, source in ace[:8]:
            lines.append(f"• <code>acestream://{hash_}</code> — <i>{source}</i>")

    return "\n".join(lines)

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
    data = await loop.run_in_executor(None, search_by_source, ["Локомотив", "Lokomotiv"])
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
