#!/usr/bin/env bash
# Builds static/css/app.css from assets/app.css with Tailwind CSS + daisyUI.
# Uses Tailwind's standalone CLI, so Node.js isn't needed. Pinned tools are
# downloaded once into .tools/ and checked against their published SHA-256.
#
#   scripts/build-css.sh            # one-off build (minified)
#   scripts/build-css.sh --watch    # rebuild on template changes while developing
set -euo pipefail

TAILWIND_VERSION="v4.3.3"
DAISYUI_VERSION="v5.7.50"

DAISYUI_SHA256="d69de07c7463bb1f41548e2688f88ee105cdadf9779fce5b3c47b3bfaa6be472"
DAISYUI_THEME_SHA256="e48a35d8326d650181ca600560e8520cf6b44cf9242bbd9d45825607e641f251"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="$ROOT/.tools"
mkdir -p "$TOOLS" "$ROOT/assets/vendor" "$ROOT/static/css"

sha256() {
  if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

# fetch <url> <destination> <expected sha256>
fetch() {
  if [[ -f "$2" && "$(sha256 "$2")" == "$3" ]]; then return; fi
  echo "Downloading $(basename "$2")..."
  curl -fsSL "$1" -o "$2.tmp"
  if [[ "$(sha256 "$2.tmp")" != "$3" ]]; then
    rm -f "$2.tmp"
    echo "Checksum mismatch for $1" >&2
    exit 1
  fi
  mv "$2.tmp" "$2"
}

# Plain case statement rather than an associative array so macOS's bash 3 works too
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) platform="linux-x64" tailwind_sha256="dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a" ;;
  Linux-aarch64 | Linux-arm64) platform="linux-arm64" tailwind_sha256="55fd0b241214eff3de1e8ee4f22796662f2d2e7a49bcfca7477cfd0bac398195" ;;
  Darwin-x86_64) platform="macos-x64" tailwind_sha256="7922e0953f2110c05976e3bf58f14e643d90427575e766b7d433f5f80cbee7e1" ;;
  Darwin-arm64) platform="macos-arm64" tailwind_sha256="cdf646702987a743464dff4d9c60fd4480d1c1e73dd819a9a67f1078815dce9d" ;;
  *) echo "Unsupported platform: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

tailwind="$TOOLS/tailwindcss-$TAILWIND_VERSION-$platform"
fetch "https://github.com/tailwindlabs/tailwindcss/releases/download/$TAILWIND_VERSION/tailwindcss-$platform" \
  "$tailwind" "$tailwind_sha256"
chmod +x "$tailwind"

# daisyUI's single-file plugins, referenced from assets/app.css
daisyui_url="https://github.com/saadeghi/daisyui/releases/download/$DAISYUI_VERSION"
fetch "$daisyui_url/daisyui.mjs" "$ROOT/assets/vendor/daisyui.mjs" "$DAISYUI_SHA256"
fetch "$daisyui_url/daisyui-theme.mjs" "$ROOT/assets/vendor/daisyui-theme.mjs" "$DAISYUI_THEME_SHA256"

cd "$ROOT"
if [[ "${1:-}" == "--watch" ]]; then
  exec "$tailwind" -i assets/app.css -o static/css/app.css --watch
fi
"$tailwind" -i assets/app.css -o static/css/app.css --minify
