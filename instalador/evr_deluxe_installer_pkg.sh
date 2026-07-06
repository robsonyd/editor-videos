#!/bin/bash
set -euo pipefail
export COPYFILE_DISABLE=1

APP_NAME="EVR Deluxe"
IDENTIFIER="com.robsonyuri.evrdeluxe"
VERSION="1.3.0"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INSTALLER_DIR="$PROJECT_DIR/instalador"
BUILD_DIR="$INSTALLER_DIR/build"
DIST_DIR="$INSTALLER_DIR/dist"
VENDOR_DIR="$INSTALLER_DIR/vendor"
ROOT_DIR="$BUILD_DIR/root"
SCRIPTS_DIR="$BUILD_DIR/scripts"
APP_BUNDLE="$ROOT_DIR/Applications/$APP_NAME.app"
MACOS_DIR="$APP_BUNDLE/Contents/MacOS"
RESOURCES_DIR="$APP_BUNDLE/Contents/Resources"
SOURCE_DIR="$RESOURCES_DIR/source"
PKG_PATH="$DIST_DIR/EVR-Deluxe-Installer.pkg"
PYTHON_RUNTIME_VERSION="3.11.15"
PYTHON_RUNTIME_BUILD="20260623"
PYTHON_RUNTIME_NAME="cpython-$PYTHON_RUNTIME_VERSION+$PYTHON_RUNTIME_BUILD-aarch64-apple-darwin-install_only_stripped.tar.gz"
PYTHON_RUNTIME_URL="https://github.com/astral-sh/python-build-standalone/releases/download/$PYTHON_RUNTIME_BUILD/cpython-$PYTHON_RUNTIME_VERSION%2B$PYTHON_RUNTIME_BUILD-aarch64-apple-darwin-install_only_stripped.tar.gz"
PYTHON_RUNTIME_PATH="$VENDOR_DIR/$PYTHON_RUNTIME_NAME"
PYTHON_RUNTIME_SHA_PATH="$VENDOR_DIR/$PYTHON_RUNTIME_NAME.sha256"

require_file() {
  if [ ! -e "$1" ]; then
    echo "Arquivo obrigatório não encontrado: $1" >&2
    exit 1
  fi
}

require_file "$PROJECT_DIR/App/app.py"
require_file "$PROJECT_DIR/App/requirements.txt"
require_file "$PROJECT_DIR/Logo APP de Video.png"
require_file "$PROJECT_DIR/Modelos/ggml-base.bin"
require_file "$PROJECT_DIR/whisper.cpp/build/bin/whisper-cli"
require_file "$(command -v ffmpeg || true)"
require_file "$(command -v ffprobe || true)"

rm -rf "$BUILD_DIR"
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR" "$SOURCE_DIR" "$SCRIPTS_DIR" "$DIST_DIR" "$VENDOR_DIR"

ensure_python_runtime() {
  if [ ! -f "$PYTHON_RUNTIME_PATH" ]; then
    echo "Baixando Python standalone: $PYTHON_RUNTIME_URL"
    curl -L --fail --progress-bar -o "$PYTHON_RUNTIME_PATH" "$PYTHON_RUNTIME_URL"
  fi

  shasum -a 256 "$PYTHON_RUNTIME_PATH" > "$PYTHON_RUNTIME_SHA_PATH"
}

copy_clean_app() {
  mkdir -p "$SOURCE_DIR/App"
  rsync -a "$PROJECT_DIR/App/" "$SOURCE_DIR/App/" \
    --exclude ".DS_Store" \
    --exclude "._*" \
    --exclude ".env" \
    --exclude ".env.save" \
    --exclude "config.json" \
    --exclude "__pycache__/" \
    --exclude "*.pyc" \
    --exclude "venv/" \
    --exclude ".venv/"
}

copy_runtime_assets() {
  cp "$PROJECT_DIR/Logo APP de Video.png" "$SOURCE_DIR/Logo APP de Video.png"

  tar -xzf "$PYTHON_RUNTIME_PATH" -C "$SOURCE_DIR"
  chmod +x "$SOURCE_DIR/python/bin/python3" "$SOURCE_DIR/python/bin/python" 2>/dev/null || true

  mkdir -p "$SOURCE_DIR/Modelos"
  cp "$PROJECT_DIR/Modelos/ggml-base.bin" "$SOURCE_DIR/Modelos/ggml-base.bin"
  touch "$SOURCE_DIR/Modelos/.gitkeep"

  mkdir -p "$SOURCE_DIR/whisper.cpp"
  rsync -a "$PROJECT_DIR/whisper.cpp/build/" "$SOURCE_DIR/whisper.cpp/build/" \
    --exclude ".DS_Store" \
    --exclude "._*" \
    --exclude "CMakeFiles/" \
    --exclude "*.o"

  if [ -f "/Applications/$APP_NAME.app/Contents/Resources/EVRDeluxe.icns" ]; then
    cp "/Applications/$APP_NAME.app/Contents/Resources/EVRDeluxe.icns" "$RESOURCES_DIR/EVRDeluxe.icns"
  fi
}

copy_ffmpeg_runtime() {
  local ffmpeg_path
  local ffprobe_path
  ffmpeg_path="$(command -v ffmpeg)"
  ffprobe_path="$(command -v ffprobe)"
  local bin_dir="$SOURCE_DIR/bin"
  local lib_dir="$SOURCE_DIR/lib"

  mkdir -p "$bin_dir" "$lib_dir"
  cp "$ffmpeg_path" "$bin_dir/ffmpeg"
  cp "$ffprobe_path" "$bin_dir/ffprobe"
  chmod +x "$bin_dir/ffmpeg" "$bin_dir/ffprobe"

  copy_macho_deps "$bin_dir/ffmpeg" "$lib_dir"
  copy_macho_deps "$bin_dir/ffprobe" "$lib_dir"

  local copied_any=1
  while [ "$copied_any" -eq 1 ]; do
    copied_any=0
    while IFS= read -r lib_file; do
      before_count="$(find "$lib_dir" -maxdepth 1 -type f -name '*.dylib' | wc -l | tr -d ' ')"
      copy_macho_deps "$lib_file" "$lib_dir"
      after_count="$(find "$lib_dir" -maxdepth 1 -type f -name '*.dylib' | wc -l | tr -d ' ')"
      if [ "$after_count" != "$before_count" ]; then
        copied_any=1
      fi
    done < <(find "$lib_dir" -maxdepth 1 -type f -name '*.dylib')
  done

  patch_macho_file "$bin_dir/ffmpeg" "@executable_path/../lib"
  patch_macho_file "$bin_dir/ffprobe" "@executable_path/../lib"
  while IFS= read -r lib_file; do
    patch_macho_file "$lib_file" "@loader_path"
  done < <(find "$lib_dir" -maxdepth 1 -type f -name '*.dylib')

  codesign --force --sign - "$bin_dir/ffmpeg" >/dev/null 2>&1 || true
  codesign --force --sign - "$bin_dir/ffprobe" >/dev/null 2>&1 || true
  while IFS= read -r lib_file; do
    codesign --force --sign - "$lib_file" >/dev/null 2>&1 || true
  done < <(find "$lib_dir" -maxdepth 1 -type f -name '*.dylib')
}

copy_macho_deps() {
  local macho_file="$1"
  local lib_dir="$2"

  otool -L "$macho_file" 2>/dev/null | awk 'NR > 1 {print $1}' | while read -r dep; do
    case "$dep" in
      /opt/homebrew/*|/usr/local/*)
        local base
        base="$(basename "$dep")"
        if [ ! -f "$lib_dir/$base" ]; then
          cp -L "$dep" "$lib_dir/$base"
          chmod u+w "$lib_dir/$base" 2>/dev/null || true
        fi
        ;;
    esac
  done
}

patch_macho_file() {
  local macho_file="$1"
  local replacement_prefix="$2"

  chmod u+w "$macho_file" 2>/dev/null || true
  otool -L "$macho_file" 2>/dev/null | awk 'NR > 1 {print $1}' | while read -r dep; do
    case "$dep" in
      /opt/homebrew/*|/usr/local/*)
        install_name_tool -change "$dep" "$replacement_prefix/$(basename "$dep")" "$macho_file" 2>/dev/null || true
        ;;
    esac
  done

  if [[ "$macho_file" == *.dylib ]]; then
    install_name_tool -id "@rpath/$(basename "$macho_file")" "$macho_file" 2>/dev/null || true
  fi
}

patch_whisper_rpath() {
  local whisper_bin="$SOURCE_DIR/whisper.cpp/build/bin/whisper-cli"
  local old_paths=(
    "$PROJECT_DIR/whisper.cpp/build/src"
    "$PROJECT_DIR/whisper.cpp/build/ggml/src"
    "$PROJECT_DIR/whisper.cpp/build/ggml/src/ggml-blas"
    "$PROJECT_DIR/whisper.cpp/build/ggml/src/ggml-metal"
  )
  local new_paths=(
    "@executable_path/../src"
    "@executable_path/../ggml/src"
    "@executable_path/../ggml/src/ggml-blas"
    "@executable_path/../ggml/src/ggml-metal"
  )

  for path in "${old_paths[@]}"; do
    install_name_tool -delete_rpath "$path" "$whisper_bin" 2>/dev/null || true
  done

  for path in "${new_paths[@]}"; do
    install_name_tool -add_rpath "$path" "$whisper_bin" 2>/dev/null || true
  done

  codesign --force --sign - "$whisper_bin" >/dev/null 2>&1 || true
}

write_info_plist() {
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
  <string>$IDENTIFIER</string>
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
}

write_launcher() {
  cat > "$MACOS_DIR/$APP_NAME" <<'LAUNCHER'
#!/bin/bash
set -u

APP_NAME="EVR Deluxe"
HOST="127.0.0.1"
PORT="5050"
URL="http://$HOST:$PORT"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUNDLE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
BUNDLED_SOURCE="$BUNDLE_DIR/Contents/Resources/source"
SUPPORT_DIR="$HOME/Library/Application Support/EVR Deluxe"
APP_DIR="$SUPPORT_DIR/App"
RUNTIME_PYTHON_BIN="$SUPPORT_DIR/python/bin/python3"
VENV_DIR="$SUPPORT_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"
LOG_DIR="$HOME/Library/Logs/EVR Deluxe"
LOG_FILE="$LOG_DIR/evr-deluxe.log"
BOOTSTRAP_LOG="$LOG_DIR/bootstrap.log"

export PATH="$SUPPORT_DIR/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export PYTHONUNBUFFERED=1
export MPLCONFIGDIR="$SUPPORT_DIR/.matplotlib"

show_error() {
  local message="$1"
  /usr/bin/osascript -e "display alert \"$APP_NAME\" message \"$message\" as critical" >/dev/null 2>&1 || echo "$message" >&2
}

notify() {
  local message="$1"
  /usr/bin/osascript -e "display notification \"$message\" with title \"$APP_NAME\"" >/dev/null 2>&1 || true
}

sync_source() {
  mkdir -p "$SUPPORT_DIR" "$LOG_DIR" "$MPLCONFIGDIR"
  /usr/bin/rsync -a --delete "$BUNDLED_SOURCE/" "$SUPPORT_DIR/" \
    --exclude ".venv/" \
    --exclude "App/config.json" \
    --exclude "App/.env" \
    --exclude "App/.env.save" \
    --exclude "__pycache__/" \
    --exclude "*.pyc" >> "$BOOTSTRAP_LOG" 2>&1
}

requirements_changed() {
  local hash_file="$VENV_DIR/.evr-requirements.sha256"
  local current_hash
  current_hash="$(/usr/bin/shasum -a 256 "$APP_DIR/requirements.txt" | /usr/bin/awk '{print $1}')"
  if [ ! -f "$hash_file" ]; then
    return 0
  fi
  if [ "$current_hash" != "$(/bin/cat "$hash_file" 2>/dev/null)" ]; then
    return 0
  fi
  return 1
}

save_requirements_hash() {
  /usr/bin/shasum -a 256 "$APP_DIR/requirements.txt" | /usr/bin/awk '{print $1}' > "$VENV_DIR/.evr-requirements.sha256"
}

dependencies_ok() {
  if [ ! -x "$PYTHON_BIN" ]; then
    return 1
  fi
  "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import importlib.util
required = ["flask", "openai", "huggingface_hub", "pyannote.audio", "torch"]
raise SystemExit(0 if all(importlib.util.find_spec(name) for name in required) else 1)
PY
}

venv_uses_supported_python() {
  if [ ! -x "$PYTHON_BIN" ]; then
    return 1
  fi
  "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)
PY
}

bootstrap_python() {
  mkdir -p "$LOG_DIR"
  touch "$BOOTSTRAP_LOG"

  if [ ! -x "$RUNTIME_PYTHON_BIN" ]; then
    show_error "O Python interno do EVR Deluxe não foi encontrado. Reinstale o aplicativo."
    exit 1
  fi

  if [ -x "$PYTHON_BIN" ] && ! venv_uses_supported_python; then
    notify "Atualizando ambiente Python interno do EVR Deluxe."
    /bin/rm -rf "$VENV_DIR" >> "$BOOTSTRAP_LOG" 2>&1
  fi

  if [ ! -x "$PYTHON_BIN" ]; then
    notify "Preparando ambiente Python. A primeira abertura pode levar alguns minutos."
    "$RUNTIME_PYTHON_BIN" -m venv "$VENV_DIR" >> "$BOOTSTRAP_LOG" 2>&1
  fi

  if ! dependencies_ok || requirements_changed; then
    notify "Instalando dependências do EVR Deluxe. Isso pode demorar na primeira abertura."
    "$PYTHON_BIN" -m pip install --upgrade pip >> "$BOOTSTRAP_LOG" 2>&1
    "$PYTHON_BIN" -m pip install -r "$APP_DIR/requirements.txt" >> "$BOOTSTRAP_LOG" 2>&1

    if ! dependencies_ok; then
      show_error "Não consegui instalar todas as dependências. Veja o log em: $BOOTSTRAP_LOG"
      exit 1
    fi

    save_requirements_hash
  fi
}

start_server() {
  local old_pid
  old_pid="$(/usr/sbin/lsof -ti tcp:$PORT -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$old_pid" ]; then
    /bin/kill $old_pid 2>/dev/null || true
    /bin/sleep 1
  fi

  cd "$APP_DIR" || exit 1
  "$PYTHON_BIN" -c "from app import app; app.run(host='$HOST', port=$PORT, debug=False, use_reloader=False)" > "$LOG_FILE" 2>&1 &
  SERVER_PID=$!

  cleanup() {
    /bin/kill "$SERVER_PID" 2>/dev/null || true
  }
  trap cleanup INT TERM EXIT

  "$PYTHON_BIN" - <<PY
import time
import urllib.request
url = "$URL"
for _ in range(80):
    try:
        urllib.request.urlopen(url, timeout=0.6).close()
        raise SystemExit(0)
    except Exception:
        time.sleep(0.25)
raise SystemExit(1)
PY

  if [ $? -ne 0 ]; then
    show_error "O servidor local do EVR Deluxe não iniciou. Veja o log em: $LOG_FILE"
    cleanup
    exit 1
  fi
}

monitor_browser() {
  /usr/bin/open -a "Google Chrome" "$URL" 2>/dev/null || /usr/bin/open "$URL"

  while /bin/kill -0 "$SERVER_PID" 2>/dev/null; do
    /bin/sleep 3

    local tab_open
    tab_open="$(/usr/bin/osascript <<'APPLESCRIPT' 2>/dev/null || echo unknown
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

    if [ "$tab_open" = "false" ]; then
      cleanup
      exit 0
    fi
  done
}

if [ ! -d "$BUNDLED_SOURCE" ]; then
  show_error "Fonte interna do EVR Deluxe não encontrada dentro do app."
  exit 1
fi

sync_source
bootstrap_python
start_server
monitor_browser
LAUNCHER

  chmod +x "$MACOS_DIR/$APP_NAME"
}

write_postinstall() {
  cat > "$SCRIPTS_DIR/postinstall" <<'POSTINSTALL'
#!/bin/bash
set -e

/usr/bin/touch "/Applications/EVR Deluxe.app" 2>/dev/null || true
exit 0
POSTINSTALL
  chmod +x "$SCRIPTS_DIR/postinstall"
}

ensure_python_runtime
copy_clean_app
copy_runtime_assets
copy_ffmpeg_runtime
patch_whisper_rpath
write_info_plist
write_launcher
write_postinstall

find "$ROOT_DIR" -name ".DS_Store" -delete
find "$ROOT_DIR" -name "._*" -delete
xattr -cr "$ROOT_DIR" 2>/dev/null || true

pkgbuild \
  --root "$ROOT_DIR" \
  --scripts "$SCRIPTS_DIR" \
  --identifier "$IDENTIFIER" \
  --version "$VERSION" \
  --install-location "/" \
  --filter '(^|/)\._[^/]*$' \
  --filter '(^|/)\.DS_Store$' \
  --filter '(^|/)\.svn($|/)' \
  --filter '(^|/)CVS($|/)' \
  "$PKG_PATH"

echo "Instalador gerado em: $PKG_PATH"
du -sh "$PKG_PATH"
