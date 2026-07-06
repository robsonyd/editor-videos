#!/bin/bash
set -euo pipefail

APP_NAME="EVR Deluxe"
BUNDLE_ID="com.robsonyuri.editorvideos"
VERSION="1.1"
PROJECT_DIR="${EVR_PROJECT_DIR:-$HOME/Projetos/editor-videos}"
APP_DIR="$PROJECT_DIR/App"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
APP_BUNDLE="/Applications/$APP_NAME.app"
MACOS_DIR="$APP_BUNDLE/Contents/MacOS"
RESOURCES_DIR="$APP_BUNDLE/Contents/Resources"
LAUNCHER="$MACOS_DIR/$APP_NAME"
ICON_SOURCE="$APP_BUNDLE/Contents/Resources/EVRDeluxe.icns"
ICON_TARGET="$RESOURCES_DIR/EVRDeluxe.icns"

if [ ! -d "$PROJECT_DIR" ]; then
  echo "Projeto não encontrado em: $PROJECT_DIR" >&2
  exit 1
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Python da .venv não encontrado em: $PYTHON_BIN" >&2
  echo "Rode: cd \"$PROJECT_DIR\" && python -m venv .venv && .venv/bin/python -m pip install -r App/requirements.txt" >&2
  exit 1
fi

"$PYTHON_BIN" - <<'PY'
import importlib.util
import sys

required = ["flask", "openai", "huggingface_hub", "pyannote.audio", "torch"]
missing = [name for name in required if importlib.util.find_spec(name) is None]
if missing:
    print("Dependências ausentes na .venv: " + ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)
PY

mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"

if [ -f "$ICON_SOURCE" ] && [ "$ICON_SOURCE" != "$ICON_TARGET" ]; then
  cp "$ICON_SOURCE" "$ICON_TARGET"
fi

cat > "$APP_BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDisplayName</key>
  <string>$APP_NAME</string>
  <key>CFBundleExecutable</key>
  <string>$APP_NAME</string>
  <key>CFBundleIconFile</key>
  <string>EVRDeluxe</string>
  <key>CFBundleIdentifier</key>
  <string>$BUNDLE_ID</string>
  <key>CFBundleName</key>
  <string>$APP_NAME</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>$VERSION</string>
  <key>CFBundleVersion</key>
  <string>$VERSION</string>
</dict>
</plist>
PLIST

cat > "$LAUNCHER" <<'LAUNCHER'
#!/bin/bash
set -u

APP_NAME="EVR Deluxe"
PROJECT_DIR="${EVR_PROJECT_DIR:-$HOME/Projetos/editor-videos}"
APP_DIR="$PROJECT_DIR/App"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
URL="http://127.0.0.1:5050"
LOG_FILE="/tmp/evr-deluxe.log"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export PYTHONUNBUFFERED=1

show_error() {
  local message="$1"
  /usr/bin/osascript -e "display alert \"$APP_NAME\" message \"$message\" as critical" >/dev/null 2>&1 || echo "$message" >&2
}

if [ ! -d "$PROJECT_DIR" ]; then
  show_error "Não encontrei o projeto em $PROJECT_DIR."
  exit 1
fi

if [ ! -x "$PYTHON_BIN" ]; then
  show_error "Não encontrei o Python da .venv em $PYTHON_BIN. Abra pelo terminal e reinstale as dependências antes de usar o app."
  exit 1
fi

if [ ! -d "$APP_DIR" ]; then
  show_error "Não encontrei a pasta App em $APP_DIR."
  exit 1
fi

cd "$APP_DIR" || exit 1

"$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import importlib.util
import sys

required = ["flask", "openai", "huggingface_hub", "pyannote.audio", "torch"]
missing = [name for name in required if importlib.util.find_spec(name) is None]
if missing:
    print(", ".join(missing), file=sys.stderr)
    raise SystemExit(1)
PY

if [ $? -ne 0 ]; then
  show_error "A .venv do EVR Deluxe está incompleta. Rode: cd ~/Projetos/editor-videos && .venv/bin/python -m pip install -r App/requirements.txt"
  exit 1
fi

OLD_PID="$(/usr/sbin/lsof -ti tcp:5050 -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$OLD_PID" ]; then
  /bin/kill $OLD_PID 2>/dev/null || true
  /bin/sleep 1
fi

"$PYTHON_BIN" -c "from app import app; app.run(host='127.0.0.1', port=5050, debug=False, use_reloader=False)" > "$LOG_FILE" 2>&1 &
SERVER_PID=$!

cleanup() {
  /bin/kill "$SERVER_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

"$PYTHON_BIN" - <<'PY'
import sys
import time
import urllib.request

url = "http://127.0.0.1:5050"
for _ in range(50):
    try:
        urllib.request.urlopen(url, timeout=0.6).close()
        raise SystemExit(0)
    except Exception:
        time.sleep(0.2)
raise SystemExit(1)
PY

if [ $? -ne 0 ]; then
  show_error "O servidor local do EVR Deluxe não iniciou. Veja o log em $LOG_FILE."
  cleanup
  exit 1
fi

/usr/bin/open -a "Google Chrome" "$URL" 2>/dev/null || /usr/bin/open "$URL"

while /bin/kill -0 "$SERVER_PID" 2>/dev/null; do
  /bin/sleep 3

  TAB_OPEN="$(/usr/bin/osascript <<'APPLESCRIPT' 2>/dev/null || echo unknown
set targetUrl to "127.0.0.1:5050"
set foundTab to false

tell application "System Events"
  set chromeRunning to exists process "Google Chrome"
end tell

if chromeRunning then
  tell application "Google Chrome"
    repeat with w in windows
      repeat with t in tabs of w
        try
          if (URL of t contains targetUrl) then
            set foundTab to true
          end if
        end try
      end repeat
    end repeat
  end tell
end if

return foundTab
APPLESCRIPT
)"

  if [ "$TAB_OPEN" = "false" ]; then
    cleanup
    exit 0
  fi
done
LAUNCHER

chmod +x "$LAUNCHER"

# Mantém compatibilidade com o nome antigo do executável, caso o Finder tenha cacheado.
ln -sf "$APP_NAME" "$MACOS_DIR/Editor de Vídeos"

touch "$APP_BUNDLE"

echo "$APP_BUNDLE atualizado com sucesso."
echo "Lançador aponta para: $PYTHON_BIN"
