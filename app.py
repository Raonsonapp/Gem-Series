import asyncio
import html
import logging
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import gradio as gr
import httpx
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gem-series")

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
PUBLIC_URL = os.environ["PUBLIC_URL"].rstrip("/")
BOT_USERNAME = os.getenv("BOT_USERNAME", "GEMSERIESTVBOT").lstrip("@")

SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL", "@GEM_SERIES_TJ").lstrip("@")
DATABASE_CHANNEL = os.getenv("DATABASE_CHANNEL", "@database_gem_series")
DESTINATION_CHANNEL = os.getenv("DESTINATION_CHANNEL", "@afsonaishab")
SUBSCRIPTION_CHANNEL = os.getenv("SUBSCRIPTION_CHANNEL", "@tm_score")
DESTINATION_URL = os.getenv("CHANNEL_URL", "https://t.me/afsonaishab")
SUBSCRIPTION_URL = os.getenv("SUBSCRIPTION_URL", "https://t.me/tm_score")
SPONSOR_BOT_URL = os.getenv("SPONSOR_BOT_URL", "https://t.me/FF_MARGEDAR_BOT")
INITIAL_SCAN_DATE = os.getenv("INITIAL_SCAN_DATE", "2026-11-09")
POLL_SECONDS = max(60, int(os.getenv("POLL_SECONDS", "180")))
DB_PATH = os.getenv("DB_PATH", "/data/gem_series.sqlite3")
LOCAL_TZ = ZoneInfo("Asia/Dushanbe")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
http = httpx.AsyncClient(timeout=25, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; GEMSeriesBot/1.0)"})
poll_task = None


def db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS posts (
        source_post TEXT PRIMARY KEY, source_url TEXT NOT NULL,
        post_date TEXT NOT NULL, caption TEXT NOT NULL,
        database_message_id INTEGER, published INTEGER NOT NULL DEFAULT 0
    )""")
    conn.commit()
    return conn


def source_url(post_id: str) -> str:
    return f"https://t.me/{SOURCE_CHANNEL}/{post_id}"


def post_keyboard(post_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Кушодани пости аслӣ", url=source_url(post_id))],
        [InlineKeyboardButton(text="📢 Канали мо", url=DESTINATION_URL)],
    ])


def join_keyboard(post_id: str):
    rows = [
        [InlineKeyboardButton(text="📢 Обуна шудан ба TM SCORE", url=SUBSCRIPTION_URL)],
        [InlineKeyboardButton(text="✅ Санҷидани обуна",
                              url=f"https://t.me/{BOT_USERNAME}?start=post_{post_id}")],
    ]
    if SPONSOR_BOT_URL:
        rows.append([InlineKeyboardButton(text="🎁 Боти шарик (ихтиёрӣ)", url=SPONSOR_BOT_URL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def scrape_public_posts():
    url = f"https://t.me/s/{SOURCE_CHANNEL}"
    response = await http.get(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    posts = []
    for node in soup.select(".tgme_widget_message_wrap"):
        message = node.select_one(".tgme_widget_message")
        if not message:
            continue
        data_post = message.get("data-post", "")
        match = re.search(r"/(\\d+)$", data_post)
        if not match:
            continue
        post_id = match.group(1)
        date_node = node.select_one("time[datetime]")
        date_text = date_node.get("datetime", "") if date_node else ""
        text_node = node.select_one(".tgme_widget_message_text")
        caption = text_node.get_text("\\n", strip=True) if text_node else ""
        has_media = bool(node.select_one(".tgme_widget_message_photo, .tgme_widget_message_video, .tgme_widget_message_document"))
        posts.append({"id": post_id, "date": date_text, "caption": caption, "has_media": has_media})
    return posts


async def save_and_publish(post, historical=False):
    post_id = post["id"]
    url = source_url(post_id)
    caption = (post["caption"] or "").strip()
    conn = db()
    try:
        existing = conn.execute("SELECT published FROM posts WHERE source_post=?", (post_id,)).fetchone()
        if existing and existing[0]:
            return
        conn.execute("""INSERT OR IGNORE INTO posts
            (source_post, source_url, post_date, caption, published)
            VALUES (?, ?, ?, ?, 0)""",
            (post_id, url, post["date"], caption))
        conn.commit()

        record_text = (
            "🎬 GEM SERIES — сабти пости манбаъ\\n\\n"
            + (caption[:2500] + "\\n\\n" if caption else "")
            + f"Пости аслӣ: {url}\\n"
            + "Эзоҳ: ин сабт танҳо маълумот ва пайванди манбаъро нигоҳ медорад; "
              "худи видеоро аз канали дигар нусхабардорӣ намекунад."
        )
        stored = await bot.send_message(DATABASE_CHANNEL, record_text, disable_web_page_preview=True)
        conn.execute("UPDATE posts SET database_message_id=? WHERE source_post=?",
                     (stored.message_id, post_id))
        conn.commit()

        banner = (
            "🎬 <b>GEM SERIES</b>\\n\\n"
            + (html.escape(caption[:650]) + "\\n\\n" if caption else "")
            + "Пости нав дар манбаъ пайдо шуд. Барои кушодан тугмаро пахш кунед."
        )
        await bot.send_message(DESTINATION_CHANNEL, banner, parse_mode=ParseMode.HTML,
                               reply_markup=post_keyboard(post_id))
        conn.execute("UPDATE posts SET published=1 WHERE source_post=?", (post_id,))
        conn.commit()
        log.info("Published public post %s (historical=%s)", post_id, historical)
    finally:
        conn.close()


async def scan_public_channel():
    try:
        posts = await scrape_public_posts()
        if not posts:
            log.warning("No public posts found; channel may be private or page markup changed")
            return
        target = datetime.strptime(INITIAL_SCAN_DATE, "%Y-%m-%d").date()
        selected = None
        for post in posts:
            try:
                stamp = datetime.fromisoformat(post["date"].replace("Z", "+00:00")).astimezone(LOCAL_TZ)
            except Exception:
                continue
            if stamp.date() == target and post["has_media"]:
                selected = post
                break
        if selected:
            await save_and_publish(selected, historical=True)
        else:
            log.info("No media post for initial test date %s in visible public page", target)
        for post in reversed(posts):
            await save_and_publish(post)
    except Exception:
        log.exception("Public channel scan failed")


async def polling_loop():
    while True:
        await scan_public_channel()
        await asyncio.sleep(POLL_SECONDS)


async def subscription_ok(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(SUBSCRIPTION_CHANNEL, user_id)
        return member.status in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
                                 ChatMemberStatus.CREATOR}
    except Exception:
        log.exception("Subscription check failed for user %s", user_id)
        return False


@dp.message(CommandStart())
async def start_handler(message: Message):
    args = (message.text or "").split(maxsplit=1)
    payload = args[1].strip() if len(args) > 1 else ""
    if payload.startswith("post_") and payload[5:].isdigit():
        post_id = payload[5:]
        if not await subscription_ok(message.from_user.id):
            await message.answer("🔒 Аввал ба канали TM SCORE обуна шавед ва баъд санҷишро пахш кунед.",
                                 reply_markup=join_keyboard(post_id))
            return
        await message.answer("✅ Обуна тасдиқ шуд. Пости аслӣ аз ин ҷо дастрас аст:",
                             reply_markup=post_keyboard(post_id))
        return
    await message.answer(
        "🎬 Хуш омадед ба GEM SERIES!\\n\\n"
        "Постҳои нав аз саҳифаи оммавии манбаъ санҷида мешаванд. "
        "Барои гирифтани пайванд аз тугмаи дохили канали мо истифода баред.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Канали GEM SERIES", url=DESTINATION_URL)],
            [InlineKeyboardButton(text="🎁 Боти шарик (ихтиёрӣ)", url=SPONSOR_BOT_URL)],
        ]))


@asynccontextmanager
async def lifespan(api: FastAPI):
    global poll_task
    await bot.set_webhook(url=f"{PUBLIC_URL}/telegram/webhook",
                          secret_token=WEBHOOK_SECRET,
                          allowed_updates=dp.resolve_used_update_types(),
                          drop_pending_updates=False)
    db().close()
    await scan_public_channel()
    poll_task = asyncio.create_task(polling_loop())
    log.info("GEM SERIES started in sessionless public-channel mode")
    try:
        yield
    finally:
        if poll_task:
            poll_task.cancel()
        await bot.delete_webhook()
        await bot.session.close()
        await http.aclose()


api = FastAPI(title="GEM SERIES Automation", lifespan=lifespan)


@api.get("/health")
async def health():
    conn = db()
    try:
        count = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    finally:
        conn.close()
    return {"status": "healthy", "mode": "public_page_polling",
            "source": f"@{SOURCE_CHANNEL}", "posts_seen": count,
            "poll_seconds": POLL_SECONDS,
            "note": "Stores post metadata and source links, not third-party video files."}


@api.post("/telegram/webhook")
async def telegram_webhook(request: Request,
                           x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return JSONResponse({"ok": True})


with gr.Blocks(title="GEM SERIES Automation") as demo:
    gr.Markdown("# 🎬 GEM SERIES — Sessionless Automation")
    gr.Markdown(
        f"**Манбаъ:** @{SOURCE_CHANNEL}  \n"
        f"**База:** {DATABASE_CHANNEL}  \n"
        f"**Канали нашр:** {DESTINATION_CHANNEL}  \n"
        f"**Санҷиши обуна:** {SUBSCRIPTION_CHANNEL}  \n"
        "**Усул:** хондани саҳифаи оммавӣ; нигоҳдории маълумот ва пайванди пост. "
        "Худи видеои канал нусхабардорӣ намешавад."
    )

app = gr.mount_gradio_app(api, demo, path="/")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "7860")))
