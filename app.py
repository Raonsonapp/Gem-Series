import html
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

import gradio as gr
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
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
CHANNEL_URL = os.environ["CHANNEL_URL"]
BOT_USERNAME = os.getenv("BOT_USERNAME", "GEMSERIESTVBOT").lstrip("@")
OWNER_IDS = {
    int(value.strip())
    for value in os.getenv("OWNER_IDS", "").split(",")
    if value.strip()
}

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


def welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="🤖 Оғози GEM SERIES BOT",
                url=f"https://t.me/{BOT_USERNAME}?start=channel",
            )],
            [InlineKeyboardButton(text="📢 Канали мо", url=CHANNEL_URL)],
        ]
    )


@dp.message(CommandStart())
async def start_handler(message: Message) -> None:
    await message.answer(
        "🏥 Салом! Хуш омадед ба GEM SERIES 🤝\n\n"
        "❤️ Дар канали мо эълонҳо ва маълумоти филмҳои туркӣ "
        "бо дубляжи тоҷикӣ ва форсӣ ҷойгир мешаванд.\n\n"
        "Барои тамошо ба канали расмии мо гузаред. 🌹",
        reply_markup=welcome_keyboard(),
    )


@dp.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(
        "🤖 GEM SERIES BOT\n"
        "Барои тамошо ва навгониҳо ба канали расмии мо гузаред."
    )


@dp.message(Command("status"))
async def status_handler(message: Message) -> None:
    if message.from_user is None or message.from_user.id not in OWNER_IDS:
        await message.answer("Ин фармон танҳо барои идоракунандаи бот аст.")
        return
    await message.answer(
        "✅ Бот фаъол аст.\n"
        f"📥 Манбаъҳои иҷозатдодашуда: {len(AUTHORIZED_SOURCE_CHANNEL_IDS)}\n"
        "📤 Канали қабулкунанда танзим шудааст."
    )


def make_caption(original: Optional[str]) -> str:
    body = html.escape((original or "").strip())
    prefix = "🎬 <b>GEM SERIES</b>\n\n"
    return prefix + body if body else prefix + "Барои маълумоти нав ба канали мо ҳамроҳ шавед."


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
        log.info("Published authorized post %s/%s", source_id, message.message_id)
    except Exception:
        log.exception("Failed publishing post %s/%s", source_id, message.message_id)


@asynccontextmanager
async def lifespan(api: FastAPI):
    await bot.set_webhook(
        url=f"{PUBLIC_URL}/telegram/webhook",
        secret_token=WEBHOOK_SECRET,
        allowed_updates=dp.resolve_used_update_types(),
        drop_pending_updates=False,
    )
    log.info("Telegram webhook configured")
    try:
        yield
    finally:
        await bot.delete_webhook()
        await bot.session.close()


api = FastAPI(title="GEM SERIES Telegram Bot", lifespan=lifespan)


@api.get("/health")
async def health():
    return {"status": "healthy", "service": "GEM SERIES BOT"}


@api.post("/telegram/webhook")
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


with gr.Blocks(title="GEM SERIES BOT") as demo:
    gr.Markdown(
        "# 🎬 GEM SERIES BOT\n"
        "Системаи Telegram барои канали GEM SERIES.\n\n"
        "Ҳолати хизматрасонӣ: **фаъол**, агар ин саҳифа кушода шавад."
    )
    gr.Markdown(
        "Барои кори бот, ҳамаи Secrets-и Hugging Face-ро танзим кунед. "
        "Бот танҳо аз каналҳои манбаъе пост мегирад, ки ID-и онҳо "
        "дар AUTHORIZED_SOURCE_CHANNEL_IDS сабт шудааст."
    )

app = gr.mount_gradio_app(api, demo, path="/")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "7860")))
