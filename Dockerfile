FROM python:3.12-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl gcc g++ git \
  && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://railway.com/install.sh | sh

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e ".[mcp]"

# Persistent state volume (Railway mounts here)
RUN mkdir -p /data

ENV PORT=8080
EXPOSE 8080

CMD ["python", "scripts/entrypoint.py"]
