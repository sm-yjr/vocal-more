"""Script to generate placeholder icons for Vocal-More."""

import struct
import zlib
from pathlib import Path


def create_png(width: int, height: int, color: tuple) -> bytes:
    """Create a simple solid-color PNG image.

    Args:
        width: Image width in pixels
        height: Image height in pixels
        color: RGB color tuple (r, g, b) with values 0-255

    Returns:
        PNG image data as bytes
    """

    def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
        chunk_len = struct.pack(">I", len(data))
        chunk_crc = struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
        return chunk_len + chunk_type + data + chunk_crc

    # PNG signature
    signature = b"\x89PNG\r\n\x1a\n"

    # IHDR chunk (image header)
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr = png_chunk(b"IHDR", ihdr_data)

    # IDAT chunk (image data)
    raw_data = b""
    r, g, b = color
    for _ in range(height):
        raw_data += b"\x00"  # Filter byte (none)
        for _ in range(width):
            raw_data += bytes([r, g, b])

    compressed = zlib.compress(raw_data, 9)
    idat = png_chunk(b"IDAT", compressed)

    # IEND chunk
    iend = png_chunk(b"IEND", b"")

    return signature + ihdr + idat + iend


def create_circle_png(size: int, bg_color: tuple, circle_color: tuple) -> bytes:
    """Create a PNG with a circle in the center.

    Args:
        size: Image size (width and height)
        bg_color: Background RGB color tuple
        circle_color: Circle RGB color tuple

    Returns:
        PNG image data as bytes
    """

    def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
        chunk_len = struct.pack(">I", len(data))
        chunk_crc = struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
        return chunk_len + chunk_type + data + chunk_crc

    # PNG signature
    signature = b"\x89PNG\r\n\x1a\n"

    # IHDR chunk
    ihdr_data = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    ihdr = png_chunk(b"IHDR", ihdr_data)

    # Generate image data with circle
    raw_data = b""
    center = size // 2
    radius = size // 3

    for y in range(size):
        raw_data += b"\x00"  # Filter byte
        for x in range(size):
            # Check if inside circle
            dist_sq = (x - center) ** 2 + (y - center) ** 2
            if dist_sq <= radius**2:
                raw_data += bytes(circle_color)
            else:
                raw_data += bytes(bg_color)

    compressed = zlib.compress(raw_data, 9)
    idat = png_chunk(b"IDAT", compressed)
    iend = png_chunk(b"IEND", b"")

    return signature + ihdr + idat + iend


def main():
    """Generate all icon files."""
    icons_dir = Path(__file__).parent.parent / "resources" / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)

    size = 22  # Menu bar icon size

    # Idle icon - gray circle
    idle_png = create_circle_png(size, (0, 0, 0), (128, 128, 128))
    (icons_dir / "icon_idle.png").write_bytes(idle_png)
    print(f"Created {icons_dir / 'icon_idle.png'}")

    # Recording icon - red circle
    recording_png = create_circle_png(size, (0, 0, 0), (255, 59, 48))
    (icons_dir / "icon_recording.png").write_bytes(recording_png)
    print(f"Created {icons_dir / 'icon_recording.png'}")

    # Processing icon - orange circle
    processing_png = create_circle_png(size, (0, 0, 0), (255, 149, 0))
    (icons_dir / "icon_processing.png").write_bytes(processing_png)
    print(f"Created {icons_dir / 'icon_processing.png'}")


if __name__ == "__main__":
    main()
