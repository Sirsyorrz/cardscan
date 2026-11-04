#!/usr/bin/env fish
# Phone's back camera as /dev/video42 over USB. Usage: phonecam.fish [WIDTHxHEIGHT] (default 1920x1080)
# /dev/video10 is NVIDIA Broadcast's loopback, so this adds its own device instead of reloading the module.

set -l dev /dev/video42
set -l size (set -q argv[1]; and echo $argv[1]; or echo 1920x1080)

if not test -e $dev
    sudo v4l2loopback-ctl add -n "Phone Cam" -x 0 $dev; or exit 1
end

# The default 8 Mbps smears the small collector-number text.
scrcpy --video-source=camera --camera-facing=back --camera-size=$size --no-audio \
    --video-bit-rate=24M --v4l2-sink=$dev --no-video-playback
