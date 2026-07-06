#!/bin/bash
set -euo pipefail

APP_NAME="EVR Deluxe"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INSTALLER_DIR="$PROJECT_DIR/instalador"
DIST_DIR="$INSTALLER_DIR/dist"
BUILD_SCRIPT="$INSTALLER_DIR/evr_deluxe_installer_pkg.sh"
UNSIGNED_PKG="$DIST_DIR/EVR-Deluxe-Installer-unsigned.pkg"
SIGNED_PKG="$DIST_DIR/EVR-Deluxe-Installer.pkg"
NOTARY_PROFILE="${EVR_NOTARY_PROFILE:-EVR_DELUXE_NOTARY}"

find_identity() {
  local label="$1"
  security find-identity -v | sed -n "s/.*\"\($label:.*\)\".*/\1/p" | head -n 1
}

APP_SIGN_IDENTITY="${EVR_APP_SIGN_IDENTITY:-$(find_identity "Developer ID Application")}"
INSTALLER_SIGN_IDENTITY="${EVR_INSTALLER_SIGN_IDENTITY:-$(find_identity "Developer ID Installer")}"

if [ -z "$APP_SIGN_IDENTITY" ]; then
  echo "Certificado Developer ID Application não encontrado no Keychain." >&2
  echo "Defina EVR_APP_SIGN_IDENTITY ou instale o certificado no Keychain." >&2
  exit 1
fi

if [ -z "$INSTALLER_SIGN_IDENTITY" ]; then
  echo "Certificado Developer ID Installer não encontrado no Keychain." >&2
  echo "Defina EVR_INSTALLER_SIGN_IDENTITY ou instale o certificado no Keychain." >&2
  exit 1
fi

echo "App:       $APP_SIGN_IDENTITY"
echo "Package:   $INSTALLER_SIGN_IDENTITY"
echo "Notary:    $NOTARY_PROFILE"
echo

EVR_APP_SIGN_IDENTITY="$APP_SIGN_IDENTITY" "$BUILD_SCRIPT"

rm -f "$UNSIGNED_PKG"
mv "$SIGNED_PKG" "$UNSIGNED_PKG"

productsign \
  --sign "$INSTALLER_SIGN_IDENTITY" \
  "$UNSIGNED_PKG" \
  "$SIGNED_PKG"

pkgutil --check-signature "$SIGNED_PKG"

if [ "${EVR_SKIP_NOTARY:-0}" = "1" ]; then
  echo
  echo "Notarização ignorada porque EVR_SKIP_NOTARY=1."
  echo "Pacote assinado gerado em: $SIGNED_PKG"
  exit 0
fi

xcrun notarytool submit "$SIGNED_PKG" \
  --keychain-profile "$NOTARY_PROFILE" \
  --wait

xcrun stapler staple "$SIGNED_PKG"
spctl -a -vv -t install "$SIGNED_PKG"

echo
echo "Pacote assinado e notarizado gerado em: $SIGNED_PKG"
