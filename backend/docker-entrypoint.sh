#!/bin/sh
# Hand the mounted directories to PUID/PGID, then stop being root.
#
# Downloads are worth more to a self-hoster than the volume they land in, so
# they go to a bind mount — which arrives owned by whoever owns it on the
# host, usually not the image's user (1001). The failure is silent until the
# first finished track cannot be written.
set -e

PUID=${PUID:-1001}
PGID=${PGID:-1001}

# Someone set `user:` in compose, which is a fine way to do this and leaves
# nothing here to do. chown would fail without root anyway.
if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

for dir in /app/downloads /app/data /app/cache /home/appuser; do
    mkdir -p "$dir"
    # Only when actually wrong — recursing a music library on every restart to
    # confirm nothing changed is slow. A tree with mixed ownership inside
    # needs one chown -R by hand.
    if [ "$(stat -c %u "$dir")" != "$PUID" ] || [ "$(stat -c %g "$dir")" != "$PGID" ]; then
        # Not fatal: Docker Desktop refuses the chown while having already
        # made the directory writable, so failing here would break the
        # platforms that don't have this problem.
        chown -R "$PUID:$PGID" "$dir" 2>/dev/null \
            || echo "unstream: cannot chown $dir — continuing (expected on Docker Desktop)" >&2
    fi
done

# yt-dlp and ffmpeg write into HOME given the chance.
export HOME=/home/appuser

# Numeric: PUID need not match any user in /etc/passwd, and usually doesn't.
exec gosu "$PUID:$PGID" "$@"
