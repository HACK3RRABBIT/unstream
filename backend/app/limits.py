"""Per-client limits for a public deployment.

No accounts, so the only handle on a caller is their IP. Counters are in
memory, per process — enough for the single-container stack this project ships
(docker-compose.yml); anything scaled out would need a shared store.

Messages stay English so the Telegram bot can share them; the Farsi UI
translates at the API seam (`localizeError` in frontend/src/lib/api.ts).
"""

import ipaddress
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

# Search fans out to four providers for up to 20s — by far the most expensive
# read. Resolve is a single fetch, so it can be looser.
SEARCH_PER_MINUTE = int(os.getenv("RATE_SEARCH_PER_MINUTE", "15"))
RESOLVE_PER_MINUTE = int(os.getenv("RATE_RESOLVE_PER_MINUTE", "30"))
DOWNLOADS_PER_HOUR = int(os.getenv("RATE_DOWNLOADS_PER_HOUR", "20"))

# The 100-track cap is the Telegram bot's, unchanged (docs/telegram-bot-spec.md).
# Its *one active job per user* doesn't port over: the web UI puts a download
# button on every row, so a strict 1 would 429 the second song someone taps.
# Three matches the download pool's worker count in jobs.py.
MAX_ACTIVE_JOBS = int(os.getenv("MAX_ACTIVE_JOBS_PER_CLIENT", "3"))
MAX_TRACKS_PER_JOB = int(os.getenv("MAX_TRACKS_PER_JOB", "100"))


def client_ip(request: Request) -> str:
    """Best guess at the real caller behind the proxy chain.

    Each hop *appends* to X-Forwarded-For, so the header reads
    `[spoofed…, ] client, traefik`. Walking right-to-left to the first public
    address skips anything the caller injected: a spoofed entry can only sit to
    the left of an address a proxy actually observed.
    """
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded or not _is_internal(peer):
        # Direct connection — the socket address is all there is to trust.
        return peer

    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    for hop in reversed(hops):
        if not _is_internal(hop):
            return hop
    return hops[0] if hops else peer


def _is_internal(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False  # not an IP at all — treat as untrusted


class RateLimiter:
    """Sliding-window counter: at most `limit` hits per `window` seconds."""

    def __init__(self, limit: int, window_seconds: float, name: str) -> None:
        self.limit = limit
        self.window = window_seconds
        self.name = name
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def take(self, key: str) -> float | None:
        """Record a hit. Returns seconds to wait if the caller is over."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1.0, round(self.window - (now - hits[0]), 1))
            hits.append(now)
            # Or a long-lived process accumulates every IP it has ever seen.
            if len(self._hits) > 4096:
                self._prune(now)
            return None

    def _prune(self, now: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]:
            del self._hits[key]


_SEARCH = RateLimiter(SEARCH_PER_MINUTE, 60, "search")
_RESOLVE = RateLimiter(RESOLVE_PER_MINUTE, 60, "resolve")
_DOWNLOAD = RateLimiter(DOWNLOADS_PER_HOUR, 3600, "download")

_LIMITERS = {"search": _SEARCH, "resolve": _RESOLVE, "download": _DOWNLOAD}

_MESSAGES = {
    "search": "Too many searches — wait {retry}s and try again.",
    "resolve": "Too many links opened — wait {retry}s and try again.",
    "download": "Too many downloads started — wait {retry}s and try again.",
}


def enforce(kind: str, request: Request) -> str:
    """Charge one hit against the caller's budget, or raise 429.

    Returns the client key, which callers reuse for per-client job limits.
    """
    key = client_ip(request)
    retry = _LIMITERS[kind].take(key)
    if retry is not None:
        raise HTTPException(
            status_code=429,
            detail=_MESSAGES[kind].format(retry=int(retry)),
            headers={"Retry-After": str(int(retry))},
        )
    return key
