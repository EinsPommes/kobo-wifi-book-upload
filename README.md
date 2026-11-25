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
- That entrypoint is **self-contained**: it runs the baked server
  `/opt/custom/bookserver.py`, so the feature works right after flashing with no
  drive files. Optional overrides live on the USB drive:
  `/mnt/onboard/.custom/startup.sh` replaces the behaviour, and
  `/mnt/onboard/.custom/DISABLE` turns everything off (anti-lockout).
- `bookserver.py` saves uploads into `/mnt/onboard` (the Kobo library) and asks
  Nickel to import them via **NickelDBus**, waiting for the completion signal
  (`qndb -t 60000 -s pfmDoneProcessing -m pfmRescanBooksFull`), with a
  hardware-FIFO fallback. Logs go to tmpfs and are viewable at
  `http://<reader-ip>:8080/log`.

See [docs/how-it-works.md](docs/how-it-works.md) for details.

## Install

Patch the rootfs once; the feature is baked in, so flashing alone is enough.

### Patch the rootfs

You need your device's decompressed `rootfs.ext4` image. On Linux (or Windows
with WSL) with `e2fsprogs` and `zstd` installed:

```sh
scripts/inject.sh /path/to/rootfs.ext4
```

This bakes `/opt/custom/bookserver.py` and `/opt/custom/startup.sh` and appends
the hook to `/etc/rc.local` without mounting (uses `debugfs`, no root needed).
Then recompress the image and rebuild your update package's checksum file.
(Only the rootfs changes; kernel/U-Boot/TEE stay untouched, so their signatures
remain valid.) Flash the repacked `update.tar` — the uploader now runs on the
next boot with nothing to copy onto the drive.

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

### Optional: override on the drive

The feature works without any drive files. For a reflash-free dev loop you can
still drop an override on the USB drive — the loader prefers it over the baked
server:

```
<drive>/.custom/startup.sh     # replaces the behaviour; edit + reboot
<drive>/.custom/DISABLE         # (empty file) turns the hook off entirely
```

## Auto-update from GitHub

The firmware can pull its own future updates from this repo's releases — no
vendor server involved. The baked `/opt/custom/github-update.sh` runs at boot
(after WiFi), checks `releases/latest`, and if the tag is newer than the baked
`/opt/custom/VERSION` it downloads the release's `update.tar`, verifies it
against the `update.tar.sha256` asset, and stages it at
`/mnt/onboard/.kobo/update.tar`. It never flashes by itself — the reader shows
its normal "install update?" prompt, so you always confirm.

**Publishing a new firmware release** (so devices pick it up):

1. Bump `VERSION` (e.g. `v2.2.0`) and rebuild `update.tar` (see Install).
2. Create a GitHub release tagged with that exact version.
3. Upload **both** `update.tar` and `update.tar.sha256` as assets
   (`sha256sum update.tar | awk '{print $1"  update.tar"}' > update.tar.sha256`).

Downgrade-protected (only strictly newer versions), checksum-verified, and the
repo owner/name are set at the top of `src/github-update.sh`.

## Blocking vendor updates

A stock Kobo/Tolino firmware update would re-flash the rootfs and wipe this mod.
To prevent that, `inject.sh` adds these lines to `/etc/hosts` so the device
can't reach the firmware servers:

```
127.0.0.1 api.kobobooks.com
127.0.0.1 api.ereaderupgrade.com
127.0.0.1 api.ereaderupgrades.com
```

This does **not** affect the GitHub self-updater (different host). If a
Kobo-branded store feature misbehaves, remove the `api.kobobooks.com` line and
reflash. To re-enable vendor updates entirely, remove the block and reflash.

## Custom boot splash (optional, device-specific)

`scripts/make-splash.js` generates the power-on splash frames
(`<product>-on-*.raw.gz` in `/etc/images/`). It is written for the Tolino Vision
Color / Kobo Libra Colour (`monzaTolino`, **1264×1680**, 32bpp). For another
model, decode one of its existing `-on-*.raw.gz` frames to find the resolution
and orientation first, then adjust `W`/`H` and the target filenames.

```sh
npm i jimp@0.22
node scripts/make-splash.js    # writes monzaTolino-on-0/1.raw.gz
# then debugfs-write them into /etc/images/ in the rootfs and repack
```

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
