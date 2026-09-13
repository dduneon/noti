#!/usr/bin/env bash
# 우분투/데비안 서버에 noti 를 설치한다. 루트로 실행.
#   curl -fsSL .../install.sh | sudo bash   또는   sudo ./deploy/install.sh
set -euo pipefail

APP_DIR=/opt/noti
REPO="${NOTI_REPO:-https://github.com/dduneon/noti.git}"
BRANCH="${NOTI_BRANCH:-main}"

command -v git >/dev/null || { echo "git 이 필요합니다"; exit 1; }
command -v python3 >/dev/null || { echo "python3 가 필요합니다"; exit 1; }

id -u noti >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin noti

if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch origin "$BRANCH" && git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO" "$APP_DIR"
fi

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet "$APP_DIR[web]"

[ -f "$APP_DIR/.env" ] || cp "$APP_DIR/.env.example" "$APP_DIR/.env"
[ -f "$APP_DIR/config.yaml" ] || cp "$APP_DIR/config.example.yaml" "$APP_DIR/config.yaml"
chmod 600 "$APP_DIR/.env"          # 봇 토큰이 들어있다
chown -R noti:noti "$APP_DIR"

install -m 644 "$APP_DIR/deploy/noti.service" /etc/systemd/system/noti.service
install -m 644 "$APP_DIR/deploy/noti-web.service" /etc/systemd/system/noti-web.service
systemctl daemon-reload

cat <<MSG

설치 완료: $APP_DIR

다음 순서로 진행하세요.
  1) sudo -u noti nano $APP_DIR/.env      # 텔레그램 토큰/챗ID 입력
  2) sudo systemctl enable --now noti noti-web
  3) 로컬 PC 에서:  ssh -L 8765:127.0.0.1:8765 <서버>
     브라우저에서 http://127.0.0.1:8765 로 조건 설정
  4) sudo journalctl -u noti -f          # 동작 확인
MSG
