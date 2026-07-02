# Build OpenClaw from source
FROM node:22-bookworm AS openclaw-build

RUN apt-get update \
  && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git ca-certificates curl python3 make g++ \
  && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://bun.sh/install | bash
ENV PATH="/root/.bun/bin:${PATH}"
RUN corepack enable

WORKDIR /openclaw
ARG OPENCLAW_GIT_REF=v2026.2.22
RUN git clone --depth 1 --branch "${OPENCLAW_GIT_REF}" https://github.com/openclaw/openclaw.git .

# Patch workspace protocol references
RUN set -eux; \
  find ./extensions -name 'package.json' -type f | while read -r f; do \
    sed -i -E 's/"openclaw"[[:space:]]*:[[:space:]]*">=[^"]+"/"openclaw": "*"/g' "$f"; \
    sed -i -E 's/"openclaw"[[:space:]]*:[[:space:]]*"workspace:[^"]+"/"openclaw": "*"/g' "$f"; \
  done

RUN pnpm install --no-frozen-lockfile
RUN pnpm build
ENV OPENCLAW_PREFER_PNPM=1
RUN pnpm ui:install && pnpm ui:build


# Runtime image
FROM node:22-bookworm
ENV NODE_ENV=production

RUN apt-get update \
  && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl build-essential gcc g++ make procps file git \
    python3 python3-pip python3-venv pkg-config sudo ripgrep \
  && rm -rf /var/lib/apt/lists/* \
  && ln -sf /usr/bin/python3 /usr/local/bin/python

# Node tools
RUN corepack enable
RUN npm install -g mcporter@0.7.3 mcp-remote@0.1.38

# OpenClaw from build stage
COPY --from=openclaw-build /openclaw /openclaw
RUN printf '%s\n' '#!/usr/bin/env bash' 'exec node /openclaw/dist/entry.js "$@"' \
  > /usr/local/bin/openclaw && chmod +x /usr/local/bin/openclaw

# rg wrapper: strip grep-style flags that agents pass
RUN mv /usr/bin/rg /usr/bin/rg-real \
  && printf '%s\n' \
    '#!/usr/bin/env bash' \
    'args=()' \
    'for a in "$@"; do' \
    '  case "$a" in -R|-r) ;; *) args+=("$a") ;; esac' \
    'done' \
    'exec /usr/bin/rg-real "${args[@]}"' \
    > /usr/local/bin/rg && chmod +x /usr/local/bin/rg

# Install agent-cli (our trading CLI)
# NOTE: build context MUST be the repo root (see deploy/openclaw-railway/railway.toml
# dockerfilePath + Railway service Root Directory = repo root). All COPY paths below
# are therefore relative to the repo root, not to deploy/openclaw-railway/.
WORKDIR /agent-cli
COPY . .
RUN pip install --no-cache-dir --break-system-packages -e ".[mcp]"

# Wrapper app
WORKDIR /app
COPY deploy/openclaw-railway/package.json ./
RUN npm install --production

COPY deploy/openclaw-railway/src ./src

# Workspace defaults (copied to volume at runtime)
COPY deploy/openclaw-railway/workspace /opt/workspace-defaults

# Vendor mcporter skill
RUN set -eux; \
  mkdir -p /opt/openclaw-skills; \
  cp -r /openclaw/skills/mcporter /opt/openclaw-skills/ 2>/dev/null || true

RUN mkdir -p /data
ENV PORT=8080
EXPOSE 8080
CMD ["node", "src/server.js"]
