# How it works

## Platform

Kobo-based e-reader firmware (Yocto/Poky, kernel 4.9, SysV init, Nickel reader
app). NTX/MediaTek `aud8113tp1` platform with legacy i.MX6 support. The root
filesystem is ext4 and is mounted **read-only** at runtime; the writable,
USB-exposed storage is a FAT partition mounted at `/mnt/onboard` (the drive
that shows up as "eReader" on a PC).

## Boot flow and hook point

1. `init` runs `/etc/init.d/rcS` (runlevel S). Among these, `S11mount-userdata`
   mounts `/mnt/onboard`.
2. The default runlevel is 5; `/etc/rc5.d/S99rc.local` runs
   `/etc/init.d/rc.local`, which executes `/etc/rc.local` if it is executable.
3. On these devices `/etc/rc.local` **is the app launcher**: it starts dbus,
   pulseaudio, speech-dispatcher and finally `nickel`.

The mod appends one line to `/etc/rc.local`, right before its final `exit 0`
and after Nickel launches, backgrounded and non-fatal so it can never prevent
Nickel from starting:

```sh
# --- custom user functions hook (added) ---
{ [ -x /opt/custom/startup.sh ] && /opt/custom/startup.sh ; } &
# --- end custom hook ---
```

`/opt/custom/startup.sh` (baked into the rootfs) is a stable entrypoint that
runs the editable `/mnt/onboard/.custom/startup.sh` from the USB drive. This
gives a reflash-free dev loop: change the drive file, reboot.

## Library rescan

Uploaded files land in `/mnt/onboard`, which Nickel scans. To import without a
USB cable cycle, the server calls **NickelDBus**:

```sh
qndb -m pfmRescanBooksFull
```

If `qndb` is missing or fails, it falls back to simulating a USB plug cycle
through the Nickel hardware FIFO:

```
/var/volatile/tmp/nickel-hardware-status  <<  "usb plug add" / "usb plug remove"
```

## Why signatures stay valid

Only the ext4 rootfs is modified. The kernel, U-Boot and OP-TEE are FIT images
signed with RSA-2048; they are left untouched, so verified boot still passes.
The update package's `sha2-256sums` is a zstd-compressed plaintext checksum
list (not a cryptographic signature), so it is simply regenerated for the
changed rootfs.
