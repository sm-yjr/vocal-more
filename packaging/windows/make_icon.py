"""Generate the Windows application icon without external image dependencies."""

from __future__ import annotations

import math
from pathlib import Path
import struct
import zlib


SIZES = (16, 24, 32, 48, 64, 128, 256)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _inside_rounded_square(x: float, y: float, size: int, radius: float) -> bool:
    left = top = 0.5
    right = bottom = size - 0.5
    if left + radius <= x <= right - radius or top + radius <= y <= bottom - radius:
        return left <= x <= right and top <= y <= bottom
    center_x = left + radius if x < left + radius else right - radius
    center_y = top + radius if y < top + radius else bottom - radius
    return (x - center_x) ** 2 + (y - center_y) ** 2 <= radius**2


def _bar_alpha(x: float, y: float, size: int) -> float:
    centers = (0.31, 0.405, 0.5, 0.595, 0.69)
    heights = (0.24, 0.44, 0.62, 0.42, 0.25)
    width = max(1.2, size * 0.045)
    edge = max(0.7, size * 0.012)
    alpha = 0.0
    for center, height in zip(centers, heights):
        cx = size * center
        half_height = size * height / 2
        dx = abs(x - cx)
        dy = max(0.0, abs(y - size / 2) - half_height + width / 2)
        distance = math.hypot(max(0.0, dx - width / 2), dy)
        alpha = max(alpha, max(0.0, min(1.0, 1.0 - distance / edge)))
    return alpha


def _png(size: int) -> bytes:
    rows = bytearray()
    radius = size * 0.22
    for y in range(size):
        rows.append(0)
        for x in range(size):
            px = x + 0.5
            py = y + 0.5
            if not _inside_rounded_square(px, py, size, radius):
                rows.extend((0, 0, 0, 0))
                continue

            diagonal = (x + y) / max(1, 2 * size - 2)
            radial = math.hypot(x - size * 0.35, y - size * 0.25) / size
            mix = max(0.0, min(1.0, 0.62 * diagonal + 0.38 * radial))
            start = (62, 72, 214)
            end = (126, 63, 210)
            r = int(start[0] * (1 - mix) + end[0] * mix)
            g = int(start[1] * (1 - mix) + end[1] * mix)
            b = int(start[2] * (1 - mix) + end[2] * mix)

            wave = _bar_alpha(px, py, size)
            if wave:
                r = int(r * (1 - wave) + 255 * wave)
                g = int(g * (1 - wave) + 255 * wave)
                b = int(b * (1 - wave) + 255 * wave)
            rows.extend((r, g, b, 255))

    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        signature
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + _chunk(b"IEND", b"")
    )


def build_icon(path: Path) -> Path:
    images = [(size, _png(size)) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    directory = bytearray()
    payload = bytearray()
    for size, image in images:
        encoded_size = 0 if size == 256 else size
        directory.extend(
            struct.pack(
                "<BBBBHHII",
                encoded_size,
                encoded_size,
                0,
                0,
                1,
                32,
                len(image),
                offset,
            )
        )
        payload.extend(image)
        offset += len(image)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + directory + payload)
    return path


def main() -> None:
    destination = Path(__file__).with_name("VocalMore.ico")
    print(build_icon(destination))


if __name__ == "__main__":
    main()
