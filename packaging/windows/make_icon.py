"""Generate a deterministic Windows ICO without image-library dependencies."""

from __future__ import annotations

import argparse
from pathlib import Path
from struct import pack
import zlib


OUTPUT_SIZE = 256
SCALE = 4
CANVAS = OUTPUT_SIZE * SCALE


def _inside_rounded_rect(x: int, y: int, left: int, top: int, right: int, bottom: int, radius: int) -> bool:
    if left + radius <= x <= right - radius or top + radius <= y <= bottom - radius:
        return left <= x <= right and top <= y <= bottom
    corner_x = left + radius if x < left + radius else right - radius
    corner_y = top + radius if y < top + radius else bottom - radius
    return (x - corner_x) ** 2 + (y - corner_y) ** 2 <= radius**2


def _inside_capsule(x: int, y: int, center_x: int, center_y: int, width: int, height: int) -> bool:
    radius = height // 2
    left = center_x - width // 2
    right = center_x + width // 2
    top = center_y - radius
    bottom = center_y + radius
    return _inside_rounded_rect(x, y, left, top, right, bottom, radius)


def _render_high_resolution() -> list[bytearray]:
    rows = [bytearray(CANVAS * 4) for _ in range(CANVAS)]
    margin = 22 * SCALE
    radius = 54 * SCALE
    left = top = margin
    right = bottom = CANVAS - margin - 1

    for y in range(CANVAS):
        for x in range(CANVAS):
            base = x * 4
            if not _inside_rounded_rect(x, y, left, top, right, bottom, radius):
                rows[y][base : base + 4] = b"\x00\x00\x00\x00"
                continue
            vertical = (y - top) / max(1, bottom - top)
            horizontal = (x - left) / max(1, right - left)
            glow = max(0.0, 1.0 - ((horizontal - 0.22) ** 2 + (vertical - 0.18) ** 2) * 2.6)
            red = int(24 + 13 * glow + 4 * vertical)
            green = int(29 + 34 * glow + 12 * vertical)
            blue = int(39 + 58 * glow + 22 * vertical)
            rows[y][base : base + 4] = bytes((red, green, blue, 255))

    # A compact white speech waveform, with a blue center pulse.
    center_x = CANVAS // 2
    center_y = CANVAS // 2
    heights = [42, 72, 108, 150, 108, 72, 42]
    widths = [18, 20, 22, 24, 22, 20, 18]
    spacing = 31 * SCALE
    for index, (height_px, width_px) in enumerate(zip(heights, widths)):
        bar_x = center_x + (index - 3) * spacing
        height = height_px * SCALE
        width = width_px * SCALE
        for y in range(center_y - height // 2, center_y + height // 2 + 1):
            if not 0 <= y < CANVAS:
                continue
            for x in range(bar_x - width // 2, bar_x + width // 2 + 1):
                if not 0 <= x < CANVAS or not _inside_capsule(x, y, bar_x, center_y, width, height):
                    continue
                base = x * 4
                if index == 3:
                    color = (111, 190, 255, 255)
                else:
                    color = (245, 248, 255, 255)
                rows[y][base : base + 4] = bytes(color)

    return rows


def _downsample(rows: list[bytearray]) -> list[bytearray]:
    result: list[bytearray] = []
    samples = SCALE * SCALE
    for target_y in range(OUTPUT_SIZE):
        row = bytearray(OUTPUT_SIZE * 4)
        for target_x in range(OUTPUT_SIZE):
            sums = [0, 0, 0, 0]
            for dy in range(SCALE):
                source = rows[target_y * SCALE + dy]
                for dx in range(SCALE):
                    base = (target_x * SCALE + dx) * 4
                    for channel in range(4):
                        sums[channel] += source[base + channel]
            base = target_x * 4
            row[base : base + 4] = bytes(round(value / samples) for value in sums)
        result.append(row)
    return result


def _png_bytes(rows: list[bytearray]) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            pack(">I", len(payload))
            + kind
            + payload
            + pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", pack(">IIBBBBB", OUTPUT_SIZE, OUTPUT_SIZE, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def build_icon(output: Path, *, png_output: Path | None = None) -> None:
    rows = _downsample(_render_high_resolution())
    png = _png_bytes(rows)
    # ICO header + one 256x256 PNG-compressed image directory entry.
    icon = (
        pack("<HHH", 0, 1, 1)
        + pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
        + png
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(icon)
    if png_output is not None:
        png_output.parent.mkdir(parents=True, exist_ok=True)
        png_output.write_bytes(png)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--png", type=Path)
    args = parser.parse_args()
    build_icon(args.output, png_output=args.png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
