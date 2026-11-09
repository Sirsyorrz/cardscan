#!/usr/bin/env bash
# Cross-builds the Windows installer on Linux. Needs, outside the system toolchain:
#   a rustup with the x86_64-pc-windows-msvc target and cargo-xwin, in $RUSTUP_HOME/$CARGO_HOME below
#   a native makensis with the Windows NSIS 3.13 Stubs/Plugins/Include/Contrib in its parent folder
# The installer bundles the scripts, uv, node and the current data/cards.db; Python and the packages
# are installed by the app on first run.
set -euo pipefail
cd "$(dirname "$0")"
app=$PWD
repo=$(cd .. && pwd)

tools=${CARDSCAN_WIN_TOOLS:-$HOME/.local/share}
export RUSTUP_HOME=$tools/rustup-win CARGO_HOME=$tools/cargo-win
export PATH=$CARGO_HOME/bin:$tools/nsis-build/nsis/bin:$PATH

UV_VERSION=0.11.17
NODE_VERSION=26.12.1
target=x86_64-pc-windows-msvc
downloads=$app/src-tauri/target/win-downloads
res=$app/src-tauri/winres

mkdir -p "$downloads"
fetch() {
  [ -f "$downloads/$2" ] && return
  curl -fsSL -o "$downloads/$2.part" "$1"
  mv "$downloads/$2.part" "$downloads/$2"
}
fetch "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$target.zip" "uv-$UV_VERSION.zip"
fetch "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-win-x64.zip" "node-$NODE_VERSION.zip"

rm -rf "$res"
mkdir -p "$res/app/tools" "$res/app/matcher" "$res/node" "$res/seed"
cp "$repo"/*.py "$repo/requirements.txt" "$res/app/"
cp "$repo/tools/build_db.py" "$res/app/tools/"
find "$repo/matcher" -maxdepth 1 -name '*.ts' ! -name '*.test.ts' -exec cp {} "$res/app/matcher/" \;
cp -r "$repo/static" "$res/app/"
# node runs matcher/*.ts as ES modules only under a package.json that says so
printf '{ "type": "module" }\n' > "$res/app/package.json"
cp "$repo/data/cards.db" "$res/seed/"
unzip -q -j -o "$downloads/uv-$UV_VERSION.zip" uv.exe -d "$res"
unzip -q -j -o "$downloads/node-$NODE_VERSION.zip" "node-v$NODE_VERSION-win-x64/node.exe" -d "$res/node"

npx tauri build --runner cargo-xwin --target "$target" --config src-tauri/tauri.windows.conf.json
ls -lh "$app/src-tauri/target/$target/release/bundle/nsis/"
