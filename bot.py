import os, re, logging, asyncio
import requests
import urllib3
urllib3.disable_warnings()
from bs4 import BeautifulSoup
from telegram import Update
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

def scrape_livetv(team):
    results = []
    try:
        r = requests.get("https://livetv.sx/dex/", headers=HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(r.text, "html.parser")
        team_lower = team.lower()

        for row in soup.find_all(["tr", "div", "li"]):
            text = row.get_text(" ", strip=True).lower()
            if team_lower not in text:
                continue
            links = []
            for a in row.find_all("a", href=True):
                href = a["href"]
                if href.startswith("/"):
                    href = "https://livetv.sx" + href
                links.append((a.get_text(strip=True), href))
            if links:
                title = row.get_text(" ", strip=True)[:80]
                results.append({"title": title, "links": links, "source": "livetv.sx"})
    except Exception as e:
        logging.warning(f"livetv error: {e}")
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

def search_streams(team):
    all_results = scrape_livetv(team) + scrape_rplnews(team)
    browser_all, ace_all = [], []
    seen = set()

    for match in all_results:
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
        return f"❌ Трансляции для <b>{team}</b> не найдены.\n\nПопробуйте в день матча или другое написание."

    lines = [f"📺 <b>{team}</b> — трансляции\n"]

    if browser:
        lines.append("🌐 <b>В браузере:</b>")
        for title, label, url, source in browser[:8]:
            lines.append(f'• <a href="{url}">{label}</a> — <i>{source}</i>')
        lines.append("")

    if ace:
        lines.append("⚡ <b>Ace Stream:</b>")
        for title, label, hash_, source in ace[:8]:
            ace_url = f"acestream://{hash_}"
            lines.append(f'• <a href="{ace_url}">{label}</a>\n  <code>{hash_[:20]}…</code> — <i>{source}</i>')

    return "\n".join(lines)

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
    text = format_response(team, browser, ace)
    await msg.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("find", find_cmd))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

if __name__ == "__main__":
    app.run_polling()
