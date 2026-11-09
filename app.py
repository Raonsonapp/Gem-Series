import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager

import asyncpg
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatMemberStatus
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from telethon import TelegramClient, events
from telethon.sessions import StringSession

logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log=logging.getLogger("gem-series")

BOT_TOKEN=os.environ["BOT_TOKEN"]
DATABASE_URL=os.environ["DATABASE_URL"]
WEBHOOK_SECRET=os.environ["WEBHOOK_SECRET"]
PUBLIC_URL=os.environ["PUBLIC_URL"].rstrip("/")
API_ID=int(os.environ["TELEGRAM_API_ID"])
API_HASH=os.environ["TELEGRAM_API_HASH"]
BOT_USERNAME=os.getenv("BOT_USERNAME","GEMSERIESTVBOT").lstrip("@")
SOURCE_CHANNEL=os.getenv("SOURCE_CHANNEL","GEM_SERIES_TJ").lstrip("@")
DATABASE_CHANNEL=os.getenv("DATABASE_CHANNEL","@database_gem_series")
DESTINATION_CHANNEL=os.getenv("DESTINATION_CHANNEL","@afsonaishab")
SUBSCRIPTION_CHANNEL=os.getenv("SUBSCRIPTION_CHANNEL","@tm_score")
DESTINATION_URL=os.getenv("CHANNEL_URL","https://t.me/afsonaishab")
SUBSCRIPTION_URL=os.getenv("SUBSCRIPTION_URL","https://t.me/tm_score")
SPONSOR_BOT_URL=os.getenv("SPONSOR_BOT_URL","").strip()
pool=None
user_client=None
bot=Bot(BOT_TOKEN)
dp=Dispatcher()

SCHEMA="""
CREATE TABLE IF NOT EXISTS gem_posts(
 source_chat_id BIGINT NOT NULL,
 source_message_id BIGINT NOT NULL,
 source_date TIMESTAMPTZ,
 caption TEXT NOT NULL DEFAULT '',
 database_message_id BIGINT,
 published_message_id BIGINT,
 status TEXT NOT NULL DEFAULT 'received',
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY(source_chat_id,source_message_id)
);
CREATE TABLE IF NOT EXISTS gem_listener_sessions(
 session_name TEXT PRIMARY KEY,
 session_data TEXT NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS gem_posts_status_idx ON gem_posts(status);
"""

def pub_keyboard(key):
    payload=f"post_{key[0]}_{key[1]}"
    rows=[[InlineKeyboardButton(text="▶️ Тамошо / гирифтани қисм",
                                url=f"https://t.me/{BOT_USERNAME}?start={payload}")],
          [InlineKeyboardButton(text="📢 Канали мо",url=DESTINATION_URL)]]
    if SUBSCRIPTION_URL:
        rows.append([InlineKeyboardButton(text="📢 Обуна ба TM SCORE",url=SUBSCRIPTION_URL)])
    if SPONSOR_BOT_URL:
        rows.append([InlineKeyboardButton(text="🎁 Боти шарик (ихтиёрӣ)",url=SPONSOR_BOT_URL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def join_keyboard(key):
    rows=[[InlineKeyboardButton(text="📢 Обуна шудан ба TM SCORE",url=SUBSCRIPTION_URL)],
          [InlineKeyboardButton(text="✅ Санҷидани обуна",
             url=f"https://t.me/{BOT_USERNAME}?start=post_{key[0]}_{key[1]}")]]
    if SPONSOR_BOT_URL:
        rows.append([InlineKeyboardButton(text="🎁 Боти шарик (ихтиёрӣ)",url=SPONSOR_BOT_URL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def import_source_message(event):
    msg=event.message
    if not msg or msg.out or not msg.media and not msg.message:
        return
    chat=await event.get_chat()
    username=(getattr(chat,"username",None) or "").lower()
    if username != SOURCE_CHANNEL.lower():
        return
    source_id=int(event.chat_id)
    source_mid=int(msg.id)
    exists=await pool.fetchrow(
      "SELECT status FROM gem_posts WHERE source_chat_id=$1 AND source_message_id=$2",
      source_id,source_mid)
    if exists and exists["status"]=="published":
        return
    await pool.execute("""
      INSERT INTO gem_posts(source_chat_id,source_message_id,source_date,caption,status)
      VALUES($1,$2,$3,$4,'processing')
      ON CONFLICT(source_chat_id,source_message_id) DO NOTHING
    """,source_id,source_mid,msg.date,(msg.message or ""))
    try:
        # Copy the authorized source media with the user-account listener into the private archive.
        saved=await user_client.send_file(
            DATABASE_CHANNEL, msg.media, caption=msg.message or "",
            force_document=False, link_preview=False
        ) if msg.media else await user_client.send_message(DATABASE_CHANNEL,msg.message or "")
        archive_id=int(saved.id)
        sent=await bot.copy_message(
            chat_id=DESTINATION_CHANNEL,from_chat_id=DATABASE_CHANNEL,
            message_id=archive_id,reply_markup=pub_keyboard((source_id,source_mid)))
        await pool.execute("""
          UPDATE gem_posts SET database_message_id=$3,published_message_id=$4,status='published'
          WHERE source_chat_id=$1 AND source_message_id=$2
        """,source_id,source_mid,archive_id,sent.message_id)
        log.info("Imported %s/%s",source_id,source_mid)
    except Exception as exc:
        await pool.execute("""
          UPDATE gem_posts SET status='error',caption=$3
          WHERE source_chat_id=$1 AND source_message_id=$2
        """,source_id,source_mid,(msg.message or "")[:3500])
        log.exception("Import failed: %s",exc)

async def start_mtproto_listener():
    global user_client
    row=await pool.fetchrow(
      "SELECT session_data FROM gem_listener_sessions WHERE session_name='gem-series-user'")
    if not row:
        log.error("No MTProto session in PostgreSQL. Run the one-time local session setup script.")
        return
    user_client=TelegramClient(StringSession(row["session_data"]),API_ID,API_HASH)
    await user_client.connect()
    if not await user_client.is_user_authorized():
        log.error("Stored Telegram session is no longer authorized.")
        await user_client.disconnect()
        user_client=None
        return
    user_client.add_event_handler(import_source_message,events.NewMessage(chats=SOURCE_CHANNEL))
    log.info("MTProto listener connected for source @%s",SOURCE_CHANNEL)
    asyncio.create_task(user_client.run_until_disconnected())

async def subscription_ok(user_id):
    try:
        member=await bot.get_chat_member(SUBSCRIPTION_CHANNEL,user_id)
        return member.status in {ChatMemberStatus.MEMBER,ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.CREATOR}
    except Exception:
        log.exception("Membership check failed")
        return False

@dp.message(CommandStart())
async def start_handler(message:Message):
    args=(message.text or "").split(maxsplit=1)
    payload=args[1] if len(args)>1 else ""
    match=re.fullmatch(r"post_(-?\d+)_([0-9]+)",payload)
    if not match:
        await message.answer("🎬 Хуш омадед ба GEM SERIES!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
              [InlineKeyboardButton(text="📢 Канали GEM SERIES",url=DESTINATION_URL)],
              [InlineKeyboardButton(text="📢 TM SCORE",url=SUBSCRIPTION_URL)]]))
        return
    key=(int(match.group(1)),int(match.group(2)))
    if not await subscription_ok(message.from_user.id):
        await message.answer("🔒 Барои гирифтани видео аввал ба TM SCORE обуна шавед.",
                             reply_markup=join_keyboard(key))
        return
    row=await pool.fetchrow("""
      SELECT database_message_id FROM gem_posts
      WHERE source_chat_id=$1 AND source_message_id=$2 AND status='published'
    """,*key)
    if not row or not row["database_message_id"]:
        await message.answer("Ин мавод ҳоло дастрас нест.")
        return
    await bot.copy_message(chat_id=message.chat.id,from_chat_id=DATABASE_CHANNEL,
                           message_id=row["database_message_id"])

@asynccontextmanager
async def lifespan(app:FastAPI):
    global pool
    pool=await asyncpg.create_pool(DATABASE_URL,min_size=1,max_size=5,command_timeout=30,
                                   server_settings={"application_name":"gem-series-bot"})
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)
    await start_mtproto_listener()
    await bot.set_webhook(url=f"{PUBLIC_URL}/telegram/webhook",secret_token=WEBHOOK_SECRET,
                          allowed_updates=dp.resolve_used_update_types(),drop_pending_updates=False)
    log.info("GEM SERIES started; PostgreSQL connected.")
    try:
        yield
    finally:
        if user_client and user_client.is_connected():
            await user_client.disconnect()
        await bot.delete_webhook()
        await bot.session.close()
        if pool:
            await pool.close()

app=FastAPI(title="GEM SERIES Bot",lifespan=lifespan)

@app.get("/")
async def root():
    return {"service":"GEM SERIES","status":"ok"}

@app.get("/health")
async def health():
    if pool is None:
        return {"status":"starting"}
    count=await pool.fetchval("SELECT COUNT(*) FROM gem_posts")
    session=await pool.fetchval("SELECT EXISTS(SELECT 1 FROM gem_listener_sessions WHERE session_name='gem-series-user')")
    return {"status":"healthy","database":"connected","posts":count,
            "mtproto_session_configured":bool(session),
            "listener_connected":bool(user_client and user_client.is_connected()),
            "source":"@"+SOURCE_CHANNEL}

@app.post("/telegram/webhook")
async def telegram_webhook(request:Request,
 x_telegram_bot_api_secret_token:str|None=Header(default=None)):
    if x_telegram_bot_api_secret_token!=WEBHOOK_SECRET:
        raise HTTPException(status_code=403,detail="Invalid webhook secret")
    update=Update.model_validate(await request.json(),context={"bot":bot})
    await dp.feed_update(bot,update)
    return JSONResponse({"ok":True})

if __name__=="__main__":
 import uvicorn
 uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT","7860")))
