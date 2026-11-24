#!/bin/sh
# WiFi book uploader.
# Started at every boot by the baked hook in /etc/rc.local (backgrounded).
# Edit this file on the reader's USB drive to change behaviour; reboot to apply.
#
# IMPORTANT: logs go to tmpfs (/tmp), NOT /mnt/onboard. A process that keeps a
# file open on /mnt/onboard prevents "Connect to PC" from unmounting it, which
# makes the drive show up empty on the computer. View logs over WiFi at
# http://<reader-ip>:8080/log instead.

CUSTOM_DIR=/mnt/onboard/.custom
SERVER="$CUSTOM_DIR/bookserver.py"
PORT=8080
LOG=/tmp/bookserver.log

# Never keep the drive as our working directory either.
cd / || exit 1

# Idempotent: never start a second copy.
if pgrep -f "bookserver.py" >/dev/null 2>&1; then
    exit 0
fi

if [ ! -f "$SERVER" ]; then
    exit 1
fi

nohup python3 "$SERVER" >"$LOG" 2>&1 &

# Best effort: once WiFi has an IP, show the URL on the e-ink screen.
# Redirected to /dev/null so this subshell never holds the drive open.
(
    i=0
    while [ "$i" -lt 20 ]; do
        ip_addr=$(ip -4 addr show 2>/dev/null \
            | awk '/inet /{print $2}' | cut -d/ -f1 | grep -v '^127' | head -1)
        if [ -n "$ip_addr" ]; then
            fbink -q -y 1 "Book upload: http://$ip_addr:$PORT" 2>/dev/null
            break
        fi
        i=$((i + 1))
        sleep 3
    done
) >/dev/null 2>&1 &

exit 0
