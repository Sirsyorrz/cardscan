# cardscan

Bulk card scanning (Magic, Pokémon, Japanese Pokémon) on the PC: the phone's camera over USB, cards dropped one at
a time on a pile, each one identified as it lands.

## Setup

```fish
python3 -m venv .venv; .venv/bin/pip install -r requirements.txt; .venv/bin/pip install --force-reinstall --no-deps opencv-python==5.0.0.93
```

The second install matters: RapidOCR pulls in `opencv-python-headless`, which shares the `cv2` folder and leaves it
without window support, so `--show` fails until the full build is reinstalled over it.

The card database (`data/cards.db`, about 100 MB, not in git) is built from Scryfall, TCGdex, tcgcsv and Card
Kingdom; downloads are cached in `tools/cache/`:

```fish
python3 tools/build_db.py
```

The matcher needs Node 22.18+ (it runs the TypeScript directly). `npm install` only fetches TypeScript for
`npm run typecheck`; `npm test` runs the matcher tests against `data/cards.db`.

## App

`app/` is a desktop window over the scripts below (Tauri + React, this PC only: it runs the Python in this
folder's `.venv`). Scan has the camera picker, Start/Stop, the finish toggle (Normal, Foil / Reverse, Holo, switchable
mid-run) and the live feed with the cards identified so far. Review and Collection are the pages review.py served,
and Settings has recording, price refresh and the card data rebuild.

```fish
cd ~/Documents/GitHub/cardscan/app; npm install; ./run.sh    # builds and starts it
```

After a build it's also in the app menu as CardScan (`~/.local/share/applications/cardscan.desktop`). The scripts
still work on their own:

## Use

Plug the phone in and pick "Webcam" in its USB options, then switch the webcam preview to the back camera. It shows
up as "Pixel 9 Pro XL: Android Webcam" (`/dev/video2` here; `cat /sys/class/video4linux/video*/name` to check).

```fish
.venv/bin/python scan.py /dev/video2 --show            # q or Esc to stop
.venv/bin/python scan.py /dev/video2 --show --foil     # start with the finish toggle on foil
.venv/bin/python review.py                             # http://localhost:8765, alongside or afterwards
.venv/bin/python prices.py                             # refresh prices of everything owned
```

The window's top-right button (or F) sets the finish of the cards you drop next: Normal, Foil / Reverse (Magic
foil, Pokémon reverse holo) or Holo (Pokémon holo; Magic foil). A card gets the setting from when it landed. When
a card doesn't come in that finish, it gets the nearest one it does.

Each run is a session: its captures (straightened, upright) go to `captures/<session>/` with a labelled
`sheet.jpg`, and every capture is recorded in `data/collection.db` with a status:

- `added`: identified and counted in the collection (`confidence` high: name plus set code or number agree;
  medium: one of them fuzzy or missing, or the printing chosen from the artwork)
- `review`: no match, a low-confidence guess, or a capture that wasn't a clean card outline (a card stuck on its
  edge) unless it still matched with high confidence. Not counted until confirmed.
- `repeat`: the same name as the card before, i.e. the same card read again after it slid. A second copy is never
  dropped straight after the first; add extra copies by hand. The surer of the two reads keeps the printing.
- `rejected`: marked "not a card" on the review page.

The review page shows a session's captures next to the matched card. Pick another printing or search for the right
card, set finish and condition, mark it not a card, or add more copies (keys: J/K to move, Enter to add, X to
reject, / to search). New captures appear while a scan is running. The Collection tab lists what you own with
prices in AUD, +/- per row, and a price refresh.

Recording a session (`--record /tmp/session.mkv`) makes it replayable for tuning without touching the collection:
`scan.py /tmp/session.mkv --no-db --out /tmp/caps`.

The collection is only in `data/collection.db` (not in git); back it up.

## How it works

- `watch.py` decides when a card has landed: the picture around the pile goes still and differs from the last
  still picture. `detect.py` (ported from the old Android app) finds the exact outline on a crop around it.
- `identify.py` reads the name strip both ways up (whichever gives a known name is up) and the collector strip,
  with RapidOCR on the GPU on a worker thread: about 0.5 s a card, twice that when the first read is unsure and a
  looser crop is tried. A rough capture that matches nothing gets searched again for the card at full resolution.
- `matcher/matcher.ts` is a long-running Node process that resolves the OCR lines with `matcher/match.ts` against
  `data/cards.db`: name (fuzzy), set code and collector number, for Magic, Pokémon and Japanese Pokémon.
- `art.py`: when the name and set were read but the collector number wasn't, the capture is compared with the
  official image of each printing in that set (showcase, borderless and so on); images are cached in `data/images/`.
- `collection.py` keeps the collection: owned copies by card, finish and condition, plus every session and capture.
  Changing a capture's card or status moves its copy.
- `tools/build_db.py` builds `data/cards.db`. Pokémon sets TCGplayer lists before TCGdex has them (new releases)
  come from TCGplayer's product data alone (ids `tcg-<productId>`, no artist or legality, images once TCGplayer has
  them); when a later build has the real cards, owned copies move over by product id.

## Camera

The phone has to be fixed above the pile (a stand or arm, pointing straight down); camera shake reads as motion and
cards get missed. Frame it so the card fills most of the picture, and keep window light off it. Cards catch on
sloped tray walls and stand on their edge; a flat surface with a low lip, or dropping from just above the pile,
avoids that.

Webcam mode tops out at 1920x1080. If collector numbers come out too soft, `phonecam.fish` streams the camera through
scrcpy instead (needs USB debugging rather than webcam mode, plus
`shelly install standard scrcpy v4l2loopback-utils`), at any size the camera supports:
`./phonecam.fish 3840x2160`, then `scan.py /dev/video42 --size 3840x2160`.
