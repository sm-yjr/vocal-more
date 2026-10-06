// SPDX-License-Identifier: GPL-3.0-only
use super::{class, ffi, ns};
use anyhow::{Context, Result, bail};
use objc2::{
    msg_send,
    rc::{Retained, autoreleasepool},
    runtime::AnyObject,
};
use std::ptr;
pub const MAX_JPEG_BYTES: usize = 190 * 1024;

/// Main display only; pixels and JPEG bytes never leave memory in this adapter.
/// Invoke from the bounded platform lane, never from the audio/event-tap lane.
pub fn capture_screen(request_permission: bool) -> Result<Vec<u8>> {
    autoreleasepool(|_| unsafe {
        if !ffi::CGPreflightScreenCaptureAccess() {
            if request_permission {
                ffi::CGRequestScreenCaptureAccess();
            }
            bail!(
                "需要屏幕录制权限；请在系统设置 → 隐私与安全性 → 屏幕录制中允许 Vocal More，然后重试"
            );
        }
        let image = ffi::Owned::from_create(ffi::CGDisplayCreateImage(ffi::CGMainDisplayID()))
            .context("未能读取主显示器画面")?;
        let source_width = ffi::CGImageGetWidth(image.as_ptr());
        let source_height = ffi::CGImageGetHeight(image.as_ptr());
        let (width, height) =
            scaled_dimensions(source_width, source_height).context("屏幕截图尺寸无效")?;
        let space = ffi::Owned::from_create(ffi::CGColorSpaceCreateDeviceRGB())
            .context("无法创建屏幕色彩空间")?;
        for size_scale in [1.0, 0.82, 0.68] {
            let w = (width as f64 * size_scale).round().max(1.0) as usize;
            let h = (height as f64 * size_scale).round().max(1.0) as usize;
            let context = ffi::Owned::from_create(ffi::CGBitmapContextCreate(
                ptr::null_mut(),
                w,
                h,
                8,
                w * 4,
                space.as_ptr(),
                1,
            ))
            .context("无法创建屏幕图像缓冲区")?;
            ffi::CGContextSetInterpolationQuality(context.as_ptr(), 3);
            ffi::CGContextDrawImage(
                context.as_ptr(),
                ffi::Rect {
                    origin: ffi::Point { x: 0.0, y: 0.0 },
                    size: ffi::Size {
                        width: w as f64,
                        height: h as f64,
                    },
                },
                image.as_ptr(),
            );
            let scaled = ffi::Owned::from_create(ffi::CGBitmapContextCreateImage(context.as_ptr()))
                .context("无法缩放屏幕图像")?;
            for quality in [0.72, 0.58, 0.44, 0.32] {
                let data = ffi::Owned::from_create(ffi::CFDataCreateMutable(ptr::null(), 0))
                    .context("无法创建 JPEG 缓冲区")?;
                let destination = ffi::Owned::from_create(ffi::CGImageDestinationCreateWithData(
                    data.as_ptr(),
                    (&*ns("public.jpeg") as *const objc2_foundation::NSString).cast(),
                    1,
                    ptr::null(),
                ))
                .context("无法创建 JPEG 编码器")?;
                let number: Retained<AnyObject> =
                    msg_send![class(c"NSNumber"),numberWithDouble:quality];
                let key = ffi::kCGImageDestinationLossyCompressionQuality;
                let value = Retained::as_ptr(&number).cast();
                let properties = ffi::Owned::from_create(ffi::CFDictionaryCreate(
                    ptr::null(),
                    &key,
                    &value,
                    1,
                    ffi::kCFTypeDictionaryKeyCallBacks.as_ptr().cast(),
                    ffi::kCFTypeDictionaryValueCallBacks.as_ptr().cast(),
                ))
                .context("无法创建 JPEG 参数")?;
                ffi::CGImageDestinationAddImage(
                    destination.as_ptr(),
                    scaled.as_ptr(),
                    properties.as_ptr(),
                );
                if !ffi::CGImageDestinationFinalize(destination.as_ptr()) {
                    bail!("屏幕帧 JPEG 编码失败");
                }
                let length = usize::try_from(ffi::CFDataGetLength(data.as_ptr()))
                    .context("屏幕帧长度无效")?;
                if (3..=MAX_JPEG_BYTES).contains(&length) {
                    let encoded =
                        std::slice::from_raw_parts(ffi::CFDataGetBytePtr(data.as_ptr()), length);
                    if encoded.starts_with(&[0xff, 0xd8, 0xff]) {
                        return Ok(encoded.to_vec());
                    }
                }
            }
        }
        bail!("屏幕内容过于复杂，无法压缩到模型的 190 KiB 限制")
    })
}
fn scaled_dimensions(width: usize, height: usize) -> Option<(usize, usize)> {
    if width == 0 || height == 0 {
        return None;
    }
    let scale = 1.0_f64
        .min(1280.0 / width as f64)
        .min(720.0 / height as f64);
    Some((
        (width as f64 * scale).round().max(1.0) as usize,
        (height as f64 * scale).round().max(1.0) as usize,
    ))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bounds_preserve_aspect_without_upscaling() {
        assert_eq!(scaled_dimensions(3840, 2160), Some((1280, 720)));
        assert_eq!(scaled_dimensions(100, 200), Some((100, 200)));
        assert_eq!(scaled_dimensions(0, 200), None);
        assert_eq!(scaled_dimensions(1200, 1920), Some((450, 720)));
    }
}
