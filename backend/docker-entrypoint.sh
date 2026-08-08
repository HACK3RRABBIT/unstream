#!/bin/sh
# Make the mounted directories belong to whoever is going to use them, then
# stop being root.
#
# The reason this exists: downloads are worth more to a self-hoster than the
# volume they land in. Pointing them at a real folder — ./downloads, or
# somewhere on a NAS — means a bind mount, and a bind mount arrives owned by
# whoever owns it on the host. The image's own user (1001) is then usually
# the wrong answer, and the failure is silent until the first track finishes
# and cannot be written.
#
# So: PUID/PGID say who you are on the host, the directories are handed to
# them, and the server runs as them. Default is 1001, which is what the image
# built, so anyone using named volumes never notices this file.
set -e

PUID=${PUID:-1001}
PGID=${PGID:-1001}

# Already dropped — someone set `user:` in compose, which is a perfectly good
# way to do this and means there is nothing here left to do. chown would fail
# anyway without root, so don't pretend otherwise.
if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

for dir in /app/downloads /app/data /app/cache /home/appuser; do
    mkdir -p "$dir"
    # Only when it is actually wrong: a music library can be large, and
    # recursing over it on every restart to confirm nothing changed is a
    # slow way to learn nothing. The check is the directory itself, so a
    # tree with mixed ownership inside needs one chown -R by hand.
    if [ "$(stat -c %u "$dir")" != "$PUID" ] || [ "$(stat -c %g "$dir")" != "$PGID" ]; then
        # Not fatal. Docker Desktop maps bind-mount ownership in the VM and
        # refuses the chown while having already made the directory writable,
        # so failing here would break exactly the platforms that don't have
        # the problem this is solving.
        chown -R "$PUID:$PGID" "$dir" 2>/dev/null \
            || echo "unstream: cannot chown $dir — continuing (expected on Docker Desktop)" >&2
    fi
done

# yt-dlp and ffmpeg both write into HOME given the chance. The caches that
# matter are pointed elsewhere (YTDLP_CACHE_DIR), but anything that falls
# back to HOME needs somewhere it is allowed to write.
export HOME=/home/appuser

# Numeric on purpose: PUID need not match any user in /etc/passwd, and the
# whole point is that it usually doesn't.
exec gosu "$PUID:$PGID" "$@"
