import logging
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.enums import ChatMemberStatus
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, Message, Update,
)
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("gem-series")

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
PUBLIC_URL = os.environ["PUBLIC_URL"].rstrip("/")
BOT_USERNAME = os.getenv("BOT_USERNAME", "GEMSERIESTVBOT").lstrip("@")

SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL", "@GEM_SERIES_TJ")
DATABASE_CHANNEL = os.getenv("DATABASE_CHANNEL", "@database_gem_series")
DESTINATION_CHANNEL = os.getenv("DESTINATION_CHANNEL", "@afsonaishab")
SUBSCRIPTION_CHANNEL = os.getenv("SUBSCRIPTION_CHANNEL", "@tm_score")
DESTINATION_URL = os.getenv("CHANNEL_URL", "https://t.me/afsonaishab")
SUBSCRIPTION_URL = os.getenv("SUBSCRIPTION_URL", "https://t.me/tm_score")
SPONSOR_BOT_URL = os.getenv("SPONSOR_BOT_URL", "https://t.me/FF_MARGEDAR_BOT").strip()
DB_PATH = os.getenv("DB_PATH", "/data/gem_series.sqlite3")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


def db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            source_post TEXT PRIMARY KEY,
            source_url TEXT NOT NULL,
            post_date TEXT NOT NULL,
            caption TEXT NOT NULL,
            database_message_id INTEGER,
            published INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def post_key(message: Message) -> str:
    return f"{message.chat.id}:{message.message_id}"


def source_link(message: Message) -> str:
    username = message.chat.username or SOURCE_CHANNEL.lstrip("@")
    return f"https://t.me/{username}/{message.message_id}"


def publication_keyboard(key: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text="▶️ Тамошо / гирифтани қисм",
            url=f"https://t.me/{BOT_USERNAME}?start=post_{key.replace(':', '_')}",
        )],
        [InlineKeyboardButton(text="📢 Канали мо", url=DESTINATION_URL)],
    ]
    if SUBSCRIPTION_URL:
        rows.append([InlineKeyboardButton(text="📢 Обуна ба TM SCORE", url=SUBSCRIPTION_URL)])
    if SPONSOR_BOT_URL:
        rows.append([InlineKeyboardButton(text="🎁 Боти шарик (ихтиёрӣ)", url=SPONSOR_BOT_URL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def join_keyboard(key: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📢 Обуна шудан ба TM SCORE", url=SUBSCRIPTION_URL)],
        [InlineKeyboardButton(
            text="✅ Санҷидани обуна",
            url=f"https://t.me/{BOT_USERNAME}?start=post_{key.replace(':', '_')}",
        )],
    ]
    if SPONSOR_BOT_URL:
        rows.append([InlineKeyboardButton(text="🎁 Боти шарик (ихтиёрӣ)", url=SPONSOR_BOT_URL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def is_source(message: Message) -> bool:
    expected = SOURCE_CHANNEL.lstrip("@").lower()
    actual = (message.chat.username or "").lower()
    return actual == expected


async def import_and_publish(message: Message):
    if not is_source(message):
        return

    key = post_key(message)
    url = source_link(message)
    caption = (message.text or message.caption or "").strip()
    conn = db()
    try:
        row = conn.execute(
            "SELECT database_message_id, published FROM posts WHERE source_post=?",
            (key,),
        ).fetchone()
        if row and row[1]:
            return

        if row and row[0]:
            stored_id = row[0]
        else:
            stored = await bot.copy_message(
                chat_id=DATABASE_CHANNEL,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            stored_id = stored.message_id
            conn.execute(
                """INSERT OR REPLACE INTO posts
                   (source_post, source_url, post_date, caption, database_message_id, published)
                   VALUES (?, ?, ?, ?, ?, 0)""",
                (key, url, message.date.isoformat(), caption, stored_id),
            )
            conn.commit()

        await bot.copy_message(
            chat_id=DESTINATION_CHANNEL,
            from_chat_id=DATABASE_CHANNEL,
            message_id=stored_id,
            reply_markup=publication_keyboard(key),
        )
        conn.execute("UPDATE posts SET published=1 WHERE source_post=?", (key,))
        conn.commit()
        log.info("Imported and published source post %s", key)
    except Exception:
        log.exception("Could not import source post %s", key)
        raise
    finally:
        conn.close()


@dp.channel_post()
async def source_channel_post(message: Message):
    await import_and_publish(message)


async def subscription_ok(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(SUBSCRIPTION_CHANNEL, user_id)
        return member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        }
    except Exception:
        log.exception("Subscription check failed for user %s", user_id)
        return False


@dp.message(CommandStart())
async def start_handler(message: Message):
    args = (message.text or "").split(maxsplit=1)
    payload = args[1].strip() if len(args) > 1 else ""

    if payload.startswith("post_"):
        key = payload[5:].replace("_", ":", 1)
        if not re.fullmatch(r"-?\d+:\d+", key):
            await message.answer("Пайванди нодуруст аст.")
            return

        if not await subscription_ok(message.from_user.id):
            await message.answer(
                "🔒 Барои гирифтани видео аввал ба канали TM SCORE обуна шавед.",
                reply_markup=join_keyboard(key),
            )
            return

        conn = db()
        try:
            row = conn.execute(
                "SELECT database_message_id FROM posts WHERE source_post=? AND published=1",
                (key,),
            ).fetchone()
        finally:
            conn.close()

        if not row:
            await message.answer("Ин видео ҳоло дар база ёфт нашуд.")
            return

        await bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=DATABASE_CHANNEL,
            message_id=row[0],
        )
        return

    await message.answer(
        "🎬 Хуш омадед ба GEM SERIES!\n\n"
        "Қисмҳои нав ба таври автоматӣ ворид ва нашр мешаванд.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Канали GEM SERIES", url=DESTINATION_URL)],
            [InlineKeyboardButton(text="📢 TM SCORE", url=SUBSCRIPTION_URL)],
        ]),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    db().close()
    await bot.set_webhook(
        url=f"{PUBLIC_URL}/telegram/webhook",
        secret_token=WEBHOOK_SECRET,
        allowed_updates=dp.resolve_used_update_types(),
        drop_pending_updates=False,
    )
    log.info("GEM SERIES Bot API channel-post listener started (no Telethon session)")
    try:
        yield
    finally:
        await bot.delete_webhook()
        await bot.session.close()


app = FastAPI(title="GEM SERIES Automation", lifespan=lifespan)


@app.get("/")
async def root():
    return {"service": "GEM SERIES", "mode": "telegram_bot_api_channel_listener"}


@app.get("/health")
async def health():
    conn = db()
    try:
        count = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    finally:
        conn.close()
    return {
        "status": "healthy",
        "mode": "telegram_bot_api_channel_listener",
        "source": SOURCE_CHANNEL,
        "database": DATABASE_CHANNEL,
        "destination": DESTINATION_CHANNEL,
        "posts_seen": count,
        "telethon_session_required": False,
        "requirement": "The bot must be an administrator in the source channel to receive new channel posts.",
    }


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "7860")))
