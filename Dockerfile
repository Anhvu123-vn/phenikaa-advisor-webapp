FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PHENIKAA_NOVNC_DIR=/usr/share/novnc \
    HOME=/home/phenikaa

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    xvfb \
    x11vnc \
    fluxbox \
    novnc \
    ca-certificates \
    fonts-liberation \
    fonts-noto-core \
    dbus-x11 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin phenikaa \
    && mkdir -p /app/data/users \
    && chown -R phenikaa:phenikaa /app

COPY --chown=phenikaa:phenikaa . /app

USER phenikaa

EXPOSE 5000

# 1 worker là chủ ý: crawl jobs đang được giữ trong RAM của process.
# Dùng nhiều threads để SSE + WebSocket noVNC + API có thể hoạt động đồng thời.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "24", "--timeout", "0", "--access-logfile", "-", "--error-logfile", "-", "server:app"]
