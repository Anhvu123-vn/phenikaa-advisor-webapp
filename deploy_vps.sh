#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo "[LỖI] Chưa có Docker. Hãy cài Docker + docker compose plugin trước."
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "[LỖI] Chưa có docker compose plugin."
  exit 1
fi

if [ ! -f .env ]; then
  if command -v python3 >/dev/null 2>&1; then
    SECRET="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(48))
PY
)"
  else
    SECRET="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 96)"
  fi
  cat > .env <<ENV
PHENIKAA_SECRET_KEY=${SECRET}
PHENIKAA_COOKIE_SECURE=0
ENV
  chmod 600 .env
  echo "[OK] Đã tạo .env + secret ngẫu nhiên."
fi

docker compose up -d --build

echo
echo "[OK] Phenikaa Advisor đã khởi động."
echo "Mở: http://<IP-VPS>/"
echo "Kiểm tra log: docker compose logs -f app"
