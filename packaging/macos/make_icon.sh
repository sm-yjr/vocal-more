#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE="$ROOT/assets/logo.png"
ICONSET="$ROOT/packaging/macos/VocalMore.iconset"
OUTPUT="$ROOT/packaging/macos/VocalMore.icns"
MASKED_SOURCE="$ROOT/packaging/macos/.VocalMore.masked.png"
ICON_SOURCE="$ROOT/packaging/macos/.VocalMore.icon-source.png"
RUNTIME_LOGO="$ROOT/packaging/macos/.VocalMore.runtime-logo.png"
PYTHON_BIN="${VOCAL_MORE_BUILD_PYTHON:-python3}"

if [[ ! -f "$SOURCE" ]]; then
  echo "Icon source not found: $SOURCE" >&2
  exit 1
fi

rm -rf "$ICONSET"
mkdir -p "$ICONSET"

"$PYTHON_BIN" - "$SOURCE" "$MASKED_SOURCE" <<'PY'
from __future__ import annotations

from collections import deque
from pathlib import Path
from struct import pack, unpack
import sys
import zlib


def read_png(path: str) -> tuple[int, int, int, list[bytearray]]:
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"Not a PNG file: {path}")

    pos = 8
    raw = b""
    width = height = color_type = None
    while pos < len(data):
        length = unpack(">I", data[pos : pos + 4])[0]
        chunk_type = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        pos += length + 12
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = unpack(
                ">IIBBBBB", chunk
            )
            if bit_depth != 8 or interlace != 0 or color_type not in {2, 6}:
                raise SystemExit("Icon source must be an 8-bit non-interlaced RGB/RGBA PNG")
        elif chunk_type == b"IDAT":
            raw += chunk

    if width is None or height is None or color_type is None:
        raise SystemExit("Invalid PNG: missing IHDR")

    bpp = 4 if color_type == 6 else 3
    scanlines = zlib.decompress(raw)
    rows: list[bytearray] = []
    previous = bytearray(width * bpp)
    index = 0
    for _ in range(height):
        filter_type = scanlines[index]
        index += 1
        encoded = scanlines[index : index + width * bpp]
        index += width * bpp
        row = bytearray(width * bpp)
        for i, value in enumerate(encoded):
            left = row[i - bpp] if i >= bpp else 0
            up = previous[i]
            upper_left = previous[i - bpp] if i >= bpp else 0
            if filter_type == 0:
                decoded = value
            elif filter_type == 1:
                decoded = value + left
            elif filter_type == 2:
                decoded = value + up
            elif filter_type == 3:
                decoded = value + ((left + up) // 2)
            elif filter_type == 4:
                estimate = left + up - upper_left
                distances = (
                    abs(estimate - left),
                    abs(estimate - up),
                    abs(estimate - upper_left),
                )
                predictor = (left, up, upper_left)[distances.index(min(distances))]
                decoded = value + predictor
            else:
                raise SystemExit(f"Unsupported PNG filter: {filter_type}")
            row[i] = decoded & 0xFF
        rows.append(row)
        previous = row
    return width, height, bpp, rows


def write_png(path: str, width: int, height: int, rows: list[bytearray]) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            pack(">I", len(payload))
            + kind
            + payload
            + pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    Path(path).write_bytes(data)


def is_edge_background(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    return max(pixel) >= 180 and max(pixel) - min(pixel) <= 90


source, output = sys.argv[1], sys.argv[2]
width, height, bpp, rows = read_png(source)
connected_background = bytearray(width * height)
queue: deque[tuple[int, int]] = deque()


def pixel_at(x: int, y: int) -> tuple[int, int, int]:
    base = x * bpp
    return tuple(rows[y][base : base + 3])  # type: ignore[return-value]


def add_if_background(x: int, y: int) -> None:
    offset = y * width + x
    if connected_background[offset] or not is_edge_background(pixel_at(x, y)):
        return
    connected_background[offset] = 1
    queue.append((x, y))


for x in range(width):
    add_if_background(x, 0)
    add_if_background(x, height - 1)
for y in range(height):
    add_if_background(0, y)
    add_if_background(width - 1, y)

while queue:
    x, y = queue.popleft()
    if x > 0:
        add_if_background(x - 1, y)
    if x < width - 1:
        add_if_background(x + 1, y)
    if y > 0:
        add_if_background(x, y - 1)
    if y < height - 1:
        add_if_background(x, y + 1)

def inside_rounded_rect(x: int, y: int) -> bool:
    margin = round(width * 0.08)
    radius = round(width * 0.18)
    left, top = margin, margin
    right, bottom = width - margin - 1, height - margin - 1
    if left + radius <= x <= right - radius and top <= y <= bottom:
        return True
    if left <= x <= right and top + radius <= y <= bottom - radius:
        return True

    corner_x = left + radius if x < left + radius else right - radius
    corner_y = top + radius if y < top + radius else bottom - radius
    return (x - corner_x) ** 2 + (y - corner_y) ** 2 <= radius**2


rgba_rows: list[bytearray] = []
for y, row in enumerate(rows):
    rgba = bytearray(width * 4)
    for x in range(width):
        source_base = x * bpp
        target_base = x * 4
        if inside_rounded_rect(x, y):
            rgba[target_base : target_base + 3] = b"\xff\xff\xff"
            rgba[target_base + 3] = 255
        else:
            rgba[target_base : target_base + 3] = b"\xff\xff\xff"
            rgba[target_base + 3] = 0

        if not connected_background[y * width + x]:
            rgba[target_base : target_base + 3] = row[source_base : source_base + 3]
            rgba[target_base + 3] = 255
    rgba_rows.append(rgba)

write_png(output, width, height, rgba_rows)
PY

sips -c 1300 1300 "$MASKED_SOURCE" --out "$ICON_SOURCE" >/dev/null
sips -z 256 256 "$ICON_SOURCE" --out "$RUNTIME_LOGO" >/dev/null

sips -z 16 16 "$ICON_SOURCE" --out "$ICONSET/icon_16x16.png" >/dev/null
sips -z 32 32 "$ICON_SOURCE" --out "$ICONSET/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "$ICON_SOURCE" --out "$ICONSET/icon_32x32.png" >/dev/null
sips -z 64 64 "$ICON_SOURCE" --out "$ICONSET/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "$ICON_SOURCE" --out "$ICONSET/icon_128x128.png" >/dev/null
sips -z 256 256 "$ICON_SOURCE" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "$ICON_SOURCE" --out "$ICONSET/icon_256x256.png" >/dev/null
sips -z 512 512 "$ICON_SOURCE" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "$ICON_SOURCE" --out "$ICONSET/icon_512x512.png" >/dev/null
sips -z 1024 1024 "$ICON_SOURCE" --out "$ICONSET/icon_512x512@2x.png" >/dev/null

if ! iconutil -c icns "$ICONSET" -o "$OUTPUT"; then
  if [[ -f "$OUTPUT" ]]; then
    echo "Warning: iconutil could not regenerate $OUTPUT; reusing existing icon." >&2
  else
    exit 1
  fi
fi
rm -rf "$ICONSET" "$MASKED_SOURCE" "$ICON_SOURCE"

echo "Wrote $OUTPUT"
