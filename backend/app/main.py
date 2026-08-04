"""Unstream API — resolve music URLs and download their tracks.

Run with:  uvicorn app.main:app --reload --port 8000
"""

import io
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import asynccontextmanager
from dataclasses import asdict
from difflib import SequenceMatcher
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import deezer, embed, itunes, jobs, soundcloud, ytdlp
from .models import Collection, ProviderError, SearchResult

# Every provider here is keyless and free — no accounts, no API credentials.
SEARCH_TIMEOUT_SECONDS = 20


def resolve_any(url: str) -> Collection:
    """Route a URL to the right metadata provider.

    Deezer / Apple Music URLs go to their public JSON APIs; YouTube and
    SoundCloud go through yt-dlp. Spotify URLs go through the public embed
    pages — no account or API key is ever required.
    """
    if deezer.is_deezer_url(url):
        return deezer.resolve(url)
    if itunes.is_itunes_url(url):
        return itunes.resolve(url)
    # SoundCloud via its web API first — yt-dlp flat playlists often omit
    # titles, artwork and duration. Fall back to yt-dlp if the API is unreachable.
    if soundcloud.is_soundcloud_url(url):
        try:
            return soundcloud.resolve(url)
        except ProviderError:
            return ytdlp.resolve(url)
    if ytdlp.is_supported_url(url):
        return ytdlp.resolve(url)
    spotify_ref = embed.parse_url(url)
    if spotify_ref:
        return embed.resolve(*spotify_ref)
    raise ProviderError(
        "Unsupported link — paste a Spotify, Deezer, Apple Music, "
        "YouTube or SoundCloud URL, or search by name instead."
    )


def _squash(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for comparing."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def dedup_key(result: SearchResult) -> str:
    """Identity of a result across providers: kind + name + lead artist.

    Sent to the client too, so it can drop cross-page duplicates that no
    single page could have caught on its own.
    """
    artist = _squash(result.subtitle.split("·")[0])
    return f"{result.kind}:{_squash(result.name).replace(' ', '')}:{artist.replace(' ', '')}"


# Catalog APIs carry cleaner metadata than raw user uploads, so an otherwise
# equal match from Deezer outranks the same song ripped to YouTube.
_SOURCE_WEIGHT = {"deezer": 1.0, "itunes": 0.97, "soundcloud": 0.9, "youtube": 0.87}


def relevance(result: SearchResult, query: str) -> float:
    """0–1 score for how well a result answers the query.

    Compared against the name alone *and* against "name + artist", because
    people search both ways ("bohemian rhapsody" and "queen bohemian").
    """
    q = _squash(query)
    name = _squash(result.name)
    both = f"{name} {_squash(result.subtitle)}".strip()

    score = max(
        SequenceMatcher(None, q, name).ratio(),
        SequenceMatcher(None, q, both).ratio(),
    )
    if name == q:
        score = 1.0
    elif name.startswith(q):
        score = max(score, 0.93)
    elif q in name:
        score = max(score, 0.86)
    # Every word of the query appears somewhere in the title or the artist.
    tokens = set(q.split())
    if tokens and tokens <= set(both.split()):
        score = max(score, 0.8)

    return score * _SOURCE_WEIGHT.get(result.source, 0.85)


def search_any(query: str, page: int = 0) -> tuple[list[SearchResult], bool]:
    """Fan out to every free source in parallel and merge one page of results.

    Each provider pages independently (page N asks each for its own Nth
    slice), near-duplicates keep only the higher-quality hit, and what
    survives is ordered by relevance rather than by which provider answered
    first. A slow or failing source never blocks the others.

    Returns the page plus whether it's worth asking for another one.
    """
    def soundcloud_search(q: str, p: int) -> list[SearchResult]:
        try:
            return soundcloud.search(q, p)  # full parity: tracks/people/albums/sets
        except Exception:
            return ytdlp.search_soundcloud(q, p)  # fallback: tracks only

    providers = [deezer.search, itunes.search, soundcloud_search, ytdlp.search_youtube]

    pool = ThreadPoolExecutor(max_workers=len(providers))
    futures = [pool.submit(p, query, page) for p in providers]
    wait(futures, timeout=SEARCH_TIMEOUT_SECONDS)
    # Don't block on stragglers — abandon anything still running.
    pool.shutdown(wait=False, cancel_futures=True)

    merged: list[SearchResult] = []
    seen: set[str] = set()
    errors: list[Exception] = []
    for future in futures:
        if not future.done():
            continue
        if future.exception():
            errors.append(future.exception())
            continue
        for result in future.result():
            key = dedup_key(result)
            if key in seen:
                continue
            seen.add(key)
            merged.append(result)

    if not merged and errors:
        raise ProviderError(f"Search failed: {errors[0]}")

    merged.sort(key=lambda r: relevance(r, query), reverse=True)
    # Nothing came back for this page, so there is nothing deeper either.
    return merged, bool(merged)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    jobs.start_sweeper()
    yield


app = FastAPI(title="Unstream", version="0.0.1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ResolveRequest(BaseModel):
    url: str


class DownloadRequest(BaseModel):
    url: str
    track_ids: list[str] | None = None  # None = everything


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/search")
def search(q: str, page: int = 0) -> dict:
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Empty search query")
    if page < 0:
        raise HTTPException(status_code=400, detail="Bad page number")
    try:
        results, has_more = search_any(q, page)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "results": [asdict(r) | {"dedup_key": dedup_key(r)} for r in results],
        "page": page,
        "has_more": has_more,
    }


@app.get("/api/artist/{artist_id}")
def artist(artist_id: str) -> dict:
    if not artist_id.isdigit():
        raise HTTPException(status_code=400, detail="Bad artist id")
    try:
        data = deezer.artist(artist_id)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Same shape as /api/search results, dedup_key included, so the client
    # can render an artist's tracks and albums with the same components.
    for key in ("top_tracks", "albums"):
        data[key] = [asdict(r) | {"dedup_key": dedup_key(r)} for r in data[key]]
    return data


@app.post("/api/resolve")
def resolve(body: ResolveRequest) -> dict:
    try:
        collection = resolve_any(body.url)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return asdict(collection)


@app.post("/api/download")
def download(body: DownloadRequest) -> dict:
    try:
        collection = resolve_any(body.url)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    tracks = collection.tracks
    if body.track_ids is not None:
        wanted = set(body.track_ids)
        tracks = [t for t in tracks if t.id in wanted]
    if not tracks:
        raise HTTPException(status_code=400, detail="No tracks to download")

    job = jobs.start(collection.name, tracks)
    return {"job_id": job.id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job")
    return job.as_dict()


@app.get("/api/jobs/{job_id}/tracks/{track_id}/file")
def track_file(job_id: str, track_id: str) -> FileResponse:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job")
    state = job.tracks.get(track_id)
    if not state or state.status != "done" or not state.file_path:
        raise HTTPException(status_code=404, detail="Track not ready")
    return FileResponse(
        state.file_path,
        media_type="audio/mpeg",
        filename=state.file_path.name,
    )


@app.get("/api/jobs/{job_id}/zip")
def job_zip(job_id: str) -> Response:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job")
    files = [
        s.file_path
        for s in job.tracks.values()
        if s.status == "done" and s.file_path and s.file_path.exists()
    ]
    if not files:
        raise HTTPException(status_code=404, detail="No completed tracks yet")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
        for path in files:
            zf.write(path, arcname=path.name)
    # HTTP headers are latin-1 only; non-ASCII names (e.g. "PERSIĀDELICĀ")
    # need the RFC 5987 filename* form with an ASCII fallback.
    name = (job.name or "unstream").replace('"', "").replace("\\", "")
    ascii_name = name.encode("ascii", "ignore").decode().strip() or "unstream"
    disposition = (
        f'attachment; filename="{ascii_name}.zip"; '
        f"filename*=UTF-8''{quote(name)}.zip"
    )
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": disposition},
    )
