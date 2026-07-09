"""Unstream API — resolve Spotify URLs and download their tracks.

Run with:  uvicorn app.main:app --reload --port 8000
"""

import io
import zipfile
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from . import deezer, embed, jobs, spotify  # noqa: E402  (needs env loaded first)
from .models import Collection, ProviderError, SearchResult  # noqa: E402


def resolve_any(url: str) -> Collection:
    """Route a URL to the right metadata provider.

    Deezer URLs go to the Deezer public API. Spotify URLs prefer the
    official API when credentials are configured, but fall back to the
    public embed pages — so no Spotify account is ever required.
    """
    if deezer.is_deezer_url(url):
        return deezer.resolve(url)
    kind, spotify_id = spotify.parse_url(url)  # raises if not a Spotify URL
    if spotify.has_credentials():
        try:
            return spotify.resolve(url)
        except spotify.SpotifyError:
            pass  # e.g. Premium-required or rate limit — use the embed page
    return embed.resolve(kind, spotify_id)


def search_any(query: str) -> list[SearchResult]:
    if spotify.has_credentials():
        try:
            return spotify.search(query)
        except spotify.SpotifyError:
            pass
    return deezer.search(query)

app = FastAPI(title="Unstream", version="0.1.0")

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
def search(q: str) -> dict:
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Empty search query")
    try:
        results = search_any(q)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"results": [asdict(r) for r in results]}


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
