FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        nodejs npm ffmpeg ca-certificates curl \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/* /root/.npm

WORKDIR /app
COPY pyproject.toml README.md ./
COPY jarvis ./jarvis
RUN pip install --no-cache-dir -e ".[voice]"
COPY workspace ./workspace

# Daten (Tokens, Sessions) und Gedächtnis liegen in Volumes
VOLUME ["/app/data", "/app/workspace", "/root/.claude", "/app/models"]
EXPOSE 8080
CMD ["jarvis", "serve"]
