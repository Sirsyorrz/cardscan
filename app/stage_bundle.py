"""Fills app/src-tauri/bundle-res/ with what an installer ships: the scripts, uv, node and a seed cards.db.

    python app/stage_bundle.py windows|linux

Python and the packages are not bundled; the app installs them with uv on first run. Needs data/cards.db
(tools/build_db.py). Downloads are cached in src-tauri/target/bundle-downloads/.
"""
import json
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

UV_VERSION = "0.11.17"
NODE_VERSION = "26.12.1"

APP = Path(__file__).resolve().parent
REPO = APP.parent
RES = APP / "src-tauri" / "bundle-res"
DOWNLOADS = APP / "src-tauri" / "target" / "bundle-downloads"

PLATFORMS = {
    "windows": {
        "uv": (f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-x86_64-pc-windows-msvc.zip",
               "uv.exe", "uv.exe"),
        "node": (f"https://nodejs.org/dist/v{NODE_VERSION}/node-v{NODE_VERSION}-win-x64.zip",
                 f"node-v{NODE_VERSION}-win-x64/node.exe", "node.exe"),
    },
    "linux": {
        "uv": (f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz",
               "uv-x86_64-unknown-linux-gnu/uv", "uv"),
        "node": (f"https://nodejs.org/dist/v{NODE_VERSION}/node-v{NODE_VERSION}-linux-x64.tar.xz",
                 f"node-v{NODE_VERSION}-linux-x64/bin/node", "node"),
    },
}


def fetch(url):
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    path = DOWNLOADS / url.rsplit("/", 1)[1]
    if not path.exists():
        part = path.with_suffix(path.suffix + ".part")
        with urllib.request.urlopen(url) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f)
        part.rename(path)
    return path


def extract(archive, member, dest):
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            data = z.read(member)
    else:
        with tarfile.open(archive) as t:
            data = t.extractfile(member).read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    dest.chmod(0o755)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in PLATFORMS:
        sys.exit(__doc__)
    platform = PLATFORMS[sys.argv[1]]
    cards = REPO / "data" / "cards.db"
    if not cards.exists():
        sys.exit(f"{cards} is missing; run tools/build_db.py first")

    shutil.rmtree(RES, ignore_errors=True)
    app = RES / "app"
    (app / "tools").mkdir(parents=True)
    (app / "matcher").mkdir()
    for f in [*REPO.glob("*.py"), REPO / "requirements.txt"]:
        shutil.copy2(f, app)
    shutil.copy2(REPO / "tools" / "build_db.py", app / "tools")
    for f in (REPO / "matcher").glob("*.ts"):
        if not f.name.endswith(".test.ts"):
            shutil.copy2(f, app / "matcher")
    shutil.copytree(REPO / "static", app / "static")
    # node runs matcher/*.ts as ES modules only under a package.json that says so
    (app / "package.json").write_text(json.dumps({"type": "module"}) + "\n")
    (RES / "seed").mkdir()
    shutil.copy2(cards, RES / "seed")

    url, member, name = platform["uv"]
    extract(fetch(url), member, RES / name)
    url, member, name = platform["node"]
    extract(fetch(url), member, RES / "node" / name)
    print(f"staged {RES}")


if __name__ == "__main__":
    main()
