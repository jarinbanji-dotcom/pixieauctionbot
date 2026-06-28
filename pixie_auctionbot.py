"""
Telegram Price Alert Bot — Per-user alert threshold edition
Commands:
  /start             — subscribe with default threshold (0.019 ETH)
  /setalert <price>  — set your personal alert threshold e.g. /setalert 0.018
  /myalert           — show your current threshold
  /stop              — unsubscribe
  /status            — show subscription + subscriber count
"""

import asyncio
import logging
import json
import os
from datetime import datetime, date

import httpx
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
TELEGRAM_BOT_TOKEN = "8476978843:AAHLo2ho1R5_PXNv_cDRU764cD729sPjsp8"
API_URL            = "https://api.pixiechess.xyz/prices"
# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────


DEFAULT_THRESHOLD   = 0.019    # ETH — used when user first subscribes
DROP_STEP_ETH       = 0.001    # re-alert every further drop of this amount
POLL_INTERVAL_MS    = 5000

SUBSCRIBERS_FILE    = "subscribers.json"

API_HEADERS = {
    "Origin":          "https://www.pixiechess.xyz",
    "Referer":         "https://www.pixiechess.xyz/",
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "Accept":          "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "sec-fetch-dest":  "empty",
    "sec-fetch-mode":  "cors",
    "sec-fetch-site":  "same-site",
}
# ─────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

WEI_PER_ETH = 10 ** 18

# ── Subscriber storage ────────────────────────────────────────────────────────
# Format: { chat_id (int): { "threshold": float } }

def load_subscribers() -> dict[int, dict]:
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE) as f:
                raw = json.load(f)
                # keys are strings in JSON — convert back to int
                return {int(k): v for k, v in raw.items()}
        except Exception:
            pass
    return {}


def save_subscribers(subs: dict[int, dict]):
    with open(SUBSCRIBERS_FILE, "w") as f:
        json.dump({str(k): v for k, v in subs.items()}, f, indent=2)


subscribers: dict[int, dict] = load_subscribers()


# ── Per-user alert state ──────────────────────────────────────────────────────
# Tracks last triggered price per (chat_id, address), resets daily

class UserAlertState:
    def __init__(self):
        # { (chat_id, address): last_trigger_eth }
        self._triggered: dict[tuple, float] = {}
        self._reset_date: date = date.today()

    def _maybe_reset(self):
        today = date.today()
        if today != self._reset_date:
            log.info("New day — resetting all alert state.")
            self._triggered.clear()
            self._reset_date = today

    def should_alert(self, chat_id: int, address: str, current_eth: float, threshold: float) -> bool:
        self._maybe_reset()
        key = (chat_id, address.lower())

        if key not in self._triggered:
            if current_eth <= threshold:
                self._triggered[key] = current_eth
                return True
            return False

        last = self._triggered[key]
        next_trigger = round(last - DROP_STEP_ETH, 6)
        if current_eth <= next_trigger:
            self._triggered[key] = current_eth
            return True
        return False


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in subscribers:
        subscribers[chat_id] = {"threshold": DEFAULT_THRESHOLD}
        save_subscribers(subscribers)
        await update.message.reply_text(
            f"✅ <b>Subscribed!</b>\n\n"
            f"Your alert threshold: <b>{DEFAULT_THRESHOLD} ETH</b>\n"
            f"You'll be re-alerted every <b>{DROP_STEP_ETH} ETH</b> further drop.\n\n"
            f"💡 Change your threshold anytime:\n<code>/setalert 0.018</code>\n\n"
            f"Send /stop to unsubscribe.",
            parse_mode=ParseMode.HTML,
        )
        log.info(f"New subscriber: {chat_id} (threshold={DEFAULT_THRESHOLD})")
    else:
        t = subscribers[chat_id]["threshold"]
        await update.message.reply_text(
            f"You're already subscribed!\n"
            f"Your current threshold: <b>{t} ETH</b>\n"
            f"Use <code>/setalert 0.018</code> to change it.",
            parse_mode=ParseMode.HTML,
        )


async def cmd_setalert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args

    if not args:
        await update.message.reply_text(
            "Usage: <code>/setalert 0.018</code>\n"
            "Sets your personal price alert threshold in ETH.",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        new_threshold = float(args[0])
        if not (0.0001 <= new_threshold <= 10.0):
            raise ValueError("out of range")
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid price. Please enter a number between 0.0001 and 10.\n"
            "Example: <code>/setalert 0.018</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if chat_id not in subscribers:
        subscribers[chat_id] = {}
    subscribers[chat_id]["threshold"] = new_threshold
    save_subscribers(subscribers)

    await update.message.reply_text(
        f"✅ Alert threshold updated!\n\n"
        f"You'll now be notified when price drops to ≤ <b>{new_threshold} ETH</b>\n"
        f"and every <b>{DROP_STEP_ETH} ETH</b> further drop after that.\n\n"
        f"<i>Note: daily state resets at midnight, so tomorrow starts fresh.</i>",
        parse_mode=ParseMode.HTML,
    )
    log.info(f"User {chat_id} set threshold to {new_threshold} ETH")


async def cmd_myalert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in subscribers:
        await update.message.reply_text(
            "You're not subscribed yet. Send /start to subscribe.",
        )
        return
    t = subscribers[chat_id]["threshold"]
    await update.message.reply_text(
        f"🔔 Your current alert threshold: <b>{t} ETH</b>\n"
        f"Re-alert step: <b>{DROP_STEP_ETH} ETH</b>\n\n"
        f"Change it with: <code>/setalert 0.017</code>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in subscribers:
        subscribers.pop(chat_id)
        save_subscribers(subscribers)
        await update.message.reply_text("❌ Unsubscribed. Send /start anytime to re-subscribe.")
        log.info(f"Unsubscribed: {chat_id}")
    else:
        await update.message.reply_text("You're not subscribed. Send /start to subscribe.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in subscribers:
        t = subscribers[chat_id]["threshold"]
        status_text = f"✅ Subscribed — threshold: <b>{t} ETH</b>"
    else:
        status_text = "❌ Not subscribed"
    await update.message.reply_text(
        f"{status_text}\nTotal subscribers: <b>{len(subscribers)}</b>",
        parse_mode=ParseMode.HTML,
    )


# ── Per-user broadcast ────────────────────────────────────────────────────────

async def send_to_user(bot: Bot, chat_id: int, text: str) -> bool:
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
        return True
    except Exception as e:
        log.warning(f"Failed to send to {chat_id}: {e}")
        return False


# ── Price polling loop ────────────────────────────────────────────────────────

async def poll_loop(bot: Bot):
    state = UserAlertState()
    poll_ms = POLL_INTERVAL_MS

    async with httpx.AsyncClient() as client:
        while True:
            try:
                resp = await client.get(API_URL, headers=API_HEADERS, timeout=10)
                if resp.status_code == 202 or not resp.text.strip():
                    log.debug(f"API returned {resp.status_code} with empty body — retrying.")
                    await asyncio.sleep(poll_ms / 1000)
                    continue
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                log.warning(f"API error: {e}")
                await asyncio.sleep(poll_ms / 1000)
                continue

            poll_ms = data.get("pollIntervalMs", poll_ms)

            for item in data.get("vrgda", []):
                address    = item.get("address", "")
                price_wei  = int(item.get("price", 0))
                total_sold = item.get("totalSold", 0)
                max_mints  = item.get("maxMints", 0)
                trend      = item.get("priceTrend", "")
                price_eth  = price_wei / WEI_PER_ETH
                emoji      = "🔻" if trend == "down" else "🔺" if trend == "up" else "➡️"

                dead = []
                for chat_id, user_data in list(subscribers.items()):
                    threshold = user_data.get("threshold", DEFAULT_THRESHOLD)

                    if state.should_alert(chat_id, address, price_eth, threshold):
                        msg = (
                            f"🚨 <b>Price Alert!</b>\n\n"
                            f"<b>Address:</b> <code>{address}</code>\n"
                            f"<b>Price:</b> <b>{price_eth:.6f} ETH</b>  {emoji}\n"
                            f"<b>Your threshold:</b> {threshold} ETH\n"
                            f"<b>Trend:</b> {trend}\n"
                            f"<b>Sold:</b> {total_sold} / {max_mints}\n"
                            f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                        ok = await send_to_user(bot, chat_id, msg)
                        if not ok:
                            dead.append(chat_id)
                        else:
                            log.info(f"Alerted {chat_id} for {address[:8]}… @ {price_eth:.6f} ETH (threshold={threshold})")

                if dead:
                    for chat_id in dead:
                        subscribers.pop(chat_id, None)
                    save_subscribers(subscribers)

            await asyncio.sleep(poll_ms / 1000)


# ── Main ──────────────────────────────────────────────────────────────────────

async def post_init(app: Application):
    asyncio.create_task(poll_loop(app.bot))


def main():
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("setalert", cmd_setalert))
    app.add_handler(CommandHandler("myalert",  cmd_myalert))
    app.add_handler(CommandHandler("stop",     cmd_stop))
    app.add_handler(CommandHandler("status",   cmd_status))

    log.info("Bot running.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()