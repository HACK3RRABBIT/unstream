"""Interactive Telegram login — one-time, produces the session the provider uses.

Run from the backend directory:

    uv run python -m app.anime.telegram_login

Set TELEGRAM_API_ID and TELEGRAM_API_HASH in the environment first (from
my.telegram.org). The login shows a QR code in the terminal; scan it with the
Telegram app (Settings → Devices → Scan QR). The session file lands at
TELEGRAM_SESSION (default ./data/animeplus.session) and is what the provider
reads at download time.

In Docker, run as the app user so the session file stays readable by the
server:  docker compose exec --user 1001:1001 api uv run python -m ...
"""

import asyncio
import os
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession

# The same env vars the provider reads, so what this script produces is
# exactly what the server consumes.
API_ID = int(os.getenv("TELEGRAM_API_ID", "0") or "0")
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
# Default mirrors the provider's — a file under ./data, persisted on the
# analytics volume in Docker.
SESSION_PATH = os.getenv(
    "TELEGRAM_SESSION", str(Path(__file__).resolve().parent.parent.parent / "data" / "animeplus.session")
)


async def main() -> None:
    if not API_ID or not API_HASH:
        print("Set TELEGRAM_API_ID and TELEGRAM_API_HASH first (my.telegram.org).")
        raise SystemExit(1)

    session_file = Path(SESSION_PATH)
    session_file.parent.mkdir(parents=True, exist_ok=True)

    # A StringSession in the file keeps the same contract as the provider's
    # session file but stores the auth key as one line.
    client = TelegramClient(StringSession(), API_ID, API_HASH)

    await client.connect()
    if await client.is_user_authorized():
        print("Already logged in — session is valid.")
        await client.disconnect()
        return

    # QR login: render the QR as ASCII so it is scannable from a terminal.
    from qrcode import QRCode
    from qrcode.constants import ERROR_CORRECT_L

    user = await client.qr_login()
    qr = QRCode(border=1, error_correction=ERROR_CORRECT_L)
    qr.add_data(user.url)
    qr.make()
    print("\nScan the QR below with Telegram (Settings → Devices → Scan QR).\n")
    qr.print_ascii(invert=True)
    print("\nWaiting for the phone to confirm… (2 minutes)")
    # qr_login resolves once the phone confirms; give it a generous window.
    try:
        await asyncio.wait_for(user.wait(), timeout=120)
    except asyncio.TimeoutError:
        print("Login timed out after 2 minutes.")
        await client.disconnect()
        raise SystemExit(1)

    # Persist the session as a string (StringSession) so it can be mounted or
    # copied without touching the API id/hash again.
    session_file.write_text(client.session.save(), encoding="utf-8")
    me = await client.get_me()
    print(f"\nLogged in as {me.first_name} — session saved to {session_file}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
