# Gamebattle orchestrator

The main FastAPI backend for Gamebattle. The UI lives at [gamebattle-ui](https://github.com/rizerphe/gamebattle-ui), and the helper terminal service is not public yet - the docker container is though.

## Setup

- **Firebase** for Google sign-in
- **MongoDB** for the main database, containing game files and metadata
- **S3-compatible storage** with an avatars bucket, caching firebase, and a recordings bucket as the main storage of playthrough sessions
- **Opensandbox** for sandboxed execution of the games
- **Terminal service**, managing TTY creation, xterm snapshots, etc.
- An OpenAI-compatible LLM endpoint for the AI features: automatic report assessment, rating fairness evaluation, and the homepage game summary feature
- **The UI** - running on vercel; see its own readme

## Example config

There are just two files here. You can just blindly copy-paste the compose file if you set up the environment variables in `.env` correctly.

Generate each secret with `openssl rand -hex 32` and `chmod 600 .env`.

```bash
# Shared secret between the orchestrator and the terminal service (the
# orchestrator's control calls are authenticated with it).
SERVICE_TOKEN=
# Shared secret between the terminal service and opensandbox-server.
OPEN_SANDBOX_API_KEY=

# MongoDB connection string. No database name in it; see MONGO_DATABASE.
MONGO_URL=mongodb+srv://user:password@cluster0.example.mongodb.net
# Database the orchestrator uses. Created on first write.
MONGO_DATABASE=gamebattle

# S3 endpoint of your provider.
S3_ENDPOINT=https://s3.eu-central-1.amazonaws.com
# Region of the endpoint and of both buckets; they must all agree.
S3_REGION=eu-central-1
# Credentials used by both the orchestrator and the terminal service.
S3_ACCESS_KEY=
S3_SECRET_KEY=
# Bucket for session recordings (written by the terminal service).
S3_BUCKET=gamebattle-recordings
# Bucket for the avatar cache (written by the orchestrator).
S3_AVATARS_BUCKET=gamebattle-avatars

# Public hostname of the API and terminal WebSocket. Its DNS must point at this
# VM before the first start, since Caddy requests the certificate then.
API_DOMAIN=api.game.example.com

# Firebase project that signs players in; must match the UI's
# NEXT_PUBLIC_FIREBASE_PROJECT_ID.
FIREBASE_PROJECT_ID=
# The project's service-account key as ONE line of JSON, in single quotes
# (`jq -c . credentials.json` prints it). Used to verify ID tokens.
FIREBASE_CREDENTIALS='{"type":"service_account",...}'

# JSON list of verified Google addresses that get the admin pages and API.
ADMIN_EMAILS=["teacher@school.org"]
# The UI's exact origin (scheme and host, no trailing slash), for browser CORS.
CORS_ORIGINS=https://game.example.com

# Optional: JSON list of regexes. Only verified emails matching one (or an
# admin) may use the service. Unset allows everyone.
# ACCESS_LIST=[".*@school\\.org"]

# Optional: how many comparisons each player is asked to rate (default 10).
# Drives the home page progress bar; play is not blocked after it.
# REQUIRED_COMPARISONS=5

# Optional: how long cached avatars are kept, in seconds (default 86400).
# AVATAR_CACHE_TTL_SECONDS=86400

# Optional: turns on the AI features (report assessment, rating fairness
# checks, the home page game summary). Any OpenAI-compatible endpoint that
# accepts `reasoning_effort` and a strict JSON-schema `response_format`.
# Without a key they are off. I'm using cerebras.ai.
# LLM_API_KEY=
# LLM_BASE_URL=https://api.cerebras.ai/v1
# LLM_MODEL=gpt-oss-120b

# Optional: image tags. "latest" is main; a branch build is tagged with the
# branch name (slashes become dashes).
# ORCHESTRATOR_TAG=latest
# TERMINAL_SERVICE_TAG=latest

# Optional: sandbox limits. Peak memory is about
# MAX_ACTIVE_SESSIONS x (SESSION_MEMORY + 100 MiB); keep it under ~60% of RAM.
# Image games run in:
# PYTHON_IMAGE=python:3.15-slim
# CPUs per game:
# SESSION_CPU=0.5
# Memory per game:
# SESSION_MEMORY=256Mi
# Games running at once; further sessions are refused:
# MAX_ACTIVE_SESSIONS=40
# Seconds before a session is ended and recorded:
# SESSION_TIME_LIMIT_SECONDS=3600
# Address sandbox ports are published on: the Docker bridge gateway, from
# `docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}'`.
# SANDBOX_PUBLISH_HOST=172.17.0.1
```

```yaml
x-logging: &logging
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"

configs:
  caddyfile:
    content: |
      {$$API_DOMAIN} {
        # The browser WebSocket goes to the terminal service; everything else
        # to the orchestrator.
        @attach path_regexp ^/sessions/[^/]+/attach$$
        handle @attach {
          reverse_proxy terminal-service:8080
        }
        handle {
          reverse_proxy orchestrator:8000
        }
      }

  opensandbox-config:
    content: |
      [server]
      host = "0.0.0.0"
      port = 8090

      [proxy]
      resolve_internal = false

      [log]
      level = "INFO"

      [runtime]
      type = "docker"
      execd_image = "opensandbox/execd:v1.1.0"

      [egress]
      image = "opensandbox/egress:v1.1.7"
      mode = "dns+nft"
      readiness_timeout_seconds = 30.0

      [docker]
      network_mode = "bridge"
      host_ip = "host.docker.internal"
      # Sandbox ports bind to the Docker bridge gateway, never the public interface.
      publish_host = "${SANDBOX_PUBLISH_HOST:-172.17.0.1}"
      port_range_min = 40000
      port_range_max = 60000
      drop_capabilities = ["AUDIT_WRITE", "MKNOD", "NET_ADMIN", "NET_RAW", "SYS_ADMIN", "SYS_MODULE", "SYS_PTRACE", "SYS_TIME", "SYS_TTY_CONFIG"]
      no_new_privileges = true
      pids_limit = 512

      [ingress]
      mode = "direct"

services:
  caddy:
    image: caddy:2
    restart: unless-stopped
    logging: *logging
    depends_on:
      - orchestrator
      - terminal-service
    ports:
      - "80:80"
      - "443:443"
    configs:
      - source: caddyfile
        target: /etc/caddy/Caddyfile
    environment:
      API_DOMAIN: ${API_DOMAIN:?}
    volumes:
      - caddy-data:/data

  orchestrator:
    image: rizerphe/gamebattle-docker-manager:${ORCHESTRATOR_TAG:-latest}
    restart: unless-stopped
    logging: *logging
    depends_on:
      - terminal-service
    environment:
      TERMINAL_SERVICE_URL: http://terminal-service:8080
      SERVICE_TOKEN: ${SERVICE_TOKEN:?}
      MONGO_URL: ${MONGO_URL:?}
      MONGO_DATABASE: ${MONGO_DATABASE:-gamebattle}
      FIREBASE_PROJECT_ID: ${FIREBASE_PROJECT_ID:?}
      FIREBASE_CREDENTIALS: ${FIREBASE_CREDENTIALS:?}
      ADMIN_EMAILS: ${ADMIN_EMAILS:-[]}
      ACCESS_LIST: ${ACCESS_LIST:-}
      REQUIRED_COMPARISONS: ${REQUIRED_COMPARISONS:-10}
      CORS_ORIGINS: ${CORS_ORIGINS:?}
      S3_ENDPOINT: ${S3_ENDPOINT:?}
      S3_REGION: ${S3_REGION:?}
      S3_ACCESS_KEY: ${S3_ACCESS_KEY:?}
      S3_SECRET_KEY: ${S3_SECRET_KEY:?}
      S3_AVATARS_BUCKET: ${S3_AVATARS_BUCKET:?}
      LLM_API_KEY: ${LLM_API_KEY:-}
      LLM_BASE_URL: ${LLM_BASE_URL:-}
      LLM_MODEL: ${LLM_MODEL:-}

  terminal-service:
    image: rizerphe/terminal-service:${TERMINAL_SERVICE_TAG:-latest}
    restart: unless-stopped
    logging: *logging
    depends_on:
      opensandbox-server:
        condition: service_healthy
    environment:
      OPEN_SANDBOX_DOMAIN: opensandbox-server:8090
      OPEN_SANDBOX_API_KEY: ${OPEN_SANDBOX_API_KEY:?}
      OPEN_SANDBOX_USE_SERVER_PROXY: "true"
      PYTHON_IMAGE: ${PYTHON_IMAGE:-python:3.15-slim}
      SESSION_CPU: ${SESSION_CPU:-0.5}
      SESSION_MEMORY: ${SESSION_MEMORY:-256Mi}
      MAX_ACTIVE_SESSIONS: ${MAX_ACTIVE_SESSIONS:-40}
      SESSION_TIME_LIMIT_SECONDS: ${SESSION_TIME_LIMIT_SECONDS:-3600}
      SERVICE_TOKEN: ${SERVICE_TOKEN:?}
      PORT: "8080"
      S3_ENDPOINT: ${S3_ENDPOINT:?}
      S3_REGION: ${S3_REGION:?}
      S3_ACCESS_KEY: ${S3_ACCESS_KEY:?}
      S3_SECRET_KEY: ${S3_SECRET_KEY:?}
      S3_BUCKET: ${S3_BUCKET:?}

  opensandbox-server:
    image: opensandbox/server:release-1.1.0
    restart: unless-stopped
    logging: *logging
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    configs:
      - source: opensandbox-config
        target: /etc/opensandbox/config.toml
    environment:
      SANDBOX_CONFIG_PATH: /etc/opensandbox/config.toml
      OPENSANDBOX_SERVER_API_KEY: ${OPEN_SANDBOX_API_KEY:?}
    healthcheck:
      test:
        [
          "CMD",
          "python",
          "-c",
          "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')",
        ]
      interval: 2s
      retries: 30

volumes:
  caddy-data:
```
