"""Privacy-scoped in-memory screen frames for Qwen realtime context."""

from __future__ import annotations

MAX_JPEG_BYTES = 190 * 1024
MAX_WIDTH = 1280
MAX_HEIGHT = 720


class ScreenCaptureError(RuntimeError):
    """A screen frame could not be captured or safely encoded."""


def _scaled_jpeg(image, *, max_bytes: int = MAX_JPEG_BYTES) -> bytes:
    from Foundation import NSMutableData
    from Quartz import (
        CGBitmapContextCreate,
        CGBitmapContextCreateImage,
        CGColorSpaceCreateDeviceRGB,
        CGContextDrawImage,
        CGContextSetInterpolationQuality,
        CGImageDestinationAddImage,
        CGImageDestinationCreateWithData,
        CGImageDestinationFinalize,
        CGImageGetHeight,
        CGImageGetWidth,
        CGRectMake,
        kCGImageAlphaPremultipliedLast,
        kCGImageDestinationLossyCompressionQuality,
        kCGInterpolationHigh,
    )

    source_width = int(CGImageGetWidth(image))
    source_height = int(CGImageGetHeight(image))
    if source_width <= 0 or source_height <= 0:
        raise ScreenCaptureError("屏幕截图尺寸无效")

    scale = min(1.0, MAX_WIDTH / source_width, MAX_HEIGHT / source_height)
    width = max(1, round(source_width * scale))
    height = max(1, round(source_height * scale))
    color_space = CGColorSpaceCreateDeviceRGB()

    # A second size pass is preferable to spilling a sensitive full-resolution
    # frame to disk.  The provider recommends 480p/720p and caps base64 at
    # 256 KiB, so keep the JPEG itself below 190 KiB.
    for size_scale in (1.0, 0.82, 0.68):
        target_width = max(1, round(width * size_scale))
        target_height = max(1, round(height * size_scale))
        context = CGBitmapContextCreate(
            None,
            target_width,
            target_height,
            8,
            target_width * 4,
            color_space,
            kCGImageAlphaPremultipliedLast,
        )
        if context is None:
            raise ScreenCaptureError("无法创建屏幕图像缓冲区")
        CGContextSetInterpolationQuality(context, kCGInterpolationHigh)
        CGContextDrawImage(
            context,
            CGRectMake(0, 0, target_width, target_height),
            image,
        )
        scaled = CGBitmapContextCreateImage(context)
        for quality in (0.72, 0.58, 0.44, 0.32):
            data = NSMutableData.data()
            destination = CGImageDestinationCreateWithData(
                data, "public.jpeg", 1, None
            )
            if destination is None:
                raise ScreenCaptureError("无法创建 JPEG 编码器")
            CGImageDestinationAddImage(
                destination,
                scaled,
                {kCGImageDestinationLossyCompressionQuality: quality},
            )
            if not CGImageDestinationFinalize(destination):
                raise ScreenCaptureError("屏幕帧 JPEG 编码失败")
            encoded = bytes(data)
            if encoded.startswith(b"\xff\xd8\xff") and len(encoded) <= max_bytes:
                return encoded
    raise ScreenCaptureError("屏幕内容过于复杂，无法压缩到模型的 190 KiB 限制")


def capture_main_display_jpeg(*, request_permission: bool = False) -> bytes:
    """Capture the main display to an in-memory 720p-or-smaller JPEG."""
    from Quartz import (
        CGDisplayCreateImage,
        CGMainDisplayID,
        CGPreflightScreenCaptureAccess,
        CGRequestScreenCaptureAccess,
    )

    if not CGPreflightScreenCaptureAccess():
        if request_permission:
            CGRequestScreenCaptureAccess()
        raise ScreenCaptureError(
            "需要屏幕录制权限；请在系统设置 → 隐私与安全性 → 屏幕录制中允许 Vocal More，然后重试"
        )
    image = CGDisplayCreateImage(CGMainDisplayID())
    if image is None:
        raise ScreenCaptureError("未能读取主显示器画面")
    return _scaled_jpeg(image)
