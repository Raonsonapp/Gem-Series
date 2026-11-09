import spaces
import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager

import asyncpg
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatMemberStatus
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon import utils

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
def channel_value(*names, default):
    for name in names:
        value=os.getenv(name,"").strip()
        if value:
            if value.lstrip("-").isdigit():
                return int(value)
            return value if value.startswith("@") else "@"+value
    return default

def channel_id_list(value):
    result=[]
    for part in str(value).replace(";",",").split(","):
        part=part.strip().strip("'\\"")
        if not part:
            continue
        if part.startswith("@"):
            part=part[1:].strip()
        if part.lstrip("-").isdigit():
            result.append(int(part))
        else:
            result.append("@"+part)
    return result

SOURCE_CHANNEL=channel_value("SOURCE_CHANNEL_ID","AUTHORIZED_SOURCE_CHANNEL_ID","SOURCE_CHANNEL",default="@GEM_SERIES_TJ")
DATABASE_CHANNEL=channel_value("DATABASE_CHANNEL_ID","DATABASE_CHANNEL",default="@database_gem_series")
DESTINATION_CHANNEL=channel_value("TARGET_CHANNEL_ID","DESTINATION_CHANNEL_ID","DESTINATION_CHANNEL",default="@afsonaishab")
SUBSCRIPTION_CHANNELS=channel_id_list(os.getenv("SUBSCRIPTION_CHANNEL_IDS") or os.getenv("SUBSCRIPTION_CHANNEL_ID") or os.getenv("SUBSCRIPTION_CHANNEL") or "@tm_score")
SUBSCRIPTION_CHANNEL=SUBSCRIPTION_CHANNELS[0] if SUBSCRIPTION_CHANNELS else "@tm_score"
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

def join_keyboard(key=None):
    rows=[[InlineKeyboardButton(text="📢 Обуна шудан ба TM SCORE",url=SUBSCRIPTION_URL)]]
    if key:
        check_url=f"https://t.me/{BOT_USERNAME}?start=post_{key[0]}_{key[1]}"
        rows.append([InlineKeyboardButton(text="✅ Санҷидани обуна",url=check_url)])
    else:
        rows.append([InlineKeyboardButton(text="✅ Санҷидани обуна",callback_data="check_subscription")])
    if SPONSOR_BOT_URL:
        rows.append([InlineKeyboardButton(text="🎁 Боти шарик (ихтиёрӣ)",url=SPONSOR_BOT_URL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def welcome_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Канали GEM SERIES",url=DESTINATION_URL)],
        [InlineKeyboardButton(text="📢 Обуна ба TM SCORE",url=SUBSCRIPTION_URL)],
        [InlineKeyboardButton(text="✅ Санҷидани обуна",callback_data="check_subscription")]
    ])

@dp.callback_query(lambda query: query.data == "check_subscription")
async def check_subscription_callback(query: CallbackQuery):
    if await subscription_ok(query.from_user.id):
        await query.answer("✅ Обуна тасдиқ шуд! Акнун метавонед аз бот истифода баред.",show_alert=True)
        if query.message:
            await query.message.answer("🎬 Хуш омадед! Обунаи шумо тасдиқ шуд. Вақте ки мавод интихоб мекунед, онро гирифта метавонед.",
                                       reply_markup=welcome_keyboard())
    else:
        await query.answer("❌ Шумо ҳоло ба @tm_score обуна нестед. Аввал обуна шавед ва боз санҷед.",show_alert=True)

async def process_source_message(msg, source_entity, source_id):
    if not msg or getattr(msg,"out",False) or not getattr(msg,"media",None):
        return False
    source_mid=int(msg.id)
    exists=await pool.fetchrow(
      "SELECT status FROM gem_posts WHERE source_chat_id=$1 AND source_message_id=$2",
      source_id,source_mid)
    if exists and exists["status"]=="published":
        return False
    await pool.execute("""
      INSERT INTO gem_posts(source_chat_id,source_message_id,source_date,caption,status)
      VALUES($1,$2,$3,$4,'processing')
      ON CONFLICT(source_chat_id,source_message_id) DO UPDATE
      SET status='processing', caption=EXCLUDED.caption
      WHERE gem_posts.status <> 'published'
    """,source_id,source_mid,msg.date,(msg.message or ""))
    try:
        # Forward the authorized source post into the archive as the signed-in account.
        archived=await user_client.forward_messages(
            entity=DATABASE_CHANNEL, messages=msg, from_peer=source_entity
        )
        if isinstance(archived,list):
            archived=archived[0]
        archive_id=int(archived.id)
        sent=await bot.copy_message(
            chat_id=DESTINATION_CHANNEL,from_chat_id=DATABASE_CHANNEL,
            message_id=archive_id,reply_markup=pub_keyboard((source_id,source_mid)))
        await pool.execute("""
          UPDATE gem_posts SET database_message_id=$3,published_message_id=$4,status='published'
          WHERE source_chat_id=$1 AND source_message_id=$2
        """,source_id,source_mid,archive_id,sent.message_id)
        log.info("Published source post %s/%s",source_id,source_mid)
        return True
    except Exception:
        await pool.execute("""
          UPDATE gem_posts SET status='error'
          WHERE source_chat_id=$1 AND source_message_id=$2 AND status <> 'published'
        """,source_id,source_mid)
        log.exception("Import failed for %s/%s",source_id,source_mid)
        return False

async def import_source_message(event):
    try:
        source_entity=await user_client.get_entity(SOURCE_CHANNEL)
        source_id=int(event.chat_id)
        await process_source_message(event.message,source_entity,source_id)
    except Exception:
        log.exception("Could not process incoming source post")

async def import_one_latest_for_test(source_entity):
    """Import exactly one latest media post as an initial smoke test."""
    source_id=int(utils.get_peer_id(source_entity))
    async for msg in user_client.iter_messages(source_entity,limit=30):
        if getattr(msg,"media",None) and not getattr(msg,"out",False):
            done=await process_source_message(msg,source_entity,source_id)
            if done:
                log.info("Initial one-post test import completed.")
            else:
                log.info("Latest media was already imported or import failed; no duplicate sent.")
            return
    log.warning("No media post found in the latest 30 source messages.")

async def start_mtproto_listener():
    global user_client
    row=await pool.fetchrow(
      "SELECT session_data FROM gem_listener_sessions WHERE session_name='gem-series-user'")
    if not row:
        log.error("No MTProto session in PostgreSQL. Channel IDs alone cannot authorize reading the source channel; run the one-time local session setup.")
        return
    user_client=TelegramClient(StringSession(row["session_data"]),API_ID,API_HASH)
    await user_client.connect()
    if not await user_client.is_user_authorized():
        log.error("Stored Telegram session is no longer authorized.")
        await user_client.disconnect()
        user_client=None
        return
    source_entity=await user_client.get_entity(SOURCE_CHANNEL)
    user_client.add_event_handler(import_source_message,events.NewMessage(chats=source_entity))
    log.info("MTProto listener connected for source @%s",SOURCE_CHANNEL)
    # First run: import at most one latest media post for a controlled test.
    if os.getenv("INITIAL_IMPORT_ONE","true").lower() in {"1","true","yes"}:
        await import_one_latest_for_test(source_entity)
    asyncio.create_task(user_client.run_until_disconnected())

async def subscription_ok(user_id):
    required={ChatMemberStatus.MEMBER,ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.CREATOR}
    for channel in SUBSCRIPTION_CHANNELS:
        try:
            member=await bot.get_chat_member(channel,user_id)
            if member.status not in required:
                return False
        except Exception as exc:
            log.error("Membership check failed for channel=%r user=%s: %s",channel,user_id,exc)
            return False
    return True

@dp.message(CommandStart())
async def start_handler(message:Message):
    args=(message.text or "").split(maxsplit=1)
    payload=args[1] if len(args)>1 else ""
    match=re.fullmatch(r"post_(-?\d+)_([0-9]+)",payload)
    if not match:
        if await subscription_ok(message.from_user.id):
            await message.answer("🎬 Хуш омадед ба GEM SERIES! ✅ Обунаи шумо тасдиқ шуд.",
                                 reply_markup=welcome_keyboard())
        else:
            await message.answer("🎬 Хуш омадед ба GEM SERIES! Барои истифода аввал ба канали TM SCORE обуна шавед, баъд «Санҷидани обуна»-ро пахш кунед.",
                                 reply_markup=join_keyboard())
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
    webhook_info=await bot.get_webhook_info()
    target_webhook=f"{PUBLIC_URL}/telegram/webhook"
    if webhook_info.url != target_webhook:
        await bot.set_webhook(url=target_webhook,secret_token=WEBHOOK_SECRET,
                              allowed_updates=dp.resolve_used_update_types(),drop_pending_updates=False)
    log.info("Telegram webhook ready: %s", target_webhook)
    log.info("Channel config: source=%r archive=%r target=%r subscription_channels=%r",
             SOURCE_CHANNEL,DATABASE_CHANNEL,DESTINATION_CHANNEL,SUBSCRIPTION_CHANNELS)
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

@app.get("/api/status")
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


# Hugging Face ZeroGPU requires the Gradio SDK. Mount the Gradio interface
# onto the existing FastAPI application so the Telegram webhook remains available.
import gradio as gr

@spaces.GPU(duration=30)
def zerogpu_probe():
    # Minimal registered ZeroGPU handler; this bot itself does not need GPU compute.
    return "ZeroGPU handler registered. GEM SERIES uses CPU for Telegram tasks."

async def gradio_status():
    try:
        data = await health()
        return "\n".join(f"- **{k}:** {v}" for k, v in data.items())
    except Exception as exc:
        return f"Status check failed: {type(exc).__name__}: {exc}"

with gr.Blocks(title="GEM SERIES") as demo:
    gr.Markdown("# 🎬 GEM SERIES\nПанели санҷиши бот")
    status_box = gr.Markdown("Барои дидани ҳолати сервер тугмаро пахш кунед.")
    refresh = gr.Button("🔄 Санҷидани сервер", variant="primary")
    refresh.click(fn=gradio_status, outputs=status_box)
    gpu_check = gr.Button("Санҷиши ZeroGPU")
    gpu_result = gr.Markdown()
    gpu_check.click(fn=zerogpu_probe, outputs=gpu_result)
    demo.load(fn=gradio_status, outputs=status_box)

app = gr.mount_gradio_app(app, demo, path="/")

def report_zerogpu_startup():
    """Report registered @spaces.GPU handlers when using a mounted Gradio app."""
    try:
        from spaces.zero import startup as zero_startup
        zero_startup()
        log.info("ZeroGPU startup report sent.")
    except ImportError:
        log.info("Non-ZeroGPU runtime; skipping ZeroGPU startup report.")

if __name__=="__main__":
 report_zerogpu_startup()
 import uvicorn
 uvicorn.run(app,host="0.0.0.0",port=7860)
