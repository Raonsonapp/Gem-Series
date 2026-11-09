import asyncio
import html
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import gradio as gr
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
)
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from telethon import TelegramClient, events
from telethon.sessions import StringSession

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("gem-series")

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
TELETHON_SESSION = os.environ["TELETHON_SESSION"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
PUBLIC_URL = os.environ["PUBLIC_URL"].rstrip("/")
BOT_USERNAME = os.getenv("BOT_USERNAME", "GEMSERIESTVBOT").lstrip("@")

SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL", "@GEM_SERIES_TJ")
DATABASE_CHANNEL = os.getenv("DATABASE_CHANNEL", "@database_gem_series")
DESTINATION_CHANNEL = os.getenv("DESTINATION_CHANNEL", "@afsonaishab")
SUBSCRIPTION_CHANNEL = os.getenv("SUBSCRIPTION_CHANNEL", "@tm_score")
DESTINATION_URL = os.getenv("CHANNEL_URL", "https://t.me/afsonaishab")
SUBSCRIPTION_URL = os.getenv("SUBSCRIPTION_URL", "https://t.me/tm_score")
SPONSOR_BOT_URL = os.getenv("SPONSOR_BOT_URL", "https://t.me/FF_MARGEDAR_BOT")
INITIAL_SCAN_DATE = os.getenv("INITIAL_SCAN_DATE", "2026-11-09")
LOCAL_TZ = ZoneInfo("Asia/Dushanbe")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
user_client = TelegramClient(StringSession(TELETHON_SESSION), API_ID, API_HASH)
source_entity = None
database_entity = None
destination_entity = None
monitor_task = None


def film_keyboard(message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="▶️ Тамошои филм",
            url=f"https://t.me/{BOT_USERNAME}?start=film_{message_id}",
        )],
        [InlineKeyboardButton(text="📢 Канали мо", url=DESTINATION_URL)],
    ])


def join_keyboard(message_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📢 Обуна шудан ба TM SCORE", url=SUBSCRIPTION_URL)],
        [InlineKeyboardButton(
            text="✅ Санҷидани обуна",
            url=f"https://t.me/{BOT_USERNAME}?start=film_{message_id}",
        )],
    ]
    if SPONSOR_BOT_URL:
        rows.append([InlineKeyboardButton(text="🎁 Боти шарик (ихтиёрӣ)", url=SPONSOR_BOT_URL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def marker_for(source_id: int, source_message_id: int) -> str:
    return f"GEM_SERIES_ARCHIVE:{source_id}:{source_message_id}"


async def was_archived(marker: str) -> bool:
    async for item in user_client.iter_messages(database_entity, limit=500):
        if item.message and marker in item.message:
            return True
    return False


async def archive_source_message(message):
    marker = marker_for(message.chat_id, message.id)
    if await was_archived(marker):
        return None

    archived = await user_client.forward_messages(
        entity=database_entity,
        messages=message,
        from_peer=source_entity,
    )
    if isinstance(archived, list):
        archived = archived[0] if archived else None
    if archived is None:
        return None

    await user_client.send_message(database_entity, marker)
    return archived.id


async def publish_banner(source_message, database_message_id: int):
    caption = (source_message.message or "").strip()
    if len(caption) > 700:
        caption = caption[:697] + "..."
    safe_caption = html.escape(caption)
    text = (
        "🎬 <b>GEM SERIES</b>\n\n"
        f"{safe_caption + chr(10) + chr(10) if safe_caption else ''}"
        "Барои гирифтани мавод тугмаи зерро пахш кунед.\n"
        "🔒 Пеш аз фиристодан обуна ба канали TM SCORE санҷида мешавад."
    )
    await bot.send_message(
        chat_id=destination_entity,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=film_keyboard(database_message_id),
    )


async def process_source_message(message):
    if not (message.video or message.document or message.photo):
        return
    try:
        db_message_id = await archive_source_message(message)
        if db_message_id is None:
            return
        await publish_banner(message, db_message_id)
        log.info("Archived and published source message %s", message.id)
    except Exception:
        log.exception("Could not process source message %s", message.id)


async def initial_history_scan():
    target = datetime.strptime(INITIAL_SCAN_DATE, "%Y-%m-%d").date()
    start = datetime.combine(target, time.min, tzinfo=LOCAL_TZ)
    end = start + timedelta(days=1)
    log.info("Starting one-item historical scan for %s", target.isoformat())

    async for message in user_client.iter_messages(source_entity, limit=5000):
        stamp = message.date.astimezone(LOCAL_TZ)
        if stamp < start:
            break
        if start <= stamp < end and (message.video or message.document):
            await process_source_message(message)
            log.info("Historical one-item test completed using message %s", message.id)
            return
    log.warning("No video/document post found for %s", target.isoformat())


async def monitor_source():
    @user_client.on(events.NewMessage(chats=source_entity))
    async def new_source_post(event):
        await process_source_message(event.message)

    log.info("Live source-channel monitoring is active")
    await user_client.run_until_disconnected()


async def subscription_ok(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(SUBSCRIPTION_CHANNEL, user_id)
        return member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        }
    except Exception:
        log.exception("Could not verify subscription for user %s", user_id)
        return False


@dp.message(CommandStart())
async def start_handler(message: Message):
    args = (message.text or "").split(maxsplit=1)
    payload = args[1].strip() if len(args) > 1 else ""

    if payload.startswith("film_") and payload[5:].isdigit():
        db_message_id = int(payload[5:])
        if not await subscription_ok(message.from_user.id):
            await message.answer(
                "🔒 Барои гирифтани мавод аввал ба канали TM SCORE обуна шавед, "
                "баъд тугмаи «Санҷидани обуна»-ро пахш кунед.",
                reply_markup=join_keyboard(db_message_id),
            )
            return
        try:
            await bot.copy_message(
                chat_id=message.chat.id,
                from_chat_id=DATABASE_CHANNEL,
                message_id=db_message_id,
            )
            await message.answer("✅ Мавод фиристода шуд.")
        except Exception:
            log.exception("Delivery failed for database message %s", db_message_id)
            await message.answer("Ҳоло фиристодани мавод муваффақ нашуд. Баъдтар такрор кунед.")
        return

    await message.answer(
        "🎬 Хуш омадед ба GEM SERIES!\n\n"
        "Барои гирифтани мавод аз тугмаи дохили пости канали мо истифода баред.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Канали GEM SERIES", url=DESTINATION_URL)],
            [InlineKeyboardButton(text="🎁 Боти шарик (ихтиёрӣ)", url=SPONSOR_BOT_URL)],
        ]),
    )


@asynccontextmanager
async def lifespan(api: FastAPI):
    global source_entity, database_entity, destination_entity
    await bot.set_webhook(
        url=f"{PUBLIC_URL}/telegram/webhook",
        secret_token=WEBHOOK_SECRET,
        allowed_updates=dp.resolve_used_update_types(),
        drop_pending_updates=False,
    )
    await user_client.connect()
    if not await user_client.is_user_authorized():
        raise RuntimeError("Telethon session is not authorized. Set a valid TELETHON_SESSION secret.")
    source_entity = await user_client.get_entity(SOURCE_CHANNEL)
    database_entity = await user_client.get_entity(DATABASE_CHANNEL)
    destination_entity = DESTINATION_CHANNEL

    await initial_history_scan()
    asyncio.create_task(monitor_source())
    log.info("GEM SERIES automation started")
    try:
        yield
    finally:
        await bot.delete_webhook()
        await bot.session.close()
        await user_client.disconnect()


api = FastAPI(title="GEM SERIES Automation", lifespan=lifespan)


@api.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "GEM SERIES",
        "source": SOURCE_CHANNEL,
        "database": DATABASE_CHANNEL,
        "destination": DESTINATION_CHANNEL,
        "subscription": SUBSCRIPTION_CHANNEL,
    }


@api.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    payload = await request.json()
    update = Update.model_validate(payload, context={"bot": bot})
    await dp.feed_update(bot, update)
    return JSONResponse({"ok": True})


with gr.Blocks(title="GEM SERIES Automation") as demo:
    gr.Markdown(
        "# 🎬 GEM SERIES — Automation\n"
        "Сканкунии як пости таърихӣ, нигоҳдорӣ дар канали база, "
        "интишори баннер, санҷиши обуна ва мониторинги постҳои нав."
    )
    gr.Markdown(
        f"**Манбаъ:** {SOURCE_CHANNEL}  \n"
        f"**База:** {DATABASE_CHANNEL}  \n"
        f"**Канали нашр:** {DESTINATION_CHANNEL}  \n"
        f"**Санҷиши обуна:** {SUBSCRIPTION_CHANNEL}"
    )

app = gr.mount_gradio_app(api, demo, path="/")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "7860")))
