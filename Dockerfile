# CareLoop — one origin, one process, in a container.
#
#   docker build -t careloop .
#   docker run --rm -p 127.0.0.1:8080:8080 -v careloop-data:/data careloop
#   open http://127.0.0.1:8080/  (the simulator is at /simulator, the card at /card)
#
# Stage one builds the three frontends and copies them into `careloop/web`, exactly as
# `npm run dist` does on a laptop. Stage two is the server the repo actually ships: Python,
# standard library only — no runtime third-party dependency is installed in this image.
#
# The container runs with `CARELOOP_AUTH=off` because that is the demo mode README documents and
# the only mode a local container can show without secrets. Turning auth ON is
# a real deployment decision: set `CARELOOP_AUTH=on`, pass `CARELOOP_PUBLIC_URL=https://<host>`
# so the OAuth metadata names the origin a client actually reaches, and pass the secret
# environment variables listed in README.md at run time. They are never baked into an
# image. Without an explicit public URL the server would publish its own bind address,
# `http://0.0.0.0:8080`, which is why the demo default below is the mapped loopback origin.

# ------------------------------------------------------------------ the frontends
FROM node:22-bookworm-slim AS web
WORKDIR /build
# Dependency manifest first, so a source-only change does not re-run `npm ci`.
COPY package.json package-lock.json tsconfig.base.json ./
COPY frontend ./frontend
# `npm run dist` = the three builds plus `node scripts/sync_web.mjs`, and the simulator imports
# the generated copy of the shipped interaction model, so both of those trees must exist here.
COPY scripts ./scripts
COPY alexa ./alexa
RUN npm ci --no-audit --no-fund && npm run dist

# ------------------------------------------------------------------ the server
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CARELOOP_DB_ROOT=/data \
    CARELOOP_AUTH=off \
    CARELOOP_PUBLIC_URL=http://127.0.0.1:8080
WORKDIR /app
COPY careloop ./careloop
COPY preflight ./preflight
COPY scripts ./scripts
COPY tests ./tests
COPY alexa ./alexa
COPY README.md requirements.txt ./
COPY --from=web /build/careloop/web ./careloop/web

# One unprivileged user, one writable data directory. The per-household encrypted cells and
# their keyrings live under /data, so the image carries no household state at all.
RUN useradd --create-home --uid 10001 careloop \
    && mkdir -p /data && chown -R careloop:careloop /data /app
USER careloop
VOLUME ["/data"]
EXPOSE 8080

# Health is the origin answering, not a bespoke route: the server serves every surface from
# one process, so if `/` is up the surfaces are up.
HEALTHCHECK --interval=30s --timeout=4s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/', timeout=3).status == 200 else 1)"

CMD ["python", "scripts/serve.py", "--host", "0.0.0.0", "--port", "8080"]
