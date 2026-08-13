"""Throwaway protocol discovery for the @AniPlusMiniBot source.

Connects with the logged-in session, reads a slice of the public @AniPlus
channel to see how titles and deep links (`t.me/AniPlusMiniBot?start=get_<id>`)
are structured, and probes the bot's /start command to see what it returns.

This is research, not product code: its output (printed JSON-ish lines) tells
us the command scheme the TelegramProvider must implement. Run it only while
reverse-engineering; it can be deleted once the provider works.

    uv run python -m app.anime.telegram_spike <anime_title_optional>
"""

import asyncio
import os
import sys
from pathlib import Path

from telethon import TelegramClient, events
from telethon.sessions import StringSession

API_ID = int(os.getenv("TELEGRAM_API_ID", "0") or "0")
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SESSION_PATH = os.getenv(
    "TELEGRAM_SESSION", str(Path(__file__).resolve().parent.parent.parent / "data" / "animeplus.session")
)
CHANNEL = "AniPlus"  # the public channel that posts anime + deep links
BOT = "AniPlusMiniBot"

SEARCH = sys.argv[1] if len(sys.argv) > 1 else "jujutsu kaisen"


async def main() -> None:
    session_text = Path(SESSION_PATH).read_text(encoding="utf-8").strip()
    client = TelegramClient(StringSession(session_text), API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        print("Not logged in — run telegram_login.py first.")
        await client.disconnect()
        return

    print(f"=== reading recent messages from @{CHANNEL} ===")
    count = 0
    async for msg in client.iter_messages(CHANNEL, limit=40):
        text = (msg.text or "")[:300].replace("\n", " | ")
        links = []
        for ent in msg.entities or []:
            if hasattr(ent, "url"):
                links.append(ent.url)
        if text or links:
            print(f"MSG id={msg.id} media={type(msg.media).__name__ if msg.media else None}")
            print(f"  text: {text}")
            if links:
                print(f"  links: {links}")
            count += 1
            if count >= 8:
                break

    # Find the search title in the channel to get its deep link id.
    print(f"\n=== searching @{CHANNEL} for '{SEARCH}' ===")
    async for msg in client.iter_messages(CHANNEL, search=SEARCH, limit=3):
        text = (msg.text or "")[:200].replace("\n", " | ")
        links = [ent.url for ent in (msg.entities or []) if hasattr(ent, "url")]
        print(f"MATCH id={msg.id} text={text}")
        print(f"  links={links}")

    print(f"\n=== sending /start to @{BOT} ===")
    me = await client.get_me()
    # The bot replies asynchronously; collect replies for a few seconds.
    replies = []

    @client.on(events.NewMessage(from_users=BOT))
    async def on_reply(event):
        text = (event.message.text or "")[:200].replace("\n", " | ")
        media = type(event.message.media).__name__ if event.message.media else None
        replies.append((text, media))
        print(f"REPLY: text={text} media={media}")

    await client.send_message(BOT, "/start")
    await asyncio.sleep(5)

    if not replies:
        print("No reply captured — the bot may need a different entry (try /start get_<id>).")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
