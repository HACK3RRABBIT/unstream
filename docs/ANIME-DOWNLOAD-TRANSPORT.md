# Anime download transport — root cause and fix on the test VPS

## Symptom

On the test VPS (`185.141.63.167`), anime downloads (Nyaa torrents) stall at
`0%` until cancelled. The job never progresses.

## Root cause (verified live)

The image's torrent client choice is: use **aria2c** if present, else **libtorrent**
(`nyaa._pick_torrent_client`). The Docker image (`backend/Dockerfile`) installs **no
aria2c** — so the app runs libtorrent. libtorrent's tracker announce is UDP; the
host's outbound UDP to *most* public torrent trackers is **blackholed** (sendto
succeeds, no reply):

| Target (UDP)                          | Result                  |
| ------------------------------------- | ----------------------- |
| `tracker.opentrackr.org:1337`         | **OK** (connect reply)  |
| `tracker.openbittorrent.com:6969`     | timeout (blackholed)    |
| `tracker.leechers-paradise.org:6969`  | DNS does not resolve    |
| `tracker.torrent.eu.org:1337`         | timeout                |
| `exodus.desync.com` / `pirateparty.gr` / etc. | timeout           |

So libtorrent cannot discover peers on the swarms Nyaa's magnets announce to, and
stays at `0%` forever (the client has no stall timeout).

Outbound **TCP is fine**: HTTP(S) works from inside the container (nyaa.si 200,
google 301), TCP sockets open to `tracker.opentrackr.org:1337` and others, and a
bulk transfer sustained ~2.8 MB/s. This is **not** a general connectivity problem.

## Why aria2c is the right fix

- The code already has a complete **aria2c path** (`_aria2_download`,
  `_download_batch_episode` with `--select-file`, `.aria2` control-file cleanup,
  `aria2.log` handling) — aria2c is the **preferred** client whenever present.
- aria2 uses **DHT + TCP "ut_metadata" + UDP**, so peers can still be found even
  when *some* UDP trackers are blackholed — it does not depend on any single
  UDP tracker (and `_aria2_download` passes `--enable-dht=true` and multiple
  `_PUBLIC_TRACKERS`).
- Installing it is a one-line addition to the Dockerfile. `TEST_VPS.md` already
  documents a validated, working 480p download over aria2c.
- No downloader architecture change, no provider change, no DRM/auth/paywall
  involvement — this makes the existing authorized workflow reliable on this box.

## What was tried and why it is NOT the fix

- **libtorrent with TCP-only trackers**: openbittorrent/timeout trackers are
  UDP-first; converting the client's announce to TCP-only would depend on
  resolved trackers and still hit the blackholed ones. Not smaller than aria2c.
- **DHT alone in libtorrent**: DHT bootstrap nodes are also UDP.
- **HTTP(S) bulk transport**: already works; not the failure point.

## Fix

Add `aria2` to the Dockerfile's `apt-get install` line, so `_pick_torrent_client`
returns `"aria2c"` and the existing aria2c download path is used on deploy.