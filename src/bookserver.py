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
from typing import NamedTuple

# --- configuration ---------------------------------------------------------
HOST = "0.0.0.0"
PORT = 8080
# Kobo scans everything under /mnt/onboard; the root makes books appear directly.
LIBRARY_DIR = Path("/mnt/onboard")
NICKEL_FIFO = Path("/var/volatile/tmp/nickel-hardware-status")
# On tmpfs, not on /mnt/onboard: a file held open there blocks USB "Connect".
# Must match the redirect in startup.sh. Served (tail only) at GET /log.
LOG_PATH = Path("/tmp/bookserver.log")
# Reject an upload unless this much space would remain afterwards.
MIN_FREE_MB = 50
# GET /log returns at most this many trailing bytes (log lives in RAM/tmpfs).
LOG_TAIL_BYTES = 64 * 1024
# Compound extensions that must be kept whole when de-duplicating a name.
COMPOUND_SUFFIXES = (".kepub.epub",)
# Nickel's library database, read read-only for the reading-stats page.
KOBO_DB = Path("/mnt/onboard/.kobo/KoboReader.sqlite")
# The device's Python has no sqlite3 module, so the stats page shells out to the
# sqlite3 command-line tool instead. Field separator chosen to never appear in a
# book title (unit separator).
SQLITE_CLI = "sqlite3"
STATS_FIELD_SEP = "\x1f"
# Only top-level books carry ContentType 6; their chapters use 9.
BOOK_CONTENT_TYPE = 6
# Kobo's per-book ReadStatus codes.
READ_STATUS_LABELS = {0: "not started", 1: "reading", 2: "finished"}

# Characters FAT (the /mnt/onboard filesystem) cannot store in a filename.
_FAT_ILLEGAL = set('<>:"/\\|?*') | {chr(code) for code in range(0x20)}


def log(message: str) -> None:
    """Write a diagnostic line to stderr (captured in the log file)."""
    print(message, file=sys.stderr, flush=True)


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


def split_name(filename: str) -> tuple[str, str]:
    """Split into (stem, suffix), keeping known compound extensions whole."""
    lowered = filename.lower()
    for suffix in COMPOUND_SUFFIXES:
        if lowered.endswith(suffix) and len(filename) > len(suffix):
            return filename[: -len(suffix)], filename[-len(suffix):]
    path = Path(filename)
    return path.stem, path.suffix


def unique_path(directory: Path, filename: str) -> Path:
    """Return a path in *directory* that does not overwrite an existing file."""
    candidate = directory / filename
    if not candidate.exists():
        return candidate

    stem, suffix = split_name(filename)
    counter = 1
    while True:
        candidate = directory / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def save_upload(part: cgi.FieldStorage) -> str:
    """Persist one uploaded form part. Returns the stored filename.

    Writes to a temporary file and renames it into place, so a failed or
    interrupted upload never leaves a half-written book in the library.
    Raises ValueError for a part without a usable filename, OSError on I/O.
    """
    filename = sanitize_filename(part.filename or "")
    if not filename:
        raise ValueError("upload without a valid filename")

    destination = unique_path(LIBRARY_DIR, filename)
    partial = destination.with_name(f".{destination.name}.part")
    try:
        with partial.open("wb") as target:
            shutil.copyfileobj(part.file, target)
        partial.replace(destination)
    except OSError:
        partial.unlink(missing_ok=True)
        raise
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
    """Free space on the library filesystem in MiB, or -1 if unknown."""
    try:
        return shutil.disk_usage(LIBRARY_DIR).free // (1024 * 1024)
    except OSError:
        return -1


def tail_bytes(path: Path, limit: int) -> bytes:
    """Return at most the last *limit* bytes of a file (placeholder if absent)."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            start = max(0, handle.tell() - limit)
            handle.seek(start)
            return handle.read()
    except OSError:
        return b"(no log yet)"


def format_summary(saved: list[str], failed: list[str], rescan_status: str) -> str:
    """Build the user-facing result message for an upload attempt."""
    if not saved and not failed:
        return "No file selected."

    lines: list[str] = []
    if saved:
        lines.append(f"Uploaded {len(saved)} file(s):")
        lines += [f"- {name}" for name in saved]
        lines += ["", rescan_status]
    if failed:
        if saved:
            lines.append("")
        lines.append(f"Failed {len(failed)} file(s):")
        lines += [f"- {item}" for item in failed]
    return "\n".join(lines)


# --- reading statistics ----------------------------------------------------
class BookProgress(NamedTuple):
    """One book's reading progress, taken from Nickel's library database."""
    title: str
    author: str
    percent: int
    status: str
    seconds_read: int
    last_read: str


class LibraryStats(NamedTuple):
    """Aggregate reading figures plus the per-book breakdown."""
    total: int
    reading: int
    finished: int
    seconds_read: int
    books: list[BookProgress]


def read_library_stats() -> LibraryStats | None:
    """Read reading progress from Nickel's database via the sqlite3 CLI.

    Returns None when the database is absent or unreadable, so the caller can
    show a friendly message instead of failing. Opened read-only so a running
    Nickel is never disturbed.
    """
    if not KOBO_DB.exists():
        return None
    query = (
        "SELECT Title, Attribution, ___PercentRead, ReadStatus, "
        f"TimeSpentReading, DateLastRead FROM content "
        f"WHERE ContentType = {BOOK_CONTENT_TYPE} "
        "ORDER BY (DateLastRead IS NULL), DateLastRead DESC"
    )
    try:
        result = subprocess.run(
            [SQLITE_CLI, "-readonly", "-separator", STATS_FIELD_SEP, str(KOBO_DB), query],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        log(f"could not run sqlite3: {error}")
        return None
    if result.returncode != 0:
        log(f"sqlite3 failed ({result.returncode}): {result.stderr.strip()}")
        return None
    rows = [line for line in result.stdout.split("\n") if line]
    return _summarize([_parse_row(line) for line in rows])


def _parse_row(line: str) -> BookProgress:
    fields = line.split(STATS_FIELD_SEP)
    fields += [""] * (6 - len(fields))
    title, author, percent, status, seconds, last_read = fields[:6]
    return BookProgress(
        title=title or "(untitled)",
        author=author,
        percent=_clamp_percent(percent),
        status=READ_STATUS_LABELS.get(_to_int(status), "unknown"),
        seconds_read=max(0, _to_int(seconds)),
        last_read=last_read[:10],
    )


def _to_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _summarize(books: list[BookProgress]) -> LibraryStats:
    reading = sum(1 for book in books if book.status == "reading")
    finished = sum(1 for book in books if book.status == "finished")
    seconds = sum(book.seconds_read for book in books)
    return LibraryStats(len(books), reading, finished, seconds, books)


def _clamp_percent(value) -> int:
    try:
        return max(0, min(100, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def format_duration(seconds: int) -> str:
    """Render a reading duration, e.g. '2 h 5 min', or a dash when unknown."""
    if seconds <= 0:
        return "—"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


# --- HTTP layer ------------------------------------------------------------
PAGE_CSS = """<style>
  body { font-family: system-ui, sans-serif; margin: 0; background: #f4f4f4; color: #222; }
  .card { max-width: 520px; margin: 24px auto; padding: 24px;
          background: #fff; border-radius: 12px; box-shadow: 0 1px 6px rgba(0,0,0,.12); }
  h1 { font-size: 1.3rem; margin: 0 0 4px; }
  .sub { color: #666; margin: 0 0 20px; font-size: .9rem; }
  .sub a, a.nav { color: #2b6cb0; text-decoration: none; }
  input[type=file] { width: 100%; padding: 14px; border: 2px dashed #bbb;
          border-radius: 10px; background: #fafafa; }
  button { width: 100%; margin-top: 16px; padding: 14px; font-size: 1rem;
          border: 0; border-radius: 10px; background: #2b6cb0; color: #fff; }
  .msg { padding: 12px; background: #e6fffa; border: 1px solid #38b2ac;
          border-radius: 8px; white-space: pre-line; }
  .stat-grid { display: flex; gap: 10px; margin: 0 0 16px; }
  .stat { flex: 1; text-align: center; padding: 12px 6px; background: #f7fafc;
          border-radius: 10px; }
  .stat b { display: block; font-size: 1.3rem; }
  .stat span { color: #666; font-size: .8rem; }
  .book { padding: 12px 0; border-top: 1px solid #eee; }
  .book-title { font-weight: 600; }
  .book-author { color: #666; font-size: .85rem; margin-bottom: 6px; }
  .bar { height: 8px; background: #edf2f7; border-radius: 4px; overflow: hidden; }
  .fill { height: 100%; background: #2b6cb0; }
  .book-meta { color: #666; font-size: .8rem; margin-top: 4px; }
</style>"""


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
{PAGE_CSS}
</head>
<body>
  <div class="card">
    <h1>Send books to my reader</h1>
    <p class="sub">{free_text} &middot; <a href="/stats">Reading stats &rarr;</a></p>
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


def render_stats_page() -> bytes:
    stats = read_library_stats()
    inner = _stats_html(stats) if stats else '<p class="msg">No reading data yet.</p>'
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reading stats</title>
{PAGE_CSS}
</head>
<body>
  <div class="card">
    <h1>Reading stats</h1>
    <p class="sub"><a class="nav" href="/">&larr; Upload books</a></p>
    {inner}
  </div>
</body>
</html>"""
    return page.encode("utf-8")


def _stats_html(stats: LibraryStats) -> str:
    cards = [
        f'<div class="stat"><b>{stats.total}</b><span>books</span></div>',
        f'<div class="stat"><b>{stats.reading}</b><span>reading</span></div>',
        f'<div class="stat"><b>{stats.finished}</b><span>finished</span></div>',
    ]
    if stats.seconds_read > 0:
        cards.append(f'<div class="stat"><b>{format_duration(stats.seconds_read)}</b>'
                     f'<span>total</span></div>')
    note = "" if stats.seconds_read > 0 else (
        '<p class="sub">Reading time appears once the reader has tracked it.</p>')
    books = "\n".join(_book_row(book) for book in stats.books)
    return f'<div class="stat-grid">{"".join(cards)}</div>{note}{books}'


def _book_row(book: BookProgress) -> str:
    details = [f"{book.percent}%", book.status]
    if book.seconds_read > 0:
        details.append(format_duration(book.seconds_read))
    if book.last_read:
        details.append(book.last_read)
    author = f'<div class="book-author">{html.escape(book.author)}</div>' if book.author else ""
    return f"""<div class="book">
  <div class="book-title">{html.escape(book.title)}</div>
  {author}
  <div class="bar"><div class="fill" style="width:{book.percent}%"></div></div>
  <div class="book-meta">{" &middot; ".join(html.escape(d) for d in details)}</div>
</div>"""


class UploadHandler(BaseHTTPRequestHandler):
    server_version = "BookUploader/1.0"

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, body: bytes, status: int = 200) -> None:
        self._send(body, "text/html; charset=utf-8", status)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send_html(render_page())
        elif self.path == "/stats":
            self._send_html(render_stats_page())
        elif self.path == "/log":
            self._send(tail_bytes(LOG_PATH, LOG_TAIL_BYTES), "text/plain; charset=utf-8")
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

    def _rejection_for_space(self) -> str:
        """If the declared body would not fit, return a message, else ''."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        free = free_space_mb()
        if free < 0 or length == 0:
            return ""
        needed = length // (1024 * 1024) + MIN_FREE_MB
        if free < needed:
            return f"Not enough space: {free} MB free, need about {needed} MB."
        return ""

    def _handle_upload(self) -> str:
        rejection = self._rejection_for_space()
        if rejection:
            return rejection

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
        failed: list[str] = []
        for part in parts:
            if not getattr(part, "filename", None):
                continue
            try:
                saved.append(save_upload(part))
            except (OSError, ValueError) as error:
                log(f"save failed for {part.filename!r}: {error}")
                failed.append(f"{part.filename}: {error}")

        rescan_status = trigger_library_rescan() if saved else ""
        return format_summary(saved, failed, rescan_status)

    def log_message(self, fmt: str = "", *args) -> None:  # route access logs to our logger
        log("http: " + (fmt % args if args else fmt))


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
