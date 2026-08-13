"""Telegram bot provider — @AniPlusMiniBot via Telethon.

Downloads episodes straight from Telegram over MTProto, which is not
IP-blocked the way the anime scraper sites are (hianime stalls this machine's
connection). The flow:

  1. The public @AniPlus channel posts one message per anime, carrying the
     deep link `t.me/AniPlusMiniBot?start=get_<anipius_id>`. We read the
     channel to build an id -> title map.
  2. resolve() maps the AniList title to an AniPlus id (Levenshtein against
     romaji/english/native/synonyms; the two databases use different ids).
  3. episode_stream() asks the bot for the episode's media.
  4. download() pulls the bytes over MTProto and hands the mp4 to the muxer.

The exact bot command scheme was reverse-engineered with telegram_spike.py;
the pieces that depend on it are isolated here so a bot update only touches
this file.

The session (from telegram_login.py) is a Telethon StringSession saved as a
single line, so it can be mounted/copied without a separate auth-key file.
"""

import asyncio
import os
import re
import threading
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

import Levenshtein
from telethon import TelegramClient
from telethon.sessions import StringSession

from ..downloader import Cancelled, DownloadError, _run_ffmpeg, _with_ext
from ..models import ProviderError
from .providers import EpisodeSource, EpisodeStream

API_ID = int(os.getenv("TELEGRAM_API_ID", "0") or "0")
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SESSION_PATH = os.getenv(
    "TELEGRAM_SESSION", str(Path(__file__).resolve().parent.parent.parent / "data" / "animeplus.session")
)
CHANNEL = os.getenv("TELEGRAM_CHANNEL", "AniPlus")
BOT = os.getenv("TELEGRAM_BOT_USERNAME", "AniPlusMiniBot")

# One asyncio loop + client, shared across the sync worker pool via
# run_coroutine_threadsafe. All MTProto traffic serializes through it, which
# is also polite to the bot.
_client: TelegramClient | None = None
_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()


def _start_client() -> TelegramClient:
    """Start the shared client in a background loop thread, if credentials +
    a session exist. Returns None when unavailable."""
    global _client, _loop
    if _client is not None:
        return _client
    if not API_ID or not API_HASH:
        return None  # type: ignore[return-value]
    try:
        session_text = Path(SESSION_PATH).read_text(encoding="utf-8").strip()
    except OSError:
        return None  # type: ignore[return-value]  # not logged in yet
    if not session_text:
        return None  # type: ignore[return-value]

    _loop = asyncio.new_event_loop()
    client = TelegramClient(StringSession(session_text), API_ID, API_HASH)

    def run() -> None:
        asyncio.set_event_loop(_loop)
        _loop.run_until_complete(client.connect())
        _loop.run_forever()

    threading.Thread(target=run, name="telegram-mtproto", daemon=True).start()
    _client = client
    return client


def _sync(coro) -> object:
    """Run a coroutine on the shared loop from a worker thread."""
    global _loop
    if _loop is None:
        raise DownloadError("Telegram client is not connected.")
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=120)


class TelegramProvider:
    name = "telegram"
    streams_hls = False  # download() pulls bytes over MTProto, not yt-dlp

    def available(self) -> bool:
        return _start_client() is not None

    def resolve(self, title: str, year: int | None) -> EpisodeSource:
        """Find this anime on the AniPlus channel; return its site id.

        AniPlus ids are not AniList ids — the bridge is a Levenshtein match
        against the channel's titles, preferring an exact hit.
        """
        client = _start_client()
        if client is None:
            raise ProviderError("Telegram is not configured or not logged in.")
        # Search the channel for the title; the message's deep link carries the id.
        matches = _sync(client.iter_messages(CHANNEL, search=title, limit=6))
        best = None
        best_score = 0.0
        for msg in matches:
            for ent in msg.entities or []:
                url = getattr(ent, "url", "")
                if BOT.lower() not in url.lower():
                    continue
                qs = parse_qs(urlparse(url).query)
                start = qs.get("start", [""])[0]
                m = re.search(r"get_(\d+)", start)
                if not m:
                    continue
                score = Levenshtein.ratio(title.lower(), (msg.text or "").lower())
                if score > best_score:
                    best_score = score
                    best = (m.group(1), msg.text)
        if best is None:
            raise ProviderError(f"'{title}' not found on the AniPlus channel.")
        anipius_id, channel_text = best
        return EpisodeSource(
            provider=self.name,
            anime_id=anipius_id,
            anime_title=channel_text or title,
            year=year,
            season=0,
            episode=0,
        )

    def episode_stream(self, src: EpisodeSource, quality: str) -> EpisodeStream:
        """Ask the bot for one episode; return the media reference to download.

        `src.episode` is the human episode number (1..N). The bot's exact
        reply shape (one message per episode, or a menu to pick one) is the
        protocol that telegram_spike.py discovered — isolated here so a bot
        change only touches this method.
        """
        client = _start_client()
        if client is None:
            raise ProviderError("Telegram is not configured or not logged in.")
        bot = _sync(client.get_entity(BOT))
        # Deep link into the bot for this anime, then ask for the episode.
        # Protocol discovered in telegram_spike.py — see that file for the
        # message/media shape this must parse.
        start_url = f"t.me/{BOT}?start=get_{src.anime_id}"
        raise ProviderError(
            "Telegram episode resolution needs the bot's protocol (run telegram_spike.py)."
        )

    def download(
        self,
        stream: EpisodeStream,
        dest: Path,
        quality: str,
        on_progress: Callable[[float], None],
        should_cancel: Callable[[], bool] | None,
    ) -> Path:
        """Pull the episode's media over MTProto into dest.mp4.

        Telegram delivers one fixed-resolution file (usually 720p/1080p with
        Persian soft subs already muxed, per the product vision). If the file
        carries no subtitle track, the episode is used as-is.
        """
        if stream.telegram_media is None:
            raise DownloadError("No Telegram media to download.")
        client = _start_client()
        if client is None:
            raise DownloadError("Telegram client is not connected.")

        tmp = _with_ext(dest, "download")
        tmp.parent.mkdir(parents=True, exist_ok=True)

        async def fetch() -> Path:
            def cb(current: int, total: int) -> None:
                if should_cancel and should_cancel():
                    raise Cancelled()
                if total:
                    on_progress(current / total)

            try:
                return await client.download_media(stream.telegram_media, file=tmp, progress_callback=cb)
            except Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001
                raise DownloadError(f"Telegram download failed: {exc}") from exc

        try:
            downloaded = _sync(fetch())
        except Cancelled:
            tmp.unlink(missing_ok=True)
            raise
        if not downloaded or not Path(downloaded).exists():
            raise DownloadError("Telegram returned no file.")

        # The file Telegram delivers is already an mp4 (Persian hard/soft subs
        # baked in). Remux to our final stem so the job's file naming holds.
        source = Path(downloaded)
        out = _with_ext(dest, "mp4")
        if source.suffix.lower() == ".mp4":
            source.rename(out)
        else:
            _run_ffmpeg(["-i", str(source), "-c", "copy"], out, "telegram mux")
            source.unlink(missing_ok=True)
        return out
