# Gold Entry Timing Desk — single-container deployment.
#
# Stage 1 builds the React SPA, stage 2 runs FastAPI, which serves both the API
# and the built assets from one port. No nginx, no separate static host.

FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json ./
RUN npm install
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends sqlite3 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements-server.txt

COPY core/ ./core/
COPY server/ ./server/
COPY --from=web /web/dist ./web/dist

# The journal is the app's memory; keep it outside the image.
ENV GOLD_TIMING_DB=/data/journal.db
VOLUME /data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health').read()" || exit 1

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
