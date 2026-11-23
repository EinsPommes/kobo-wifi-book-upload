#!/bin/sh
# Inject the custom boot hook into a decompressed ext4 rootfs image.
#
# Usage:  inject.sh /path/to/rootfs.ext4
#
# Adds /opt/custom/startup.sh and appends a one-line hook to the existing
# /etc/rc.local (before its final `exit 0`). Uses debugfs, so it needs no
# mount and no root. Requires e2fsprogs (debugfs, e2fsck) in PATH.
#
# Only the rootfs is touched; kernel/U-Boot/TEE are untouched, so their
# signatures stay valid. After running this, recompress the image and rebuild
# your update package's checksum file.

set -e

IMG="$1"
[ -n "$IMG" ] && [ -f "$IMG" ] || { echo "usage: $0 /path/to/rootfs.ext4" >&2; exit 1; }
command -v debugfs >/dev/null || { echo "debugfs not found (install e2fsprogs)" >&2; exit 1; }

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

# Baked stable entrypoint.
cat > "$workdir/startup.sh" <<'EOF'
#!/bin/sh
# Stable custom entrypoint (baked into rootfs).
# Edit /mnt/onboard/.custom/startup.sh on the USB drive to change behaviour.
CUSTOM_DIR=/mnt/onboard/.custom
LOG="$CUSTOM_DIR/custom.log"
mkdir -p "$CUSTOM_DIR" 2>/dev/null
{
    echo "=== custom startup: $(date) ==="
    if [ -f "$CUSTOM_DIR/startup.sh" ]; then
        echo "running user override: $CUSTOM_DIR/startup.sh"
        /bin/sh "$CUSTOM_DIR/startup.sh"
        echo "user override exited: $?"
    else
        echo "no user override yet. drop your code at $CUSTOM_DIR/startup.sh"
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
write $workdir/startup.sh /opt/custom/startup.sh
sif /opt/custom/startup.sh mode 0100755
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
debugfs -R "cat /etc/rc.local" "$IMG" 2>/dev/null | grep -q "custom user functions hook" \
    && echo "  /etc/rc.local hook OK"
e2fsck -fn "$IMG" >/dev/null 2>&1 && echo "  fsck clean"

echo "Done. Now:"
echo "  1) copy src/bookserver.py + src/startup.sh to <drive>/.custom/ on the reader"
echo "  2) recompress:  zstd -19 $IMG -o rootfs.img   (no --long)"
echo "  3) update sha256 of rootfs.img in your package's sha2-256sums"
