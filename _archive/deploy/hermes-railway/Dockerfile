# Hermes Agent + agent-cli — Railway one-click image
#
# Pinned upstream:  github.com/NousResearch/hermes-agent @ ${HERMES_GIT_REF}
# Pinned interface: hermes dashboard --host 0.0.0.0 --port <PORT> --insecure --no-open
#
# Layout mirrors deploy/openclaw-railway: an Express front door at $PORT proxies
# to the internal Hermes dashboard, while preserving Nunchi-specific /api/*
# endpoints driven by agent-cli's status_reader.

FROM node:22-bookworm AS hermes-build

RUN apt-get update \
  && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git ca-certificates curl python3 python3-venv python3-pip make g++ \
  && rm -rf /var/lib/apt/lists/*

# uv (matches upstream Dockerfile) for fast, reproducible Python installs
ADD https://astral.sh/uv/install.sh /tmp/uv-install.sh
RUN sh /tmp/uv-install.sh && mv /root/.local/bin/uv /root/.local/bin/uvx /usr/local/bin/

WORKDIR /hermes
ARG HERMES_GIT_REF=v2026.4.30
RUN git clone --depth 1 --branch "${HERMES_GIT_REF}" https://github.com/NousResearch/hermes-agent.git .

# Build the web dashboard SPA + TUI assets.
ENV npm_config_install_links=false
RUN npm install --prefer-offline --no-audit \
  && (cd web && npm install --prefer-offline --no-audit && npm run build) \
  && (cd ui-tui && npm install --prefer-offline --no-audit && npm run build) \
  && npm cache clean --force


# Runtime image
FROM node:22-bookworm
ENV NODE_ENV=production
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
  && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl build-essential gcc g++ make procps file git \
    python3 python3-pip python3-venv pkg-config sudo ripgrep tini \
  && rm -rf /var/lib/apt/lists/* \
  && ln -sf /usr/bin/python3 /usr/local/bin/python

# uv (used for both Hermes and agent-cli installs)
ADD https://astral.sh/uv/install.sh /tmp/uv-install.sh
RUN sh /tmp/uv-install.sh && mv /root/.local/bin/uv /root/.local/bin/uvx /usr/local/bin/

# Copy Hermes source + pre-built UI assets
COPY --from=hermes-build /hermes /hermes

# Install Hermes into a venv, including MCP + messaging extras (Telegram/Discord/Slack/etc).
WORKDIR /hermes
RUN uv venv \
  && uv pip install --no-cache-dir -e ".[mcp,messaging,web,cli]"

# Make hermes CLI globally available.
RUN printf '%s\n' '#!/usr/bin/env bash' \
    'source /hermes/.venv/bin/activate' \
    'exec hermes "$@"' \
  > /usr/local/bin/hermes && chmod +x /usr/local/bin/hermes

# Install agent-cli (the Nunchi trading CLI) into the same venv so the
# `nunchi_trading` MCP server (`python3 -m cli.main mcp serve`) and the
# /api/* status_reader endpoints can both find it.
# NOTE: build context MUST be the repo root (see deploy/hermes-railway/railway.toml
# dockerfilePath + Railway service Root Directory = repo root). All COPY paths below
# are therefore relative to the repo root, not to deploy/hermes-railway/.
WORKDIR /agent-cli
COPY . .
# `uv venv` (line ~54) creates the venv WITHOUT pip, so /hermes/.venv/bin/pip
# doesn't exist. Install into that same venv with `uv pip` (same mechanism used
# to install Hermes above), targeting it via VIRTUAL_ENV.
RUN VIRTUAL_ENV=/hermes/.venv uv pip install --no-cache-dir -e ".[mcp]"

# Express wrapper
WORKDIR /app
COPY deploy/hermes-railway/package.json ./
RUN npm install --production

COPY deploy/hermes-railway/src ./src

# Workspace defaults — copied to volume on first boot
COPY deploy/hermes-railway/workspace /opt/workspace-defaults

RUN mkdir -p /data
ENV PORT=8080
EXPOSE 8080

# tini reaps MCP stdio subprocesses (matches upstream Hermes Dockerfile)
ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["node", "src/server.js"]
