#!/usr/bin/env python3
"""Export the ten Vocal More mockups using the local macOS Chrome binary."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parent
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
EXPECTED_SIZE = (2048, 1644)
RENDERS = (
    ("engine", "light", "01-engine-status.png"),
    ("general", "light", "02-general.png"),
    ("audio", "light", "03-audio.png"),
    ("appearance", "light", "04-appearance-capsule.png"),
    ("recognition", "light", "05-recognition.png"),
    ("polish", "light", "06-polish.png"),
    ("shortcuts", "light", "07-shortcuts.png"),
    ("dictionary", "light", "08-dictionary.png"),
    ("history", "light", "09-history.png"),
    ("audio", "dark", "10-dark-mode-audio.png"),
)


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def verify_png(path):
    with path.open("rb") as png:
        header = png.read(24)
    if len(header) != 24 or header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        raise ValueError(f"{path.name}: invalid PNG header")
    size = struct.unpack(">II", header[16:24])
    if size != EXPECTED_SIZE:
        raise ValueError(f"{path.name}: expected {EXPECTED_SIZE}, got {size}")


def capture(url, output, profile):
    result = subprocess.run(
        [
            str(CHROME),
            "--headless=new",
            "--hide-scrollbars",
            "--force-device-scale-factor=2",
            "--window-size=1024,822",
            "--virtual-time-budget=2000",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            f"--user-data-dir={profile}",
            f"--screenshot={output}",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if result.returncode or not output.is_file():
        detail = result.stderr.strip() or result.stdout.strip() or "Chrome produced no PNG"
        raise RuntimeError(f"{output.name}: {detail}")
    verify_png(output)


def main():
    if not CHROME.is_file():
        raise FileNotFoundError(f"Chrome not found at {CHROME}")

    output = ROOT / "renders"
    output.mkdir(exist_ok=True)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=ROOT))
    except OSError as error:
        raise RuntimeError(f"could not bind the preview server to 127.0.0.1:0 ({error})") from error

    with server, TemporaryDirectory(prefix=".render-", dir=ROOT) as staging:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            scratch = Path(staging)
            for screen, theme, filename in RENDERS:
                query = urlencode(dict(screen=screen, theme=theme, export=1))
                url = f"http://127.0.0.1:{server.server_port}/index.html?{query}"
                capture(url, scratch / filename, scratch / "profile")
            for _, _, filename in RENDERS:
                (scratch / filename).replace(output / filename)
                print(f"{filename} · {EXPECTED_SIZE[0]} × {EXPECTED_SIZE[1]}")
        finally:
            server.shutdown()
            worker.join()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(f"Mockup export failed: {error}")
