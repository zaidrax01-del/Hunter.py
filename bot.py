import re
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import logging

# ---------- CONFIG ----------
TELEGRAM_TOKEN = "YOUR_BOT_TOKEN"
CHECK_INTERVAL = 300  # 5 minutes
MIN_MC = 5000
MAX_MC = 500000
AGE_HOURS = 24

logging.basicConfig(level=logging.INFO)

# ---------- SCRAPING ENGINE ----------
def fetch_new_pairs(chain: str):
    url = f"https://dexscreener.com/new-pairs?chain={chain}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logging.error(f"Request failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.select("table tbody tr")
    found = []

    for row in rows:
        row_text = row.get_text(" ", strip=True)

        # ---- Extract Market Cap ----
        mc_match = re.search(r'\$(\d+\.?\d*)([kmb]?)', row_text, re.I)
        if not mc_match:
            continue
        num, unit = float(mc_match.group(1)), mc_match.group(2).lower()
        if unit == "k":
            mc = num * 1_000
        elif unit == "m":
            mc = num * 1_000_000
        elif unit == "b":
            mc = num * 1_000_000_000
        else:
            mc = num

        if not (MIN_MC <= mc <= MAX_MC):
            continue

        # ---- Social Links ----
        tg_link = None
        has_twitter = False
        has_website = False

        for a in row.find_all("a", href=True):
            href = a["href"]
            if "t.me" in href:
                tg_link = href
            elif "twitter.com" in href:
                has_twitter = True
            elif href.startswith("http") and not ("t.me" in href or "twitter.com" in href):
                has_website = True

        # Condition: must have TG + Twitter, but NO website
        if not (tg_link and has_twitter) or has_website:
            continue

        # ---- Launch Age ----
        age_match = re.search(r'(\d+)\s+(min|hour|day)s?\s+ago', row_text, re.I)
        if age_match:
            num = int(age_match.group(1))
            unit = age_match.group(2).lower()
            if unit == "min":
                age_sec = num * 60
            elif unit == "hour":
                age_sec = num * 3600
            elif unit == "day":
                age_sec = num * 86400
            else:
                continue
            if age_sec > AGE_HOURS * 3600:
                continue
        else:
            # If we can't parse age, skip to be safe (or set to True if you want to keep)
            continue

        found.append({
            "name": row_text[:30],  # just a snippet for log
            "tg": tg_link,
            "mc": mc,
        })

    return found

# ---------- BOT HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "chain" not in context.bot_data:
        context.bot_data["chain"] = "solana"
    if "seen" not in context.bot_data:
        context.bot_data["seen"] = set()

    await update.message.reply_text(
        "🤖 Meme Hunter active!\n\n"
        "Commands:\n"
        "/setchain <chain> – e.g. /setchain base, /setchain bsc, /setchain arbitrum\n"
        "/status – show current chain & filters\n"
        "/scannow – manually trigger a scan\n\n"
        f"Current chain: {context.bot_data['chain']}"
    )

async def set_chain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /setchain <chain> (e.g. solana, ethereum, base, ton)")
        return
    new_chain = context.args[0].lower()
    context.bot_data["chain"] = new_chain
    context.bot_data["seen"] = set()  # reset seen list for new chain
    await update.message.reply_text(f"✅ Chain switched to **{new_chain}**")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chain = context.bot_data.get("chain", "solana")
    await update.message.reply_text(
        f"📊 **Current Status**\n"
        f"Chain: `{chain}`\n"
        f"MC range: ${MIN_MC:,} – ${MAX_MC:,}\n"
        f"Max age: {AGE_HOURS}h\n"
        f"Interval: {CHECK_INTERVAL}s"
    )

async def scan_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Scanning...")
    await scan_and_send(context)

# ---------- CORE SCAN JOB ----------
async def scan_and_send(context: ContextTypes.DEFAULT_TYPE):
    chain = context.bot_data.get("chain", "solana")
    seen = context.bot_data.get("seen", set())

    pairs = fetch_new_pairs(chain)
    new_pairs = [p for p in pairs if p["tg"] not in seen]

    if not new_pairs:
        return

    for pair in new_pairs:
        # Send ONLY the Telegram link (as requested)
        await context.bot.send_message(
            chat_id=context.bot_data["chat_id"],
            text=pair["tg"]
        )
        seen.add(pair["tg"])
        await asyncio.sleep(0.5)  # avoid flood

    context.bot_data["seen"] = seen

# ---------- MAIN ----------
import asyncio

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Store chat_id globally so the job can send messages
    async def store_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.bot_data["chat_id"] = update.effective_chat.id

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setchain", set_chain))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("scannow", scan_now))
    app.add_handler(CommandHandler("store", store_chat_id))  # internal

    # Schedule the scan every CHECK_INTERVAL seconds
    job_queue = app.job_queue
    job_queue.run_repeating(scan_and_send, interval=CHECK_INTERVAL, first=10)

    # Start polling
    app.run_polling()

if __name__ == "__main__":
    main()
