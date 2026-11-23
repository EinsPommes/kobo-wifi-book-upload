#!/bin/sh
# WiFi book uploader.
# Started at every boot by the baked hook in /etc/rc.local (backgrounded).
# Edit this file on the reader's USB drive to change behaviour; reboot to apply.
# Output is logged to /mnt/onboard/.custom/custom.log (this launcher) and
# bookserver.log (the server itself).

CUSTOM_DIR=/mnt/onboard/.custom
SERVER="$CUSTOM_DIR/bookserver.py"
PORT=8080

# Idempotent: never start a second copy.
if pgrep -f "bookserver.py" >/dev/null 2>&1; then
    echo "book uploader already running"
    exit 0
fi

if [ ! -f "$SERVER" ]; then
    echo "server script not found: $SERVER"
    exit 1
fi

echo "starting book uploader on port $PORT"
nohup python3 "$SERVER" >"$CUSTOM_DIR/bookserver.log" 2>&1 &

# Best effort: once WiFi has an IP, show the URL on the e-ink screen.
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
) &

exit 0
