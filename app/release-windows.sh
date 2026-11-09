#!/usr/bin/env bash
# Cross-builds the Windows installer on Linux, for testing without CI. Needs, outside the system toolchain:
#   a rustup with the x86_64-pc-windows-msvc target and cargo-xwin, in $RUSTUP_HOME/$CARGO_HOME below
#   a native makensis with the Windows NSIS 3.13 Stubs/Plugins/Include/Contrib in its parent folder
# Signs with ~/.tauri/cardscan.key unless TAURI_SIGNING_PRIVATE_KEY is set.
set -euo pipefail
cd "$(dirname "$0")"

tools=${CARDSCAN_WIN_TOOLS:-$HOME/.local/share}
export RUSTUP_HOME=$tools/rustup-win CARGO_HOME=$tools/cargo-win
export PATH=$CARGO_HOME/bin:$tools/nsis-build/nsis/bin:$PATH
if [ -z "${TAURI_SIGNING_PRIVATE_KEY:-}" ]; then
  export TAURI_SIGNING_PRIVATE_KEY=$(cat ~/.tauri/cardscan.key)
  export TAURI_SIGNING_PRIVATE_KEY_PASSWORD=$(cat ~/.tauri/cardscan.password)
fi

target=x86_64-pc-windows-msvc
python3 stage_bundle.py windows
npx tauri build --runner cargo-xwin --target "$target" --bundles nsis --config src-tauri/tauri.bundle.conf.json
ls -lh "src-tauri/target/$target/release/bundle/nsis/"
