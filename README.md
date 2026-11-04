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

## Use

Plug the phone in and pick "Webcam" in its USB options, then switch the webcam preview to the back camera. It shows
up as "Pixel 9 Pro XL: Android Webcam" (`/dev/video2` here; `cat /sys/class/video4linux/video*/name` to check).

```fish
.venv/bin/python scan.py /dev/video2 --show            # q or Esc to stop
.venv/bin/python scan.py clip.mkv --out /tmp/caps       # replay a recording
.venv/bin/python scan.py /dev/video2 --show --record /tmp/session.mkv
```

`--out` (default `captures/`) gets each card straightened and upright, `sheet.jpg` with every capture labelled, and
`session.json`: one entry per capture with the match and a status:

- `added`: identified (`confidence` high: name plus set code or number agree; medium: one of them fuzzy or missing)
- `review`: no match, a low-confidence guess, or a capture that wasn't a clean card outline (a card stuck on its edge)
  unless it still matched with high confidence
- `repeat`: the same name as the card before, i.e. the same card read again after it slid. A second copy is never
  dropped straight after the first; add extra copies by hand. The surer of the two reads keeps the printing.

## How it works

- `watch.py` decides when a card has landed: the picture around the pile goes still and differs from the last
  still picture. `detect.py` (ported from the old Android app) finds the exact outline on a crop around it.
- `identify.py` reads the name strip both ways up (whichever gives a known name is up) and the collector strip,
  with RapidOCR on the GPU on a worker thread: about 0.5 s a card, twice that when the first read is unsure and a
  looser crop is tried. A rough capture that matches nothing gets searched again for the card at full resolution.
- `matcher/matcher.ts` is a long-running Node process that resolves the OCR lines with `matcher/match.ts` against
  `data/cards.db`: name (fuzzy), set code and collector number, for Magic, Pokémon and Japanese Pokémon.

## Camera

The phone has to be fixed above the pile (a stand or arm, pointing straight down); camera shake reads as motion and
cards get missed. Frame it so the card fills most of the picture, and keep window light off it. Cards catch on
sloped tray walls and stand on their edge; a flat surface with a low lip, or dropping from just above the pile,
avoids that.

Webcam mode tops out at 1920x1080. If collector numbers come out too soft, `phonecam.fish` streams the camera through
scrcpy instead (needs USB debugging rather than webcam mode, plus
`shelly install standard scrcpy v4l2loopback-utils`), at any size the camera supports:
`./phonecam.fish 3840x2160`, then `scan.py /dev/video42 --size 3840x2160`.
