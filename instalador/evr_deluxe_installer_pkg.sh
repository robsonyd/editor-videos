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
INSTALLER_RESOURCES_DIR="$BUILD_DIR/installer-resources"
APP_BUNDLE="$ROOT_DIR/Applications/$APP_NAME.app"
MACOS_DIR="$APP_BUNDLE/Contents/MacOS"
RESOURCES_DIR="$APP_BUNDLE/Contents/Resources"
SOURCE_DIR="$RESOURCES_DIR/source"
PKG_PATH="$DIST_DIR/EVR-Deluxe-Installer.pkg"
COMPONENT_PKG_PATH="$DIST_DIR/EVR-Deluxe-Component.pkg"
DISTRIBUTION_PATH="$BUILD_DIR/Distribution.xml"
INSTALLER_LICENSE_PATH="$INSTALLER_RESOURCES_DIR/License.txt"
APP_SIGN_IDENTITY="${EVR_APP_SIGN_IDENTITY:-}"
INSTALLER_SIGN_IDENTITY="${EVR_INSTALLER_SIGN_IDENTITY:-}"
APP_ENTITLEMENTS_PATH="$BUILD_DIR/EVRDeluxe.entitlements"
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

if [ "$(uname -m)" != "arm64" ]; then
  echo "Este instalador atualmente empacota runtime Apple Silicon (arm64). Gere um pacote separado para Intel/Universal." >&2
  exit 1
fi

rm -rf "$BUILD_DIR"
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR" "$SOURCE_DIR" "$SCRIPTS_DIR" "$INSTALLER_RESOURCES_DIR" "$DIST_DIR" "$VENDOR_DIR"

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

  "$bin_dir/ffmpeg" -hide_banner -version >/dev/null
  "$bin_dir/ffprobe" -hide_banner -version >/dev/null
  "$bin_dir/ffmpeg" -hide_banner -f lavfi -i color=c=black:s=16x16:d=0.1 -f null - >/dev/null 2>&1
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

write_entitlements() {
  cat > "$APP_ENTITLEMENTS_PATH" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.cs.allow-jit</key>
  <true/>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
  <true/>
  <key>com.apple.security.cs.disable-library-validation</key>
  <true/>
</dict>
</plist>
PLIST
}

sign_app_bundle() {
  if [ -z "$APP_SIGN_IDENTITY" ]; then
    echo "Assinatura do app: ignorada. Defina EVR_APP_SIGN_IDENTITY para assinar."
    return 0
  fi

  write_entitlements
  echo "Assinando binários internos com: $APP_SIGN_IDENTITY"

  while IFS= read -r -d '' file_path; do
    if file "$file_path" | grep -q "Mach-O"; then
      codesign \
        --force \
        --options runtime \
        --timestamp \
        --entitlements "$APP_ENTITLEMENTS_PATH" \
        --sign "$APP_SIGN_IDENTITY" \
        "$file_path"
    fi
  done < <(find "$APP_BUNDLE" -type f -print0)

  codesign \
    --force \
    --deep \
    --options runtime \
    --timestamp \
    --entitlements "$APP_ENTITLEMENTS_PATH" \
    --sign "$APP_SIGN_IDENTITY" \
    "$APP_BUNDLE"

  codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"
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
  cat > "$RESOURCES_DIR/launcher.sh" <<'LAUNCHER'
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
BOOTSTRAP_STATUS_HTML="$LOG_DIR/preparando-evr-deluxe.html"
BOOTSTRAP_STATUS_OPENED=0

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

write_bootstrap_status() {
  local title="$1"
  local detail="$2"
  local redirect_url="${3:-}"
  local refresh_tag='<meta http-equiv="refresh" content="2">'
  if [ -n "$redirect_url" ]; then
    refresh_tag="<meta http-equiv=\"refresh\" content=\"1; url=$redirect_url\">"
  fi

  mkdir -p "$LOG_DIR"
  cat > "$BOOTSTRAP_STATUS_HTML" <<HTML
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  $refresh_tag
  <title>Preparando EVR Deluxe</title>
  <style>
    body { margin:0; min-height:100vh; display:grid; place-items:center; background:#080908; color:#fff7df; font-family:-apple-system,BlinkMacSystemFont,"Inter","Segoe UI",sans-serif; }
    .panel { width:min(720px, calc(100vw - 40px)); padding:34px; border:1px solid rgba(241,220,148,.28); border-radius:24px; background:radial-gradient(circle at 20% 0%, rgba(241,220,148,.18), transparent 42%), rgba(20,21,20,.94); box-shadow:0 30px 90px rgba(0,0,0,.45); }
    .kicker { color:#58d8d1; text-transform:uppercase; letter-spacing:.12em; font-size:13px; font-weight:900; }
    h1 { margin:12px 0 10px; font-size:38px; line-height:1.05; }
    p { margin:0; color:rgba(255,247,223,.68); font-size:18px; line-height:1.45; }
    .bar { height:10px; margin-top:26px; overflow:hidden; border-radius:999px; background:rgba(255,255,255,.08); }
    .bar span { display:block; width:42%; height:100%; border-radius:inherit; background:linear-gradient(90deg,#58d8d1,#ffe18a); animation:load 1.2s ease-in-out infinite alternate; }
    small { display:block; margin-top:18px; color:rgba(255,247,223,.42); overflow-wrap:anywhere; }
    @keyframes load { from { transform:translateX(-35%); } to { transform:translateX(175%); } }
  </style>
</head>
<body>
  <main class="panel">
    <span class="kicker">EVR Deluxe</span>
    <h1>$title</h1>
    <p>$detail</p>
    <div class="bar" aria-hidden="true"><span></span></div>
    <small>Log técnico: $BOOTSTRAP_LOG</small>
  </main>
</body>
</html>
HTML
}

open_bootstrap_status() {
  if [ "$BOOTSTRAP_STATUS_OPENED" -eq 1 ]; then
    return 0
  fi
  BOOTSTRAP_STATUS_OPENED=1
  /usr/bin/open "$BOOTSTRAP_STATUS_HTML" >/dev/null 2>&1 || true
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
required = [
    "annotated_types",
    "anyio",
    "blinker",
    "certifi",
    "click",
    "distro",
    "dotenv",
    "flask",
    "h11",
    "httpcore",
    "httpx",
    "idna",
    "itsdangerous",
    "jinja2",
    "jiter",
    "markupsafe",
    "openai",
    "pydantic",
    "pydantic_core",
    "pyannote.audio",
    "requests",
    "sniffio",
    "torch",
    "tqdm",
    "typing_extensions",
    "typing_inspection",
    "urllib3",
    "werkzeug",
]
missing = [name for name in required if importlib.util.find_spec(name) is None]
raise SystemExit(1 if missing else 0)
PY
}

runtime_assets_ok() {
  local failed=0

  if [ "$(uname -m)" != "arm64" ]; then
    echo "Arquitetura não suportada neste pacote: $(uname -m). Este build é Apple Silicon (arm64)." >> "$BOOTSTRAP_LOG"
    failed=1
  fi

  if [ ! -x "$SUPPORT_DIR/bin/ffmpeg" ]; then
    echo "ffmpeg interno ausente ou sem permissão de execução: $SUPPORT_DIR/bin/ffmpeg" >> "$BOOTSTRAP_LOG"
    failed=1
  else
    "$SUPPORT_DIR/bin/ffmpeg" -hide_banner -version >> "$BOOTSTRAP_LOG" 2>&1 || failed=1
  fi

  if [ ! -x "$SUPPORT_DIR/bin/ffprobe" ]; then
    echo "ffprobe interno ausente ou sem permissão de execução: $SUPPORT_DIR/bin/ffprobe" >> "$BOOTSTRAP_LOG"
    failed=1
  else
    "$SUPPORT_DIR/bin/ffprobe" -hide_banner -version >> "$BOOTSTRAP_LOG" 2>&1 || failed=1
  fi

  if [ ! -x "$SUPPORT_DIR/whisper.cpp/build/bin/whisper-cli" ]; then
    echo "whisper-cli ausente ou sem permissão de execução." >> "$BOOTSTRAP_LOG"
    failed=1
  fi

  if [ ! -f "$SUPPORT_DIR/Modelos/ggml-base.bin" ]; then
    echo "Modelo Whisper ggml-base.bin ausente." >> "$BOOTSTRAP_LOG"
    failed=1
  fi

  return "$failed"
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
    write_bootstrap_status "Atualizando ambiente interno" "O EVR encontrou um Python antigo/incompatível e está reconstruindo o ambiente local."
    open_bootstrap_status
    notify "Atualizando ambiente Python interno do EVR Deluxe."
    /bin/rm -rf "$VENV_DIR" >> "$BOOTSTRAP_LOG" 2>&1
  fi

  if [ ! -x "$PYTHON_BIN" ]; then
    write_bootstrap_status "Preparando ambiente Python" "Primeira abertura: criando o ambiente interno do EVR Deluxe. Isso pode levar alguns minutos."
    open_bootstrap_status
    notify "Preparando ambiente Python. A primeira abertura pode levar alguns minutos."
    "$RUNTIME_PYTHON_BIN" -m venv "$VENV_DIR" >> "$BOOTSTRAP_LOG" 2>&1
  fi

  if ! dependencies_ok || requirements_changed; then
    write_bootstrap_status "Instalando dependências" "O EVR Deluxe está instalando bibliotecas internas. Não feche esta janela; ela vai abrir o app ao terminar."
    open_bootstrap_status
    notify "Instalando dependências do EVR Deluxe. Isso pode demorar na primeira abertura."
    "$PYTHON_BIN" -m pip install --upgrade pip >> "$BOOTSTRAP_LOG" 2>&1
    "$PYTHON_BIN" -m pip install -r "$APP_DIR/requirements.txt" >> "$BOOTSTRAP_LOG" 2>&1

    if ! dependencies_ok; then
      write_bootstrap_status "Reparando dependências" "Algumas bibliotecas não ficaram corretas. O EVR está tentando reparar automaticamente."
      notify "Reparando ambiente Python do EVR Deluxe."
      "$PYTHON_BIN" -m pip install --force-reinstall --no-deps -r "$APP_DIR/requirements.txt" >> "$BOOTSTRAP_LOG" 2>&1
    fi

    if ! dependencies_ok; then
      write_bootstrap_status "Reconstruindo dependências" "O EVR está reconstruindo o ambiente Python interno para corrigir a instalação."
      notify "Reconstruindo dependências do EVR Deluxe."
      "$PYTHON_BIN" -m pip install --force-reinstall -r "$APP_DIR/requirements.txt" >> "$BOOTSTRAP_LOG" 2>&1
    fi

    if ! dependencies_ok; then
      show_error "Não consegui instalar todas as dependências. Reinstale o pacote mais recente. Se precisar, peça ajuda ao ChatGPT colando esta mensagem. Se continuar, envie para Robson o log técnico em: $BOOTSTRAP_LOG"
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

open_browser_window() {
  local chrome_binary="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  if [ -x "$chrome_binary" ]; then
    "$chrome_binary" --app="$URL" --new-window >/dev/null 2>&1 &
    return 0
  fi

  /usr/bin/open -b com.google.Chrome "$URL" 2>/dev/null || /usr/bin/open "$URL"
}

monitor_browser() {
  open_browser_window

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
if ! runtime_assets_ok; then
  write_bootstrap_status "Instalação incompleta" "O EVR encontrou falha em FFmpeg, FFprobe, Whisper, modelo local ou arquitetura do Mac. Reinstale o pacote mais recente. Se precisar, peça ajuda ao ChatGPT colando esta mensagem."
  open_bootstrap_status
  show_error "Instalação incompleta do EVR Deluxe. Reinstale o pacote mais recente. Se precisar, peça ajuda ao ChatGPT colando esta mensagem. Se continuar, envie para Robson o log técnico em: $BOOTSTRAP_LOG"
  exit 1
fi
bootstrap_python
start_server
if [ "$BOOTSTRAP_STATUS_OPENED" -eq 1 ]; then
  write_bootstrap_status "EVR pronto" "Ambiente preparado. Abrindo o EVR Deluxe agora." "$URL"
fi
monitor_browser
LAUNCHER

  chmod +x "$RESOURCES_DIR/launcher.sh"

  cat > "$BUILD_DIR/evr_launcher.c" <<'C'
#include <mach-o/dyld.h>
#include <limits.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(void) {
  char executable_path[PATH_MAX];
  uint32_t size = sizeof(executable_path);
  if (_NSGetExecutablePath(executable_path, &size) != 0) {
    return 1;
  }

  char *last_slash = strrchr(executable_path, '/');
  if (!last_slash) {
    return 1;
  }
  *last_slash = '\0';

  char script_path[PATH_MAX];
  int written = snprintf(script_path, sizeof(script_path), "%s/../Resources/launcher.sh", executable_path);
  if (written < 0 || written >= (int)sizeof(script_path)) {
    return 1;
  }

  execl("/bin/bash", "bash", script_path, (char *)NULL);
  return 1;
}
C

  /usr/bin/clang "$BUILD_DIR/evr_launcher.c" -o "$MACOS_DIR/$APP_NAME"
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

write_installer_license() {
  cat > "$INSTALLER_LICENSE_PATH" <<'LICENSE'
EVR DELUXE - TERMOS DE USO E PRIVACIDADE

Antes de instalar e utilizar o EVR Deluxe, leia os Termos de Uso e Política de Privacidade oficiais incluídos no aplicativo.

Ao continuar a instalação, você declara que leu e concorda com os Termos de Uso e Política de Privacidade do EVR Deluxe. Você reconhece que o uso da ferramenta é local, pessoal e de sua exclusiva responsabilidade; que deve cumprir LGPD, direitos autorais, imagem, voz, privacidade e demais leis aplicáveis; e que Robson Yuri não coleta, não armazena, não acessa, não fornece APIs e não manipula seus conteúdos ou dados.

Resumo operacional:
- O EVR Deluxe é uma ferramenta local para processamento de vídeos, transcrições, cortes, ganchos e organização de projetos.
- O usuário é responsável pelos arquivos enviados ao aplicativo e pelas credenciais de serviços externos configuradas na ferramenta.
- Algumas funcionalidades dependem de serviços de terceiros, como provedores de IA, Hugging Face/Pyannote, FFmpeg e modelos locais.
- O uso de APIs externas pode gerar custos na conta do próprio usuário ou da empresa, conforme as regras de cada fornecedor.
- O EVR Deluxe pode armazenar configurações, histórico local, logs técnicos e arquivos processados na máquina do usuário.
- Erros em integrações experimentais devem ser reportados a Robson Yuri para análise.

O documento completo em PDF estará disponível no primeiro uso do aplicativo e fica incluído nos arquivos internos do EVR Deluxe.
LICENSE
}

write_distribution() {
  cat > "$DISTRIBUTION_PATH" <<XML
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="1">
  <title>$APP_NAME</title>
  <license file="License.txt" mime-type="text/plain"/>
  <options customize="never" require-scripts="false"/>
  <choices-outline>
    <line choice="default"/>
  </choices-outline>
  <choice id="default" title="$APP_NAME">
    <pkg-ref id="$IDENTIFIER"/>
  </choice>
  <pkg-ref id="$IDENTIFIER" version="$VERSION" onConclusion="none">$(basename "$COMPONENT_PKG_PATH")</pkg-ref>
</installer-gui-script>
XML
}

ensure_python_runtime
copy_clean_app
copy_runtime_assets
copy_ffmpeg_runtime
patch_whisper_rpath
write_info_plist
write_launcher
write_postinstall
write_installer_license
write_distribution
sign_app_bundle

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
  "$COMPONENT_PKG_PATH"

productbuild_args=(
  --distribution "$DISTRIBUTION_PATH"
  --resources "$INSTALLER_RESOURCES_DIR"
  --package-path "$DIST_DIR"
)

if [ -n "$INSTALLER_SIGN_IDENTITY" ]; then
  productbuild_args+=(--sign "$INSTALLER_SIGN_IDENTITY" --timestamp)
fi

productbuild "${productbuild_args[@]}" "$PKG_PATH"

echo "Instalador gerado em: $PKG_PATH"
du -sh "$PKG_PATH"
