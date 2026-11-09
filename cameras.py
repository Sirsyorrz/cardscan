"""Prints the cameras as JSON lines ({"path", "name"}) for the app on Windows, where `path` is the DirectShow index
scan.py opens. Linux needs none of this: the app reads /sys/class/video4linux itself."""

import json

import cv2
from cv2_enumerate_cameras import enumerate_cameras

for cam in enumerate_cameras(cv2.CAP_DSHOW):
    print(json.dumps({"path": str(cam.index), "name": cam.name}))
