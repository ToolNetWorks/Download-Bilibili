FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BILIBILI_COOKIE_FILE=/run/secrets/bilibili_cookies.txt

WORKDIR /opt/download-bilibili

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md requirements-server.txt ./
COPY src/ ./src/

RUN python -m pip install --no-cache-dir \
    -r requirements-server.txt .

COPY app/ ./app/
COPY server/ ./server/

RUN python -c \
    "from server.main import app; print('WORKER IMPORT READY')"

EXPOSE 8080

CMD ["uvicorn", "server.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8080", \
     "--workers", "1"]
