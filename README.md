# Kobo/Tolino WiFi Book Upload

Push e-books onto a Kobo/Tolino e-reader over WiFi from your phone's browser —
no USB cable. A tiny web server runs on the device; you open
`http://<reader-ip>:8080`, pick an EPUB/PDF, upload, and it appears in your
library after an automatic Nickel rescan.

> Works on NTX/MediaTek + legacy i.MX6 Kobo-based readers (Yocto/Poky, SysV
> init, Nickel). Tested on a Tolino device.

## ⚠️ Disclaimer

Modifying firmware can brick your device. Everything here is provided **as is,
without warranty**, and you use it **at your own risk**. This repository
contains **only my own code and instructions** — **no vendor firmware** is
included or distributed. You apply these changes to firmware you already own.

## What's in here

```
src/bookserver.py    the upload web server (Python 3.10, standard library only)
src/startup.sh       boot launcher (starts the server, shows the URL on e-ink)
scripts/inject.sh    reproducible rootfs patch (WSL/Linux: debugfs + zstd)
docs/how-it-works.md boot flow, hook point, Nickel rescan details
```

## How it works

- The reader boots with SysV init; `/etc/rc.local` is the app launcher that
  starts Nickel. A single line is appended to it (after Nickel, backgrounded,
  non-fatal) that runs a stable entrypoint `/opt/custom/startup.sh`.
- That entrypoint runs `/mnt/onboard/.custom/startup.sh` from the USB drive, so
  you can change the feature later by editing files on the drive — **no
  reflashing** after the first install.
- `bookserver.py` saves uploads into `/mnt/onboard` (the Kobo library) and asks
  Nickel to import them via **NickelDBus** (`qndb -m pfmRescanBooksFull`), with
  a hardware-FIFO fallback.

See [docs/how-it-works.md](docs/how-it-works.md) for details.

## Install

There are two parts: a one-time rootfs patch (adds the boot hook) and the
drive files (the actual feature, editable anytime).

### 1. Patch the rootfs (one time)

You need your device's decompressed `rootfs.ext4` image. On Linux (or Windows
with WSL) with `e2fsprogs` and `zstd` installed:

```sh
scripts/inject.sh /path/to/rootfs.ext4
```

This adds `/opt/custom/startup.sh` and appends the hook to `/etc/rc.local`
without mounting (uses `debugfs`, no root needed). Then recompress the image
and rebuild your update package's checksum file. (Only the rootfs changes;
kernel/U-Boot/TEE stay untouched, so their signatures remain valid.)

#### Build it in the cloud (optional)

A GitHub Actions workflow (`.github/workflows/build.yml`) can do the rootfs
patch and repack for you. Trigger it manually (Actions → *Build patched
update.tar* → *Run workflow*) and give it a direct download URL to your **own
stock `update.tar`**. The runner patches it and uploads the patched
`update.tar` as a build artifact. Vendor firmware is never stored in the repo —
it only lives on the ephemeral runner.

> **Caveat:** on a *public* repository, build artifacts are downloadable by
> anyone, and the patched `update.tar` contains vendor firmware. For non-public
> output, run the workflow in a **private** repository. Retention is set to 1 day.

### 2. Put the feature on the drive

Connect the reader by USB and copy the server files so they live at:

```
<drive>/.custom/bookserver.py
<drive>/.custom/startup.sh
```

Eject and reboot.

## Use

1. Reader and phone on the same WiFi.
2. Find the reader's IP (shown briefly on screen at boot, or
   Settings → Device information).
3. Open `http://<reader-ip>:8080` on the phone, upload, done.

Logs: open `http://<reader-ip>:8080/log` in the browser. (They are kept in
`/tmp` on the device, not on `/mnt/onboard` — a file held open on the drive
would block USB "Connect to PC".)

## Security note

No authentication: anyone on your WiFi who knows the IP can upload. Fine for a
trusted home network; do not expose it to untrusted networks. Plain HTTP on
port 8080.

## Requirements

- Device Python **3.10** (uses the stdlib `cgi` module, removed in Python 3.13).
- `qndb` (NickelDBus) present for clean rescans; otherwise the FIFO fallback is
  used.

## License

MIT — see [LICENSE](LICENSE). Applies to the code in this repository only.
