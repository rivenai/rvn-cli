#!/bin/sh
# rvn installer — curl -fsSL https://dl.rivenai.io/rvn/install.sh | sh
# Downloads the prebuilt single-file binary from dl.rivenai.io into ~/.rvn/bin
# (or /usr/local/bin when writable and RUNVN_INSTALL_PREFIX is unset).
set -eu

VERSION="${RVN_VERSION:-latest}"
BASE="https://dl.rivenai.io/rvn"
OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
  Darwin) TARGET="rvn-macos-universal2" ;;
  Linux)
    case "$ARCH" in
      x86_64|amd64) TARGET="rvn-linux-x64" ;;
      aarch64|arm64) TARGET="rvn-linux-arm64" ;;
      *) echo "rvn: unsupported linux arch $ARCH" >&2; exit 1 ;;
    esac
    ;;
  *) echo "rvn: unsupported OS $OS (use pip: pip install rvn-cli)" >&2; exit 1 ;;
esac

if [ "$VERSION" = "latest" ]; then
  URL="$BASE/latest/$TARGET"
else
  URL="$BASE/$VERSION/$TARGET"
fi

PREFIX="${RVN_INSTALL_PREFIX:-$HOME/.rvn}"
BINDIR="$PREFIX/bin"
mkdir -p "$BINDIR"
TMP="$(mktemp -t rvn.XXXXXX)"
trap 'rm -f "$TMP"' EXIT

echo "Downloading $URL"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$URL" -o "$TMP"
else
  wget -qO "$TMP" "$URL"
fi

chmod +x "$TMP"
DEST="$BINDIR/rvn"
mv "$TMP" "$DEST"
trap - EXIT

echo "Installed: $DEST"
"$DEST" --version

case ":$PATH:" in
  *":$BINDIR:"*) ;;
  *) echo "note: add $BINDIR to your PATH" ;;
esac

echo "Next: rvn login   (stores your rvn_* key at ~/.rvn/key)"
