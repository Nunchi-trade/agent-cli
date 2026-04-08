FROM python:3.12-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends gcc g++ git \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e ".[mcp,telegram]"

# Persistent state volume (Railway mounts here)
RUN mkdir -p /data

ENV PORT=8080
EXPOSE 8080

CMD ["python", "scripts/entrypoint.py"]
