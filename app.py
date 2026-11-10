import spaces
import asyncio
import base64
import hashlib
import html
import logging
import time
import os
import re
from contextlib import asynccontextmanager

import asyncpg
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatMemberStatus
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon import utils
from telethon.errors import SessionPasswordNeededError
from cryptography.fernet import Fernet

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
        part=part.strip().strip("'").strip('"')
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
# GEM SERIES requires only TM SCORE membership; ignore stale extra channel IDs from old Space secrets.
SUBSCRIPTION_CHANNELS=["@tm_score"]
SUBSCRIPTION_CHANNEL=SUBSCRIPTION_CHANNELS[0] if SUBSCRIPTION_CHANNELS else "@tm_score"
DESTINATION_URL=os.getenv("CHANNEL_URL","https://t.me/afsonaishab")
SUBSCRIPTION_URL=os.getenv("SUBSCRIPTION_URL","https://t.me/tm_score")
SPONSOR_BOT_URL=os.getenv("SPONSOR_BOT_URL","").strip()
pool=None
user_client=None
pending_login={}
# Telegram webhook requests are acknowledged quickly; updates are processed by one worker.
update_queue=asyncio.Queue(maxsize=1000)
update_worker_task=None
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

def extract_watch_url(msg):
    """Use the source post's own watch/download URL; never invent a destination."""
    preferred=("тамошо","гирифтан","watch","смотреть","скачать","download")
    try:
        for row in (getattr(msg,"buttons",None) or []):
            for button in row:
                label=(getattr(button,"text","") or "").lower()
                url=getattr(button,"url",None)
                if url and any(word in label for word in preferred):
                    return url
        for entity in (getattr(msg,"entities",None) or []):
            url=getattr(entity,"url",None)
            if url:
                return url
    except Exception:
        log.exception("Could not inspect source watch link")
    return None

def published_caption(msg):
    """Render every imported film post in GEM SERIES house style with its own bot deep-link."""
    raw=(getattr(msg,"message","") or "").strip()
    source_id=int(utils.get_peer_id(getattr(msg,"chat",None))) if getattr(msg,"chat",None) else int(getattr(msg,"peer_id",0) or 0)
    post_id=int(getattr(msg,"id",0) or 0)
    bot_url=f"https://t.me/{BOT_USERNAME}?start=post_{source_id}_{post_id}"

    # Remove source-channel promotional buttons/links and competitor branding.
    cleaned=[]
    for line in raw.splitlines():
        low=line.lower()
        if any(word in low for word in ("тамошо кардан","тамошо /","watch","смотреть","гирифтани кисм","гирифтани қисм")):
            continue
        if "╔" in line or "╚" in line or "════" in line or "────" in line:
            continue
        if "истехсоли:" in low or "истеҳсоли:" in low or "t.me/" in low or "https://" in low:
            continue
        line=re.sub(r"^\s*[✅☑️📢🔊]+\s*","",line).strip()
        if line:
            cleaned.append(line)

    title=""
    details=[]
    tags=[]
    for line in cleaned:
        if line.startswith("#"):
            tags.append(line)
        elif not title and any(ch.isalpha() for ch in line) and not re.search(r"(қисми?|кисми?|серия)\s*\d+",line,re.I):
            title=line
        else:
            details.append(line)

    out=["🎬 <b>GEM SERIES</b>","━━━━━━━━━━━━━━━━"]
    if title:
        out.append("🍿 <b>"+html.escape(title[:100])+"</b>")
    for line in details[:5]:
        low=line.lower()
        if "қисм" in low or "кисм" in low or "серия" in low:
            icon="🎞"
        elif "дубляж" in low or "озвуч" in low or "забон" in low:
            icon="🎙"
        elif "сифат" in low or "quality" in low:
            icon="💎"
        else:
            icon="📌"
        out.append(icon+" "+html.escape(line[:150]))
    if tags:
        out.append(html.escape(" ".join(tags[:3])))
    out.extend([
        "━━━━━━━━━━━━━━━━",
        '▶️ <a href="'+html.escape(bot_url,quote=True)+'"><b>ТАМОШО ДАР GEM SERIES</b></a>',
        '🌐 <a href="'+html.escape(DESTINATION_URL,quote=True)+'">GEM SERIES • @afsonaishab</a>'
    ])
    return "\n".join(out)[:1024]

def join_keyboard(key=None):
    rows=[[InlineKeyboardButton(text="📢 Обуна шудан ба TM SCORE",url=SUBSCRIPTION_URL)]]
    if key:
        rows.append([InlineKeyboardButton(
            text="✅ Санҷидани обуна",
            callback_data=f"check_post:{key[0]}:{key[1]}"
        )])
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

@dp.callback_query(lambda query: query.data == "check_subscription" or (query.data or "").startswith("check_post:"))
async def check_subscription_callback(query: CallbackQuery):
    # Acknowledge the button immediately so Telegram does not keep it spinning.
    await query.answer("Санҷида мешавад...")
    post_key = None
    if (query.data or "").startswith("check_post:"):
        try:
            _, chat_id, message_id = query.data.split(":", 2)
            post_key = (int(chat_id), int(message_id))
        except (TypeError, ValueError):
            await query.answer("Дархост нодуруст аст.", show_alert=True)
            return

    is_subscribed = await subscription_ok(query.from_user.id)
    if not query.message:
        return

    if not is_subscribed:
        await query.message.answer(
            "❌ Шумо ҳоло ба @tm_score обуна нестед. Аввал обуна шавед ва баъд «Санҷидани обуна»-ро пахш кунед.",
            reply_markup=join_keyboard(post_key))
        return

    if post_key:
        row = await pool.fetchrow(
            "SELECT database_message_id FROM gem_posts WHERE source_chat_id=$1 AND source_message_id=$2 AND status='published'",
            *post_key
        )
        if not row or not row["database_message_id"]:
            await query.message.answer(
                "✅ Обуна тасдиқ шуд, аммо ин видео ҳоло дар база дастрас нест. Лутфан баъдтар кӯшиш кунед."
            )
            return
        await bot.copy_message(
            chat_id=query.message.chat.id,
            from_chat_id=DATABASE_CHANNEL,
            message_id=row["database_message_id"]
        )
        return

    await query.message.answer(
        "🎬 Хуш омадед! Обунаи шумо тасдиқ шуд.",
        reply_markup=welcome_keyboard())

def is_actual_video(msg):
    """Accept only a playable video file, not poster photos or ordinary documents."""
    if not msg or not getattr(msg, "media", None):
        return False
    try:
        if getattr(msg, "video", None):
            return True
        file_obj=getattr(msg, "file", None)
        mime=(getattr(file_obj, "mime_type", "") or "").lower()
        if mime.startswith("video/"):
            return True
        document=getattr(msg, "document", None)
        if document:
            from telethon.tl.types import DocumentAttributeVideo
            return any(isinstance(attr, DocumentAttributeVideo)
                       for attr in (getattr(document, "attributes", None) or []))
    except Exception:
        log.exception("Could not inspect source media type")
    return False


def source_bot_usernames(msg):
    """Collect Telegram bot usernames linked by the source post, including inline buttons."""
    found=set()
    text=(getattr(msg, "message", "") or "")
    candidates=[text]
    try:
        for row in (getattr(msg, "buttons", None) or []):
            for button in row:
                candidates.extend([
                    getattr(button, "url", "") or "",
                    getattr(button, "text", "") or ""
                ])
        for entity in (getattr(msg, "entities", None) or []):
            candidates.extend([
                getattr(entity, "url", "") or "",
                getattr(entity, "text", "") or ""
            ])
    except Exception:
        log.exception("Could not inspect source bot links")
    joined=" ".join(candidates).lower()
    for username in ("gemseries_robot", "gemseriesvip_bot"):
        if re.search(r"(?<![a-z0-9_])"+re.escape(username)+r"(?![a-z0-9_])", joined):
            found.add(username)
    return found


def is_free_source_post(msg):
    """Accept playable source posts unless they are explicitly VIP/paid."""
    users=source_bot_usernames(msg)
    if "gemseriesvip_bot" in users:
        return False
    text=(getattr(msg, "message", "") or "").lower()
    paid_markers=(
        "пулаки", "пулӣ", "платный", "платная", "платное",
        "paid", "premium", "vip", "vip only", "обунаи пулак",
        "танҳо пулак", "только платно"
    )
    if any(marker in text for marker in paid_markers):
        return False
    # Some source posts do not contain a free-bot button or username.
    # Do not reject those by default; only explicit VIP/paid posts are excluded.
    return True

async def process_source_message(msg, source_entity, source_id):
    if not msg or getattr(msg,"out",False) or not is_actual_video(msg):
        return False
    if not is_free_source_post(msg):
        log.info("Skipped post not explicitly linked to free bot (or marked VIP): %s/%s", source_id, getattr(msg,"id",None))
        return False
    source_mid=int(msg.id)
    exists=await pool.fetchrow(
      "SELECT status,published_message_id FROM gem_posts WHERE source_chat_id=$1 AND source_message_id=$2",
      source_id,source_mid)
    if exists and exists["status"]=="published":
        if exists["published_message_id"]:
            try:
                await bot.edit_message_caption(
                    chat_id=DESTINATION_CHANNEL,
                    message_id=exists["published_message_id"],
                    caption=published_caption(msg),
                    parse_mode="HTML",
                    reply_markup=None)
            except Exception:
                log.exception("Could not refresh published post caption %s/%s",source_id,source_mid)
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
            message_id=archive_id,
            caption=published_caption(msg),
            parse_mode="HTML",
            reply_markup=None)
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
    """Import exactly one authorized, non-paid video; keep scanning if a candidate fails."""
    source_id=int(utils.get_peer_id(source_entity))
    scanned=0
    videos=0
    skipped_paid=0
    failed=0

    # Scan a wider recent window. A failed/previously archived candidate must
    # not prevent trying the next eligible video.
    async for msg in user_client.iter_messages(source_entity, limit=1000):
        scanned += 1
        if getattr(msg, "out", False) or not is_actual_video(msg):
            continue
        videos += 1
        if not is_free_source_post(msg):
            skipped_paid += 1
            log.info("Test import skipped explicitly paid/VIP video: source=%s message=%s", source_id, msg.id)
            continue

        existing=await pool.fetchrow(
            "SELECT status FROM gem_posts WHERE source_chat_id=$1 AND source_message_id=$2",
            source_id, int(msg.id)
        )
        if existing and existing["status"] == "published":
            log.info("Test import candidate already published; checking next video: %s", msg.id)
            continue

        if await process_source_message(msg, source_entity, source_id):
            log.info(
                "TEST IMPORT SUCCESS: imported exactly one free video source=%s message=%s scanned=%s",
                source_id, msg.id, scanned
            )
            return

        failed += 1
        log.warning("Test import candidate failed; checking next eligible video: %s", msg.id)

    log.warning(
        "TEST IMPORT FOUND NO PUBLISHABLE VIDEO: scanned=%s video_posts=%s paid_skipped=%s failed=%s",
        scanned, videos, skipped_paid, failed
    )

async def start_mtproto_listener():
    global user_client
    if user_client and user_client.is_connected():
        await user_client.disconnect()
    user_client=None
    row=await pool.fetchrow(
      "SELECT session_data FROM gem_listener_sessions WHERE session_name='gem-series-user'")
    if not row:
        log.error("No MTProto session in PostgreSQL. Channel IDs alone cannot authorize reading the source channel; run the one-time local session setup.")
        return
    try:
        session_data=session_cipher().decrypt(row["session_data"].encode("ascii")).decode("utf-8")
    except Exception:
        log.exception("Stored MTProto session could not be decrypted; check TELETHON_SESSION_ENCRYPTION_KEY.")
        return
    user_client=TelegramClient(StringSession(session_data),API_ID,API_HASH)
    await user_client.connect()
    if not await user_client.is_user_authorized():
        log.error("Stored Telegram session is no longer authorized.")
        await user_client.disconnect()
        user_client=None
        return
    source_entity=await user_client.get_entity(SOURCE_CHANNEL)
    user_client.add_event_handler(import_source_message,events.NewMessage(chats=source_entity))
    log.info("MTProto listener connected for source @%s",str(SOURCE_CHANNEL).lstrip("@"))
    # First run: import at most one latest media post for a controlled test.
    if os.getenv("INITIAL_IMPORT_ONE","true").lower() in {"1","true","yes"}:
        await import_one_latest_for_test(source_entity)
    asyncio.create_task(user_client.run_until_disconnected())

async def subscription_ok(user_id):
    """Check membership in the required public channel, including restricted-but-still-member users."""
    for channel in SUBSCRIPTION_CHANNELS:
        try:
            member = await bot.get_chat_member(channel, user_id)
            status = member.status
            is_member = (
                status in {
                    ChatMemberStatus.MEMBER,
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.CREATOR,
                }
                or (
                    status == ChatMemberStatus.RESTRICTED
                    and bool(getattr(member, "is_member", False))
                )
            )
            log.info(
                "Membership result: user=%s channel=%r status=%s is_member=%s",
                user_id, channel, status, getattr(member, "is_member", None)
            )
            if not is_member:
                return False
        except Exception as exc:
            log.exception(
                "Membership API check failed: user=%s channel=%r",
                user_id, channel
            )
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

async def telegram_update_worker():
    while True:
        update=await update_queue.get()
        try:
            await dp.feed_update(bot,update)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Telegram update processing failed: update_id=%s",getattr(update,"update_id",None))
        finally:
            update_queue.task_done()

@asynccontextmanager
async def lifespan(app:FastAPI):
    global pool, update_worker_task
    pool=await asyncpg.create_pool(DATABASE_URL,min_size=1,max_size=5,command_timeout=30,
                                   server_settings={"application_name":"gem-series-bot"})
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)
    update_worker_task=asyncio.create_task(telegram_update_worker(),name="telegram-update-worker")
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
        if update_worker_task:
            update_worker_task.cancel()
            try:
                await update_worker_task
            except asyncio.CancelledError:
                pass
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
            "source":"@"+str(SOURCE_CHANNEL).lstrip("@")}


def owner_key_ok(request: Request):
    import hmac
    expected=os.getenv("TELETHON_LOGIN_ADMIN_KEY","")
    supplied=request.headers.get("x-owner-key","")
    if len(expected)<24 or not supplied or not hmac.compare_digest(expected,supplied):
        raise HTTPException(status_code=403,detail="Owner access denied")

def session_cipher():
    secret=os.getenv("TELETHON_SESSION_ENCRYPTION_KEY","")
    if len(secret)<24:
        raise HTTPException(status_code=503,detail="Session encryption secret is not configured")
    key=base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)

@app.get("/owner",response_class=HTMLResponse)
async def owner_login_page():
    return HTMLResponse("""<!doctype html><html lang="tg"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GEM SERIES — Telegram setup</title><style>
body{font:16px Arial;background:#07110c;color:#eef7ef;margin:0;padding:18px}
main{max-width:480px;margin:25px auto;background:#101d15;padding:20px;border-radius:16px}
input,button{box-sizing:border-box;width:100%;padding:14px;margin:7px 0;border-radius:9px;border:1px solid #45634d;font-size:16px}
input{background:#07110c;color:white}button{background:#28b463;color:#061009;font-weight:bold}
small{color:#b7c9bb}#status{white-space:pre-wrap;margin-top:14px}
</style><main><h2>🎬 GEM SERIES</h2><p>Танзими пайвасти Telegram</p>
<input id="key" type="password" placeholder="Калиди соҳиби бот (Admin Key)" autocomplete="off">
<input id="phone" placeholder="Рақами Telegram, мисол +992..." autocomplete="tel">
<button onclick="sendCode()">1. Фиристодани рамз</button>
<input id="code" placeholder="Рамзи Telegram" autocomplete="one-time-code">
<input id="password" type="password" placeholder="Пароли 2-қадама (агар фаъол бошад)" autocomplete="off">
<button onclick="verify()">2. Тасдиқ ва пайваст кардан</button>
<div id="status">Калид ва рамзи Telegram-ро ба касе нафиристед. Онҳо танҳо дар ҳамин саҳифа ворид карда мешаванд.</div>
</main><script>
const statusBox=document.getElementById('status');
async function call(path,data){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Owner-Key':document.getElementById('key').value},body:JSON.stringify(data)});const j=await r.json();if(!r.ok)throw Error(j.detail||'Хато');return j}
async function sendCode(){try{statusBox.textContent='Дархост фиристода мешавад...';let j=await call('/owner/request-code',{phone:document.getElementById('phone').value});statusBox.textContent=j.message}catch(e){statusBox.textContent='Хато: '+e.message}}
async function verify(){try{statusBox.textContent='Пайваст карда мешавад...';let j=await call('/owner/verify-code',{phone:document.getElementById('phone').value,code:document.getElementById('code').value,password:document.getElementById('password').value});statusBox.textContent=j.message}catch(e){statusBox.textContent='Хато: '+e.message}}
</script></html>""")

@app.post("/owner/request-code")
async def owner_request_code(request:Request):
    owner_key_ok(request)
    data=await request.json()
    phone=str(data.get("phone","")).strip()
    if not re.fullmatch(r"\+[1-9]\d{7,14}",phone):
        raise HTTPException(status_code=400,detail="Рақами телефонро бо формати +992 нависед")
    old=pending_login.pop(phone,None)
    if old:
        try: await old["client"].disconnect()
        except Exception: pass
    client=TelegramClient(StringSession(),API_ID,API_HASH)
    await client.connect()
    try:
        sent=await client.send_code_request(phone)
        pending_login[phone]={"client":client,"phone_code_hash":sent.phone_code_hash,"created":time.monotonic()}
        return {"ok":True,"message":"Рамз ба Telegram фиристода шуд. Рамзро танҳо дар ҳамин саҳифа ворид кунед."}
    except Exception:
        await client.disconnect()
        log.exception("Telegram login code request failed")
        raise HTTPException(status_code=400,detail="Telegram рамз нафиристод. Рақам ва API танзимотро санҷед.")

@app.post("/owner/verify-code")
async def owner_verify_code(request:Request):
    owner_key_ok(request)
    data=await request.json()
    phone=str(data.get("phone","")).strip()
    code=str(data.get("code","")).strip().replace(" ","")
    password=str(data.get("password",""))
    state=pending_login.get(phone)
    if not state or time.monotonic()-state["created"]>600:
        pending_login.pop(phone,None)
        raise HTTPException(status_code=400,detail="Дархости рамз гузаштааст. Рамзи нав гиред.")
    if not code or len(code)>16:
        raise HTTPException(status_code=400,detail="Рамз нодуруст аст.")
    client=state["client"]
    try:
        try:
            if password:
                await client.sign_in(password=password)
            else:
                await client.sign_in(phone=phone,code=code,phone_code_hash=state["phone_code_hash"])
        except SessionPasswordNeededError:
            raise HTTPException(status_code=428,detail="Пароли тасдиқи дуқадамаро дар майдони парол ворид кунед.")
        if not await client.is_user_authorized():
            raise HTTPException(status_code=400,detail="Telegram аккаунтро тасдиқ накард.")
        encrypted=session_cipher().encrypt(client.session.save().encode("utf-8")).decode("ascii")
        await pool.execute("""INSERT INTO gem_listener_sessions(session_name,session_data,updated_at)
          VALUES('gem-series-user',$1,NOW())
          ON CONFLICT(session_name) DO UPDATE SET session_data=EXCLUDED.session_data,updated_at=NOW()""",encrypted)
        await client.disconnect()
        pending_login.pop(phone,None)
        await start_mtproto_listener()
        return {"ok":True,"message":"Telegram пайваст шуд. Ҳоло /health-ро санҷед."}
    except HTTPException:
        raise
    except Exception:
        log.exception("Telegram login verification failed")
        raise HTTPException(status_code=400,detail="Тасдиқ нашуд. Рамзро санҷед ё рамзи нав гиред.")


@app.post("/telegram/webhook")
async def telegram_webhook(request:Request,
 x_telegram_bot_api_secret_token:str|None=Header(default=None)):
    if x_telegram_bot_api_secret_token!=WEBHOOK_SECRET:
        raise HTTPException(status_code=403,detail="Invalid webhook secret")
    try:
        update=Update.model_validate(await request.json(),context={"bot":bot})
        update_queue.put_nowait(update)
    except asyncio.QueueFull:
        log.error("Telegram update queue is full; asking Telegram to retry")
        raise HTTPException(status_code=503,detail="Bot is busy; retry later")
    except Exception:
        log.exception("Invalid Telegram webhook update")
        raise HTTPException(status_code=400,detail="Invalid update")
    return JSONResponse({"ok":True,"queued":True})


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
