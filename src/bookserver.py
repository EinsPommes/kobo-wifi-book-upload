#!/usr/bin/env python3
"""Tiny WiFi book uploader for the e-reader.

Serves a mobile-friendly upload page so books can be pushed onto the device
from a phone browser on the same WiFi. Uploaded files are stored in the Kobo
library root and the Nickel library is asked to re-scan so they show up.

Standard library only (Python 3.10 on the device); nothing to install.
"""

from __future__ import annotations

import cgi
import html
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# --- configuration ---------------------------------------------------------
HOST = "0.0.0.0"
PORT = 8080
# Kobo scans everything under /mnt/onboard; the root makes books appear directly.
LIBRARY_DIR = Path("/mnt/onboard")
NICKEL_FIFO = Path("/var/volatile/tmp/nickel-hardware-status")


def log(message: str) -> None:
    """Write a diagnostic line to stderr (captured in bookserver.log)."""
    print(message, file=sys.stderr, flush=True)


# Characters FAT (the /mnt/onboard filesystem) cannot store in a filename.
_FAT_ILLEGAL = set('<>:"/\\|?*') | {chr(code) for code in range(0x20)}


def sanitize_filename(raw_name: str) -> str:
    """Reduce an uploaded name to a safe, path-free, FAT-valid filename.

    Returns an empty string when nothing usable remains, so the caller can
    reject the part instead of writing to an unexpected location.
    """
    base = Path(raw_name.replace("\\", "/")).name
    cleaned = "".join("_" if char in _FAT_ILLEGAL else char for char in base).strip()
    if cleaned in ("", ".", ".."):
        return ""
    return cleaned


def unique_path(directory: Path, filename: str) -> Path:
    """Return a path in *directory* that does not overwrite an existing file."""
    candidate = directory / filename
    if not candidate.exists():
        return candidate

    stem, suffix = candidate.stem, candidate.suffix
    counter = 1
    while True:
        candidate = directory / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def save_upload(part: cgi.FieldStorage) -> str:
    """Persist one uploaded form part. Returns the stored filename.

    Raises ValueError for a part without a usable filename.
    """
    filename = sanitize_filename(part.filename or "")
    if not filename:
        raise ValueError("upload without a valid filename")

    destination = unique_path(LIBRARY_DIR, filename)
    with destination.open("wb") as target:
        shutil.copyfileobj(part.file, target)
    log(f"saved {destination}")
    return destination.name


def trigger_library_rescan() -> str:
    """Ask Nickel to import new books. Returns a short human-readable status.

    Prefers NickelDBus (clean, no USB screen); falls back to the hardware
    FIFO plug cycle. Never raises: a failed rescan must not drop the upload.
    """
    # Trigger the rescan AND wait for Nickel's completion signal; firing the
    # method without waiting often does not actually import (the call returns
    # before Nickel processes it).
    try:
        result = subprocess.run(
            ["qndb", "-t", "60000", "-s", "pfmDoneProcessing", "-m", "pfmRescanBooksFull"],
            capture_output=True, timeout=75,
        )
        if result.returncode == 0:
            return "library rescan done"
        log(f"qndb failed ({result.returncode}): {result.stderr.decode(errors='replace')}")
    except (FileNotFoundError, subprocess.SubprocessError) as error:
        log(f"qndb unavailable: {error}")

    if _rescan_via_fifo():
        return "library rescan triggered (fifo)"
    return "upload stored, but automatic rescan failed - reconnect USB to import"


def _rescan_via_fifo() -> bool:
    """Simulate a USB plug cycle so Nickel imports. Best effort."""
    try:
        with NICKEL_FIFO.open("w") as fifo:
            fifo.write("usb plug add\n")
            fifo.write("usb plug remove\n")
        return True
    except OSError as error:
        log(f"fifo rescan failed: {error}")
        return False


def free_space_mb() -> int:
    try:
        return shutil.disk_usage(LIBRARY_DIR).free // (1024 * 1024)
    except OSError:
        return -1


# --- HTTP layer ------------------------------------------------------------
def render_page(message: str = "") -> bytes:
    free = free_space_mb()
    free_text = f"{free} MB free" if free >= 0 else "free space unknown"
    banner = f'<p class="msg">{html.escape(message)}</p>' if message else ""
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Send books to my reader</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 0; background: #f4f4f4; color: #222; }}
  .card {{ max-width: 520px; margin: 24px auto; padding: 24px;
          background: #fff; border-radius: 12px; box-shadow: 0 1px 6px rgba(0,0,0,.12); }}
  h1 {{ font-size: 1.3rem; margin: 0 0 4px; }}
  .sub {{ color: #666; margin: 0 0 20px; font-size: .9rem; }}
  input[type=file] {{ width: 100%; padding: 14px; border: 2px dashed #bbb;
          border-radius: 10px; background: #fafafa; }}
  button {{ width: 100%; margin-top: 16px; padding: 14px; font-size: 1rem;
          border: 0; border-radius: 10px; background: #2b6cb0; color: #fff; }}
  .msg {{ padding: 12px; background: #e6fffa; border: 1px solid #38b2ac;
          border-radius: 8px; white-space: pre-line; }}
</style>
</head>
<body>
  <div class="card">
    <h1>Send books to my reader</h1>
    <p class="sub">{free_text}</p>
    {banner}
    <form method="post" action="/upload" enctype="multipart/form-data">
      <input type="file" name="files" multiple
             accept=".epub,.kepub,.kepub.epub,.pdf,.mobi,.cbz,.cbr,.txt,.fb2">
      <button type="submit">Upload</button>
    </form>
  </div>
</body>
</html>"""
    return page.encode("utf-8")


class UploadHandler(BaseHTTPRequestHandler):
    server_version = "BookUploader/1.0"

    def _send_html(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send_html(render_page())
        else:
            self._send_html(b"Not found", status=404)

    def do_POST(self) -> None:
        if self.path != "/upload":
            self._send_html(b"Not found", status=404)
            return
        try:
            self._send_html(render_page(self._handle_upload()))
        except (OSError, ValueError, TypeError, KeyError) as error:
            log(f"upload request failed: {error}")
            self._send_html(render_page(f"Upload failed: {error}"), status=500)

    def _handle_upload(self) -> str:
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST",
                     "CONTENT_TYPE": self.headers.get("Content-Type", "")},
        )
        parts = form["files"] if "files" in form else []
        if not isinstance(parts, list):
            parts = [parts]

        saved: list[str] = []
        for part in parts:
            if getattr(part, "filename", None):
                saved.append(save_upload(part))

        if not saved:
            return "No file selected."

        status = trigger_library_rescan()
        names = "\n".join(f"- {name}" for name in saved)
        return f"Uploaded {len(saved)} file(s):\n{names}\n\n{status}"

    def log_message(self, *args) -> None:  # route access logs through our logger
        log("http: " + (args[0] % args[1:] if args else ""))


def run() -> None:
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), UploadHandler)
    log(f"book uploader listening on {HOST}:{PORT}, saving to {LIBRARY_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
