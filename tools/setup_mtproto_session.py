"""One-time local setup for GEM SERIES MTProto session.

Run only on your own trusted computer. The Telegram login code is entered locally
and is never sent to this repository or to ChatGPT. The resulting session is
stored in the dedicated GEM SERIES PostgreSQL database.
"""
import asyncio
import getpass
import os

import asyncpg
from telethon import TelegramClient
from telethon.sessions import StringSession

async def main():
    api_id=int(os.environ["TELEGRAM_API_ID"])
    api_hash=os.environ["TELEGRAM_API_HASH"]
    database_url=os.environ["DATABASE_URL"]
    phone=input("Your Telegram phone number (with country code): ").strip()
    client=TelegramClient(StringSession(),api_id,api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            await client.send_code_request(phone)
            code=getpass.getpass("Telegram login code (entered locally): ").strip()
            try:
                await client.sign_in(phone=phone,code=code)
            except Exception as exc:
                if "SessionPasswordNeeded" in type(exc).__name__:
                    password=getpass.getpass("Telegram 2-step verification password: ")
                    await client.sign_in(password=password)
                else:
                    raise
        session=client.session.save()
        pool=await asyncpg.create_pool(database_url,min_size=1,max_size=1)
        try:
            async with pool.acquire() as conn:
                await conn.execute("""
                  CREATE TABLE IF NOT EXISTS gem_listener_sessions(
                    session_name TEXT PRIMARY KEY,
                    session_data TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                  )
                """)
                await conn.execute("""
                  INSERT INTO gem_listener_sessions(session_name,session_data,updated_at)
                  VALUES('gem-series-user',$1,NOW())
                  ON CONFLICT(session_name) DO UPDATE
                  SET session_data=EXCLUDED.session_data,updated_at=NOW()
                """,session)
        finally:
            await pool.close()
        print("GEM SERIES session saved to its PostgreSQL database.")
        print("Do not share the session string or this database credential.")
    finally:
        await client.disconnect()

if __name__=="__main__":
    asyncio.run(main())
