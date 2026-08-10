#!/bin/sh
# Writes /config.js from the container's environment, before nginx starts.
#
# The app bundle is a static build, so anything baked in at `npm run build`
# time can only be changed by rebuilding the image — and the published images
# are prebuilt. This file is the seam that keeps one setting runtime-editable:
# set UNSTREAM_DEFAULT_LOCALE in your .env, restart the container, done.
#
# nginx's own entrypoint runs everything executable in /docker-entrypoint.d/
# in name order, which is why this is 10-.
set -e

target=/usr/share/nginx/html/config.js

# Injected into a JS string literal, so it is stripped to what a BCP 47 tag can
# contain rather than trusted. An empty or unset value writes an empty string,
# which the app reads as "not configured" and falls through to its own default.
locale=$(printf '%s' "${UNSTREAM_DEFAULT_LOCALE:-}" | tr -cd 'A-Za-z0-9-')

cat >"$target" <<EOF
// Generated at container start-up. Not a build artifact and not tracked —
// change the environment, not this file.
window.__UNSTREAM_CONFIG__ = { defaultLocale: "$locale" }
EOF

echo "unstream: default locale = ${locale:-(unset, using app default)}"
