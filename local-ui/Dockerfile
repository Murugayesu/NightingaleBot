# NursingBot web image: serves the local-ui management workspace via Caddy.
# Build from the repository root:  docker build -f local-ui/Dockerfile -t nursingbot-web .

FROM caddy:2.11.7-alpine
COPY local-ui /srv
COPY Caddyfile /etc/caddy/Caddyfile
