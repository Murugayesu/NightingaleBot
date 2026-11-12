# NursingBot web image: the React app built by Node, served by Caddy.
# Build from the repository root:  docker build -f frontend/Dockerfile -t nursingbot-web .

FROM node:24.22.3-alpine AS build
WORKDIR /web
# Dependencies first (cached until the lockfile changes).
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM caddy:2.11.7-alpine
# The static app. Caddy's configuration (Caddyfile) is mounted by docker compose.
COPY --from=build /web/dist /srv
COPY Caddyfile /etc/caddy/Caddyfile
