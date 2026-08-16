# Test VPS — download-engine validation

A small disposable VPS used to validate Unstream's **download core** against
the real internet — the thing hermetic tests cannot cover (a torrent swarm,
actual ffprobe of a real file, provider reachability).

> ⚠️ **Test environment only. NOT production.** Never deploy the full Unstream
> app here, never configure the frontend or a reverse proxy, never expose
> public application ports, never point anything at production.

## Server

| Field    | Value          |
| -------- | -------------- |
| IP       | `168.222.49.86` |
| Username | `root`         |

**Credentials (the root password) are deliberately NOT stored in Git.** They
live only in a session-local, temporary file outside the repository, and are
rotated/discarded with the box. Anyone cloning this repo gets the address and
nothing to log in with — that is intentional.

## Purpose & constraints

- Used only for **controlled, one-at-a-time** real downloads of a small anime
  episode at an explicit resolution (480p), with resource use checked first
  (RAM / disk / CPU / load).
- No parallel heavy operations, no full-season downloads, never a known
  multi-GiB batch (the batch-safety fix is validated hermetically instead).
- A run is aborted immediately if resource pressure becomes unsafe.

## Successful 480p validation

Requested **480p** for **One Piece episode 1100** through the production job
machinery (`jobs.start` → `download_track` → `anime/download_video_track` →
Nyaa strict 480p match → aria2c → ffmpeg mux):

- Nyaa selected `[SubsPlease] One Piece - 1100 (480p) [5880A6EB].mkv` — the
  requested resolution, no fallback.
- Final file (`One Piece - Episode 1100.mp4`) verified with **ffprobe**:
  **h264 848×480** yuv420p @23.976 fps, AAC 44.1 kHz stereo, ~1431 s.
- Job result: status `done`, `provider = "nyaa"`, `served_quality = "480p"` —
  **served == requested (480)**.
- A second run hit a weaker swarm (~180–230 KiB/s, 10–15 peers, 2 seeds;
  ~1413 s elapsed) — confirmed live and progressing throughout, not a stall.
  Nyaa swarm speed varies run to run (same torrent: ~120 KiB/s local,
  ~1.1 MiB/s VPS on the first run).

## Re-running the validation

The driver used on the box lives at `/root/unstream-test/vps_drive.py` and is
copied from this repo's validation work (see `backend/tests/` and the project
state doc for the hermetic equivalent). To reproduce:

1. Ship the current `backend/app` to the box (scp a tarball over the existing
   `/root/unstream-test` install).
2. Check resources first: `free -m`, `df -h /root`, `uptime`.
3. `nohup ./.venv/bin/python vps_drive.py > vps_run.log 2>&1 &`, then poll
   `vps_run.log` for `FINAL` / `STALL:` / `TIMEOUT:` / `Traceback`.
4. ffprobe the produced mp4 and compare against the requested quality.
