FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
# Retries + a persistent download cache so a flaky connection doesn't fail the build
RUN --mount=type=cache,target=/root/.npm \
    npm config set fetch-retries 6 && npm config set fetch-retry-mintimeout 20000 && \
    npm config set fetch-retry-maxtimeout 120000 && \
    (npm ci --no-audit --no-fund || (sleep 10 && npm ci --no-audit --no-fund) || (sleep 30 && npm ci --no-audit --no-fund))
COPY frontend/ ./
RUN npm run build

FROM caddy:2-alpine
ARG CADDYFILE=deploy/Caddyfile
COPY ${CADDYFILE} /etc/caddy/Caddyfile
COPY --from=ui /ui/dist /srv
