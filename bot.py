import os, re, logging, asyncio
import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

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
    """Find eventinfo URLs on main page matching team name."""
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
# Webplayer URLs: dynamic endpoints (contain query string or .php/.m3u8/.ts path)
PLAYER_URL_RE = re.compile(
    r'(https?://[^\s\'"<>]+(?:webplayer|player\.php|embed\.php|stream\.php|/play/|/embed/|\.m3u8)[^\s\'"<>]*)',
    re.IGNORECASE
)

def is_stream_url(url):
    """True if URL looks like a video stream, not a static file or bare domain."""
    if STATIC_EXT_RE.search(url):
        return False
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if any(parsed.netloc.endswith(d) for d in SKIP_DOMAINS):
        return False
    # Must have a real path or query string (not just "/")
    has_path = parsed.path not in ("", "/")
    has_query = bool(parsed.query)
    return has_path or has_query

def scrape_event_page(title, url):
    """Extract browser and acestream links from an eventinfo page."""
    links = []
    seen = set()
    try:
        r = get(url)
        soup = BeautifulSoup(r.text, "html.parser")

        def normalize(u):
            """Convert protocol-relative //... URLs to https://..."""
            if u.startswith("//"):
                return "https:" + u
            return u

        # 1. <a href> tags
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

        # 2. <iframe src> — browser streams often here
        for iframe in soup.find_all("iframe", src=True):
            src = normalize(iframe["src"].strip())
            if not src or src in seen:
                continue
            seen.add(src)
            if src.startswith("http") and is_stream_url(src):
                links.append(("Player", src))

        # 3. Webplayer/player URLs in raw page text (JS variables, data attributes)
        for m in PLAYER_URL_RE.finditer(r.text):
            u = m.group(1).rstrip("',;\"\\")
            if u not in seen and is_stream_url(u):
                seen.add(u)
                links.append(("Stream", u))

        # 4. acestream:// in raw text
        for m in re.finditer(r'acestream://([a-f0-9]{40})', r.text):
            ace_url = "acestream://" + m.group(1)
            if ace_url not in seen:
                seen.add(ace_url)
                links.append(("Ace Stream", ace_url))

        # 5. Bare hex hashes in JS (e.g. var hash = "abc123...")
        for m in re.finditer(r'["\']([a-f0-9]{40})["\']', r.text):
            ace_url = "acestream://" + m.group(1)
            if ace_url not in seen:
                seen.add(ace_url)
                links.append(("Ace Stream", ace_url))

    except Exception as e:
        logging.warning(f"event page error {url}: {e}")
    return links

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

def search_streams(team):
    browser_all, ace_all = [], []
    seen = set()

    # livetv.sx: find event pages, then scrape each
    for title, url in find_event_pages(team):
        links = scrape_event_page(title, url)
        for label, href in links:
            if href in seen:
                continue
            seen.add(href)
            if "acestream://" in href or re.match(r'^[a-f0-9]{40}$', href):
                hash_ = href.replace("acestream://", "")
                ace_all.append((title, label, hash_, "livetv.sx"))
            else:
                browser_all.append((title, label, href, "livetv.sx"))

    # rplnews.online
    for match in scrape_rplnews(team):
        b, a = extract_streams(match["links"])
        for item in b:
            if item[1] not in seen:
                seen.add(item[1])
                browser_all.append((match["title"], item[0], item[1], match["source"]))
        for item in a:
            if item[1] not in seen:
                seen.add(item[1])
                ace_all.append((match["title"], item[0], item[1], match["source"]))

    return browser_all, ace_all

def format_response(team, browser, ace):
    if not browser and not ace:
        return (
            f"❌ Трансляции для <b>{team}</b> не найдены.\n\nПопробуйте в день матча или другое написание.",
            None
        )

    lines = [f"📺 <b>{team}</b> — трансляции\n"]

    if browser:
        lines.append("🌐 <b>В браузере:</b>")
        for title, label, url, source in browser[:8]:
            lines.append(f'• <a href="{url}">{label}</a> — <i>{source}</i>')
        lines.append("")

    keyboard = None
    if ace:
        lines.append("⚡ <b>Ace Stream:</b>")
        buttons = []
        for i, (title, label, hash_, source) in enumerate(ace[:8], 1):
            ace_url = f"acestream://{hash_}"
            lines.append(f"• <code>{ace_url}</code> — <i>{source}</i>")
            buttons.append([InlineKeyboardButton(f"▶️ Ace Stream {i}", url=ace_url)])
        keyboard = InlineKeyboardMarkup(buttons)

    return "\n".join(lines), keyboard

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я ищу трансляции футбольных матчей.\n\n"
        "Просто напиши название команды, например:\n"
        "<b>Локомотив</b>\n\n"
        "Или используй команду:\n/find Зенит",
        parse_mode="HTML"
    )

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
    browser, ace = await loop.run_in_executor(None, search_streams, team)
    text, keyboard = format_response(team, browser, ace)
    await msg.edit_text(text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=keyboard)

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("find", find_cmd))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

if __name__ == "__main__":
    app.run_polling()
