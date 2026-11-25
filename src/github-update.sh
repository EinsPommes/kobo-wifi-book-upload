#!/bin/sh
# Self-updater: pull future firmware from the maintainer's GitHub releases and
# STAGE it for the device's own update prompt. It never flashes by itself: it
# downloads the release's update.tar, verifies its SHA-256, and drops it at
# /mnt/onboard/.kobo/update.tar. Nickel then shows the normal "install update?"
# prompt, so you always confirm before anything is flashed.
#
# POSIX sh. Logs to tmpfs. Safe to run at every boot (in the background).

OWNER=EinsPommes
REPO=kobo-wifi-book-upload
ASSET=update.tar
SUM_ASSET=update.tar.sha256
VERSION_FILE=/opt/custom/VERSION             
STAGE=/mnt/onboard/.kobo/update.tar
TMP=/mnt/onboard/.kobo/.update.download
LOG=/tmp/github-update.log
API="https://api.github.com/repos/$OWNER/$REPO/releases/latest"

log() { echo "$(date): $*" >>"$LOG"; }

# Return 0 if version $1 is strictly newer than $2 (vMAJOR.MINOR.PATCH).
version_gt() {
    a=${1#v}; b=${2#v}
    IFS=.
    # shellcheck disable=SC2086
    set -- $a; a1=${1:-0}; a2=${2:-0}; a3=${3:-0}
    # shellcheck disable=SC2086
    set -- $b; b1=${1:-0}; b2=${2:-0}; b3=${3:-0}
    unset IFS
    case "$a1$a2$a3$b1$b2$b3" in *[!0-9]*) return 1 ;; esac
    [ "$a1" -ne "$b1" ] && { [ "$a1" -gt "$b1" ]; return; }
    [ "$a2" -ne "$b2" ] && { [ "$a2" -gt "$b2" ]; return; }
    [ "$a3" -gt "$b3" ]
}

json_asset_url() {  # $1 = asset filename; prints its browser_download_url
    grep -o '"browser_download_url"[ ]*:[ ]*"[^"]*/'"$1"'"' \
        | head -1 | sed 's/.*"\(http[^"]*\)"$/\1/'
}

installed=$(cat "$VERSION_FILE" 2>/dev/null || echo unknown)

# Wait for WiFi to come up (up to ~90s), then give up quietly.
i=0
while [ "$i" -lt 30 ]; do
    if ip -4 addr show 2>/dev/null | awk '/inet /{print $2}' | grep -qv '^127'; then
        break
    fi
    i=$((i + 1)); sleep 3
done

meta=$(curl -fsSL --max-time 30 -H "Accept: application/vnd.github+json" "$API" 2>>"$LOG") \
    || { log "release metadata fetch failed"; exit 0; }

latest=$(printf '%s' "$meta" | grep -o '"tag_name"[ ]*:[ ]*"[^"]*"' \
    | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
[ -n "$latest" ] || { log "no tag_name in response"; exit 0; }
log "installed=$installed latest=$latest"

if [ "$installed" != unknown ] && ! version_gt "$latest" "$installed"; then
    log "already up to date"
    exit 0
fi

asset_url=$(printf '%s' "$meta" | json_asset_url "$ASSET")
sum_url=$(printf '%s' "$meta" | json_asset_url "$SUM_ASSET")
[ -n "$asset_url" ] || { log "release has no $ASSET asset"; exit 0; }
[ -n "$sum_url" ]   || { log "release has no $SUM_ASSET asset - refusing without a checksum"; exit 0; }

want=$(curl -fsSL --max-time 30 "$sum_url" 2>>"$LOG" | awk '{print $1}')
case "$want" in
    [0-9a-fA-F]*) : ;;
    *) log "could not read checksum"; exit 0 ;;
esac

# Already staged and valid? Just remind and stop.
if [ -f "$STAGE" ] && [ "$(sha256sum "$STAGE" | awk '{print $1}')" = "$want" ]; then
    log "update $latest already staged"
    fbink -q -y 1 "Update $latest ready - restart to install" 2>/dev/null
    exit 0
fi

log "downloading $latest"
if ! curl -fSL --max-time 1800 -o "$TMP" "$asset_url" 2>>"$LOG"; then
    log "download failed"; rm -f "$TMP"; exit 0
fi

got=$(sha256sum "$TMP" | awk '{print $1}')
if [ "$got" != "$want" ]; then
    log "CHECKSUM MISMATCH (want=$want got=$got) - discarding download"
    rm -f "$TMP"; exit 0
fi

mv "$TMP" "$STAGE"
log "staged $latest at $STAGE"
fbink -q -y 1 "Update $latest ready - restart to install" 2>/dev/null
exit 0
