# Anivexa-API sidecar — the anime streaming-source aggregator the backend's
# anivexa provider talks to (backend/app/anime/anivexa.py).
#
# Built from the pinned upstream commit, never from a floating tag, so a
# rebuild is reproducible: the exact source a release was tested against is
# what ships. `npm install --omit=dev` pulls only the one runtime dependency
# (dotenv); everything else is Node's own standard library.
#
# The sidecar only ever talks outbound to anime sites and inbound from the
# backend on the compose-internal network. It publishes no host port.

FROM node:20-alpine AS build

# The Anivexa-API commit this image is pinned to. Bump deliberately after
# validating a newer upstream against the test corpus — never "latest".
ARG ANIVEXA_COMMIT=14abe7b39733ed1ae591d1c010e7d4a84f655cd6

WORKDIR /src
RUN apk add --no-cache git \
    && git init -q \
    && git remote add origin https://github.com/walterwhite-69/Anivexa-API.git \
    && git fetch -q --depth 1 origin "$ANIVEXA_COMMIT" \
    && git checkout -q FETCH_HEAD

FROM node:20-alpine

WORKDIR /app
ENV NODE_ENV=production
# curl is required at runtime: anidbapp and animedunya (the sidecar's only 480p
# source) shell out to `curl` to scrape — a container without it makes those
# sources fail every scrape with "curl: not found". ca-certificates is needed
# for those TLS fetches against real certificates.
RUN apk add --no-cache curl ca-certificates
COPY --from=build /src /app
RUN npm install --omit=dev --no-audit --no-fund

# Non-root: node:20-alpine ships the `node` user. The sidecar only needs to
# read its own source and open a listen socket.
USER node
EXPOSE 4000

# The app answers 200 on / (its landing page); a probe here is a real signal
# the server is accepting connections, without depending on a third tool.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD wget -qO- http://127.0.0.1:4000/ >/dev/null 2>&1 || exit 1

CMD ["node", "server.js"]
