import html
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gem-series")

BOT_TOKEN = os.environ["BOT_TOKEN"]
DESTINATION_CHANNEL_ID = int(os.environ["DESTINATION_CHANNEL_ID"])
AUTHORIZED_SOURCE_CHANNEL_IDS = {
    int(value.strip())
    for value in os.getenv("AUTHORIZED_SOURCE_CHANNEL_IDS", "").split(",")
    if value.strip()
}
PUBLIC_URL = os.environ["PUBLIC_URL"].rstrip("/")
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/GEM_SERIES_TJ")
OWNER_IDS = {int(v.strip()) for v in os.getenv("OWNER_IDS", "").split(",") if v.strip()}

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

BOT_USERNAME = os.getenv("BOT_USERNAME", "GEMSERIESTVBOT").lstrip("@")

def welcome_keyboard() -> InlineKeyboardMarkup:
    bot_url = f"https://t.me/{BOT_USERNAME}?start=channel"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Оғози GEM SERIES BOT", url=bot_url)],
        [InlineKeyboardButton(text="📢 Канали мо", url=CHANNEL_URL)],
    ])

@dp.message(CommandStart())
async def start_handler(message: Message) -> None:
    await message.answer(
        "🏥 Салом! Хуш омадед ба GEM SERIES 🤝\n\n"
        "❤️ Дар канали мо маълумот ва эълонҳои филмҳои туркӣ "
        "бо дубляжи тоҷикӣ ва форсӣ ҷойгир мешаванд.\n\n"
        "Барои тамошо ба канали расмии мо гузаред. 🌹",
        reply_markup=welcome_keyboard(),
    )

@dp.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(
        "🤖 GEM SERIES BOT\n"
        "Ин бот барои роҳнамоӣ ба канали расмӣ ва интишори "
        "маводи иҷозатдодашуда истифода мешавад."
    )

@dp.message(Command("status"))
async def status_handler(message: Message) -> None:
    if message.from_user is None or message.from_user.id not in OWNER_IDS:
        await message.answer("Ин фармон танҳо барои идоракунандаи бот аст.")
        return
    await message.answer(
        "✅ Бот фаъол аст.\n"
        f"📥 Каналҳои манбаи иҷозатдодашуда: {len(AUTHORIZED_SOURCE_CHANNEL_IDS)}\n"
        "📤 Канали қабулкунанда танзим шудааст."
    )

def make_caption(original: Optional[str]) -> str:
    body = html.escape((original or "").strip())
    if body:
        return f"🎬 <b>GEM SERIES</b>\n\n{body}"
    return "🎬 <b>GEM SERIES</b>\n\nБарои маълумоти нав ба канали мо ҳамроҳ шавед."

@dp.channel_post()
async def channel_post_handler(message: Message) -> None:
    source_id = message.chat.id
    if source_id not in AUTHORIZED_SOURCE_CHANNEL_IDS:
        log.info("Ignored post from non-authorized source channel %s", source_id)
        return
    try:
        caption = make_caption(message.caption or message.text)
        if message.text:
            await bot.send_message(
                chat_id=DESTINATION_CHANNEL_ID,
                text=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=welcome_keyboard(),
            )
        else:
            await bot.copy_message(
                chat_id=DESTINATION_CHANNEL_ID,
                from_chat_id=source_id,
                message_id=message.message_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=welcome_keyboard(),
            )
        log.info("Published authorized source post %s/%s", source_id, message.message_id)
    except Exception:
        log.exception("Could not publish source post %s/%s", source_id, message.message_id)

@asynccontextmanager
async def lifespan(app: FastAPI):
    webhook_url = f"{PUBLIC_URL}/telegram/webhook"
    await bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET,
        allowed_updates=dp.resolve_used_update_types(),
        drop_pending_updates=False,
    )
    log.info("Telegram webhook configured")
    yield
    await bot.delete_webhook()
    await bot.session.close()

app = FastAPI(title="GEM SERIES Telegram Bot", lifespan=lifespan)

@app.get("/")
async def root():
    return {"service": "GEM SERIES BOT", "status": "ok"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
):
    if x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    payload = await request.json()
    update = Update.model_validate(payload, context={"bot": bot})
    await dp.feed_update(bot, update)
    return JSONResponse({"ok": True})
