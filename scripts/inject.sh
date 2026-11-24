#!/bin/sh
# Bake the WiFi book uploader into a decompressed ext4 rootfs image, so the
# feature works right after flashing (self-contained) with no drive files.
#
# Usage:  inject.sh /path/to/rootfs.ext4
#
# Installs:
#   /opt/custom/bookserver.py   the server (copied from this repo's src/)
#   /opt/custom/startup.sh      the baked loader
# and appends a one-line hook to the existing /etc/rc.local (before `exit 0`).
#
# The loader runs the baked server by default; a file
# /mnt/onboard/.custom/startup.sh overrides it (power-user hook), and
# /mnt/onboard/.custom/DISABLE disables everything (anti-lockout). Logs go to
# tmpfs so nothing ever holds /mnt/onboard open (USB "Connect" keeps working).
#
# Uses debugfs: no mount, no root. Requires e2fsprogs in PATH. Only the rootfs
# is touched; kernel/U-Boot/TEE stay untouched, so their signatures stay valid.
# After running this, recompress the image and rebuild your package checksums.

set -e

IMG="$1"
[ -n "$IMG" ] && [ -f "$IMG" ] || { echo "usage: $0 /path/to/rootfs.ext4" >&2; exit 1; }
command -v debugfs >/dev/null || { echo "debugfs not found (install e2fsprogs)" >&2; exit 1; }

script_dir=$(cd "$(dirname "$0")" && pwd)
server_src="$script_dir/../src/bookserver.py"
[ -f "$server_src" ] || { echo "server source not found: $server_src" >&2; exit 1; }

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

# Baked loader.
cat > "$workdir/startup.sh" <<'EOF'
#!/bin/sh
# Baked loader for the WiFi book uploader (self-contained).
# - Runs the baked server by default (works right after flashing).
# - /mnt/onboard/.custom/startup.sh overrides it (power-user hook).
# - /mnt/onboard/.custom/DISABLE skips everything (anti-lockout).
# - Logs to tmpfs so this never holds /mnt/onboard open (USB "Connect" works).
CUSTOM_DIR=/mnt/onboard/.custom
OVERRIDE="$CUSTOM_DIR/startup.sh"
BAKED_SERVER=/opt/custom/bookserver.py
PORT=8080
LOG=/tmp/custom-loader.log

cd / || exit 0
[ -f "$CUSTOM_DIR/DISABLE" ] && exit 0

{
    echo "=== custom loader: $(date) ==="
    if [ -f "$OVERRIDE" ]; then
        echo "running drive override: $OVERRIDE"
        /bin/sh "$OVERRIDE"
    else
        echo "running baked uploader: $BAKED_SERVER"
        if ! pgrep -f "bookserver.py" >/dev/null 2>&1; then
            nohup python3 "$BAKED_SERVER" >/tmp/bookserver.log 2>&1 &
        fi
        (
            i=0
            while [ "$i" -lt 20 ]; do
                ip=$(ip -4 addr show 2>/dev/null | awk '/inet /{print $2}' \
                     | cut -d/ -f1 | grep -v '^127' | head -1)
                if [ -n "$ip" ]; then
                    fbink -q -y 1 "Book upload: http://$ip:$PORT" 2>/dev/null
                    break
                fi
                i=$((i + 1)); sleep 3
            done
        ) >/dev/null 2>&1 &
    fi
} >>"$LOG" 2>&1
EOF

echo "Reading existing /etc/rc.local ..."
debugfs -R "cat /etc/rc.local" "$IMG" 2>/dev/null > "$workdir/rc.local.new"
[ -s "$workdir/rc.local.new" ] || { echo "could not read /etc/rc.local" >&2; exit 1; }

if grep -q "custom user functions hook" "$workdir/rc.local.new"; then
    echo "Hook already present; leaving rc.local unchanged."
else
    echo "Appending hook before final 'exit 0' ..."
    awk '
        /^exit 0[[:space:]]*$/ && !done {
            print "# --- custom user functions hook (added) ---"
            print "{ [ -x /opt/custom/startup.sh ] && /opt/custom/startup.sh ; } &"
            print "# --- end custom hook ---"
            print ""
            done = 1
        }
        { print }
    ' "$workdir/rc.local.new" > "$workdir/rc.local.tmp"
    mv "$workdir/rc.local.tmp" "$workdir/rc.local.new"
fi

echo "Writing files into the image ..."
cat > "$workdir/dbg.cmds" <<EOF
mkdir /opt
mkdir /opt/custom
rm /opt/custom/startup.sh
write $workdir/startup.sh /opt/custom/startup.sh
sif /opt/custom/startup.sh mode 0100755
rm /opt/custom/bookserver.py
write $server_src /opt/custom/bookserver.py
sif /opt/custom/bookserver.py mode 0100755
sif /opt mode 040755
sif /opt/custom mode 040755
rm /etc/rc.local
write $workdir/rc.local.new /etc/rc.local
sif /etc/rc.local mode 0100755
EOF
debugfs -w -f "$workdir/dbg.cmds" "$IMG" >/dev/null 2>&1 || true

echo "Verifying ..."
debugfs -R "stat /opt/custom/startup.sh" "$IMG" 2>/dev/null | grep -q "Mode:  0755" \
    && echo "  /opt/custom/startup.sh OK"
debugfs -R "stat /opt/custom/bookserver.py" "$IMG" 2>/dev/null | grep -q "Mode:  0755" \
    && echo "  /opt/custom/bookserver.py OK"
debugfs -R "cat /etc/rc.local" "$IMG" 2>/dev/null | grep -q "custom user functions hook" \
    && echo "  /etc/rc.local hook OK"
e2fsck -fn "$IMG" >/dev/null 2>&1 && echo "  fsck clean"

echo "Done. Now:"
echo "  1) recompress:  zstd -19 $IMG -o rootfs.img   (no --long)"
echo "  2) update rootfs.img's sha256 in your package's sha2-256sums"
echo "  3) repack update.tar"
echo "The uploader now works after flashing alone. Optional overrides live in"
echo "<drive>/.custom/ (startup.sh to replace behaviour, DISABLE to turn it off)."
