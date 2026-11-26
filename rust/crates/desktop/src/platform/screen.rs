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
/// Minimum active window intersection size (in display points) worth cropping to;
/// smaller popups or toolbars fall back to their full containing display.
const MIN_WINDOW_CROP_WIDTH: f64 = 240.0;
const MIN_WINDOW_CROP_HEIGHT: f64 = 160.0;
const FINGERPRINT_DIM: usize = 16;
pub const FINGERPRINT_LEN: usize = FINGERPRINT_DIM * FINGERPRINT_DIM;

const K_CG_WINDOW_LIST_OPTION_ON_SCREEN_ONLY: u32 = 1 << 0;
const K_CG_WINDOW_LIST_EXCLUDE_DESKTOP_ELEMENTS: u32 = 1 << 4;
const K_CF_NUMBER_SINT64_TYPE: isize = 4;

#[derive(Clone, Debug)]
pub struct CapturedFrame {
    pub jpeg: Vec<u8>,
    pub fingerprint: [u8; FINGERPRINT_LEN],
}

/// Captures the active window (cropped to its containing display for 2x-4x higher
/// text readability on HiDPI/multi-monitor setups) or falls back to the active/main
/// display. Pixels and JPEG bytes stay in memory only.
/// Invoke from the bounded platform lane, never from the audio/event-tap lane.
pub fn capture_screen(request_permission: bool) -> Result<CapturedFrame> {
    autoreleasepool(|_| unsafe {
        if !ffi::CGPreflightScreenCaptureAccess() {
            if request_permission {
                ffi::CGRequestScreenCaptureAccess();
            }
            bail!(
                "需要屏幕录制权限；请在系统设置 → 隐私与安全性 → 屏幕录制中允许 Vocal More，然后重试"
            );
        }
        let image = capture_focused_image().context("未能读取当前屏幕画面")?;
        let source_width = ffi::CGImageGetWidth(image.as_ptr());
        let source_height = ffi::CGImageGetHeight(image.as_ptr());
        let (width, height) =
            scaled_dimensions(source_width, source_height).context("屏幕截图尺寸无效")?;
        let space = ffi::Owned::from_create(ffi::CGColorSpaceCreateDeviceRGB())
            .context("无法创建屏幕色彩空间")?;
        let fingerprint =
            compute_fingerprint(image.as_ptr(), space.as_ptr()).unwrap_or([0u8; FINGERPRINT_LEN]);

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
            for quality in [0.76, 0.62, 0.48, 0.34] {
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
                        return Ok(CapturedFrame {
                            jpeg: encoded.to_vec(),
                            fingerprint,
                        });
                    }
                }
            }
        }
        bail!("屏幕内容过于复杂，无法压缩到模型的 190 KiB 限制")
    })
}

/// Returns true when two 16x16 luma fingerprints are visually nearly identical
/// (e.g. static screen or a blinking cursor), so periodic uploads can be skipped.
pub fn frames_visually_similar(a: &[u8; FINGERPRINT_LEN], b: &[u8; FINGERPRINT_LEN]) -> bool {
    let mut total_diff = 0_u32;
    let mut changed_cells = 0_usize;
    for (&lhs, &rhs) in a.iter().zip(b.iter()) {
        let diff = lhs.abs_diff(rhs);
        total_diff += diff as u32;
        if diff >= 12 {
            changed_cells += 1;
        }
    }
    let mean_diff = total_diff as f64 / FINGERPRINT_LEN as f64;
    mean_diff < 3.0 && changed_cells <= 4
}

unsafe fn compute_fingerprint(image: ffi::Ref, space: ffi::Ref) -> Option<[u8; FINGERPRINT_LEN]> {
    let mut rgba = vec![0u8; FINGERPRINT_DIM * FINGERPRINT_DIM * 4];
    let ctx = unsafe {
        ffi::Owned::from_create(ffi::CGBitmapContextCreate(
            rgba.as_mut_ptr().cast(),
            FINGERPRINT_DIM,
            FINGERPRINT_DIM,
            8,
            FINGERPRINT_DIM * 4,
            space,
            1, // kCGImageAlphaPremultipliedLast
        ))
    }?;
    unsafe {
        ffi::CGContextSetInterpolationQuality(ctx.as_ptr(), 1);
        ffi::CGContextDrawImage(
            ctx.as_ptr(),
            ffi::Rect {
                origin: ffi::Point { x: 0.0, y: 0.0 },
                size: ffi::Size {
                    width: FINGERPRINT_DIM as f64,
                    height: FINGERPRINT_DIM as f64,
                },
            },
            image,
        );
    }
    let mut luma = [0u8; FINGERPRINT_LEN];
    for (idx, px) in rgba.chunks_exact(4).enumerate() {
        // Rec. 601 integer luma approximation
        let y = ((77 * px[0] as u32) + (150 * px[1] as u32) + (29 * px[2] as u32)) >> 8;
        luma[idx] = y as u8;
    }
    Some(luma)
}

unsafe fn capture_focused_image() -> Option<ffi::Owned> {
    let main_display = unsafe { ffi::CGMainDisplayID() };
    if let Some(window_rect) = unsafe { frontmost_window_bounds() } {
        let mut display_ids = [0u32; 8];
        let mut count = 0u32;
        let err = unsafe {
            ffi::CGGetDisplaysWithRect(
                window_rect,
                display_ids.len() as u32,
                display_ids.as_mut_ptr(),
                &mut count,
            )
        };
        if err == 0 && count > 0 {
            let displays: Vec<(u32, ffi::Rect)> = display_ids[..count as usize]
                .iter()
                .map(|&id| (id, unsafe { ffi::CGDisplayBounds(id) }))
                .collect();
            let (display_id, local_crop) =
                select_capture_region(Some(window_rect), &displays, main_display);
            if let Some(crop) = local_crop
                && let Some(cropped) = unsafe {
                    ffi::Owned::from_create(ffi::CGDisplayCreateImageForRect(display_id, crop))
                }
            {
                return Some(cropped);
            }
            if let Some(full) =
                unsafe { ffi::Owned::from_create(ffi::CGDisplayCreateImage(display_id)) }
            {
                return Some(full);
            }
        }
    }
    unsafe { ffi::Owned::from_create(ffi::CGDisplayCreateImage(main_display)) }
}

unsafe fn frontmost_window_bounds() -> Option<ffi::Rect> {
    let workspace: Option<Retained<AnyObject>> =
        unsafe { msg_send![class(c"NSWorkspace"), sharedWorkspace] };
    let front_app: Option<Retained<AnyObject>> =
        workspace.and_then(|ws| unsafe { msg_send![&*ws, frontmostApplication] });
    let front_pid: i32 = front_app
        .map(|app| unsafe { msg_send![&*app, processIdentifier] })
        .unwrap_or(0);
    let own_pid = std::process::id() as i32;
    let target_pid = if front_pid > 0 && front_pid != own_pid {
        Some(front_pid)
    } else {
        None
    };

    let list = unsafe {
        ffi::Owned::from_create(ffi::CGWindowListCopyWindowInfo(
            K_CG_WINDOW_LIST_OPTION_ON_SCREEN_ONLY | K_CG_WINDOW_LIST_EXCLUDE_DESKTOP_ELEMENTS,
            0,
        ))
    }?;
    let count = unsafe { ffi::CFArrayGetCount(list.as_ptr()) };
    let pid_key = ns("kCGWindowOwnerPID");
    let layer_key = ns("kCGWindowLayer");
    let bounds_key = ns("kCGWindowBounds");

    let mut fallback: Option<ffi::Rect> = None;
    for idx in 0..count {
        let dict = unsafe { ffi::CFArrayGetValueAtIndex(list.as_ptr(), idx) };
        if dict.is_null() {
            continue;
        }
        let layer = unsafe {
            dict_i64(
                dict,
                (&*layer_key as *const objc2_foundation::NSString).cast(),
            )
        }
        .unwrap_or(-1);
        if layer != 0 {
            continue;
        }
        let pid = unsafe {
            dict_i64(
                dict,
                (&*pid_key as *const objc2_foundation::NSString).cast(),
            )
        }
        .unwrap_or(0) as i32;
        if pid <= 0 || pid == own_pid {
            continue;
        }
        let bounds_dict = unsafe {
            ffi::CFDictionaryGetValue(
                dict,
                (&*bounds_key as *const objc2_foundation::NSString).cast(),
            )
        };
        if bounds_dict.is_null() {
            continue;
        }
        let mut rect = ffi::Rect {
            origin: ffi::Point { x: 0.0, y: 0.0 },
            size: ffi::Size {
                width: 0.0,
                height: 0.0,
            },
        };
        if !unsafe { ffi::CGRectMakeWithDictionaryRepresentation(bounds_dict, &mut rect) } {
            continue;
        }
        if rect.size.width < 120.0 || rect.size.height < 80.0 {
            continue;
        }
        if target_pid == Some(pid) {
            return Some(rect);
        }
        if fallback.is_none() {
            fallback = Some(rect);
        }
    }
    fallback
}

unsafe fn dict_i64(dict: ffi::Ref, key: ffi::Ref) -> Option<i64> {
    let raw = unsafe { ffi::CFDictionaryGetValue(dict, key) };
    if raw.is_null() || unsafe { ffi::CFGetTypeID(raw) != ffi::CFNumberGetTypeID() } {
        return None;
    }
    let mut out = 0_i64;
    if unsafe { ffi::CFNumberGetValue(raw, K_CF_NUMBER_SINT64_TYPE, (&mut out as *mut i64).cast()) }
    {
        Some(out)
    } else {
        None
    }
}

fn rect_intersection(a: ffi::Rect, b: ffi::Rect) -> Option<ffi::Rect> {
    let x1 = a.origin.x.max(b.origin.x);
    let y1 = a.origin.y.max(b.origin.y);
    let x2 = (a.origin.x + a.size.width).min(b.origin.x + b.size.width);
    let y2 = (a.origin.y + a.size.height).min(b.origin.y + b.size.height);
    let width = x2 - x1;
    let height = y2 - y1;
    (width > 0.0 && height > 0.0).then_some(ffi::Rect {
        origin: ffi::Point { x: x1, y: y1 },
        size: ffi::Size { width, height },
    })
}

/// Chooses the target display (the one holding the largest visible area of the
/// active window) and an optional display-local crop rectangle.
fn select_capture_region(
    window: Option<ffi::Rect>,
    displays: &[(u32, ffi::Rect)],
    main_display: u32,
) -> (u32, Option<ffi::Rect>) {
    let Some(window) = window else {
        return (main_display, None);
    };
    let best = displays
        .iter()
        .filter_map(|&(id, bounds)| {
            let inter = rect_intersection(window, bounds)?;
            let area = inter.size.width * inter.size.height;
            Some((id, bounds, inter, area))
        })
        .max_by(|a, b| a.3.total_cmp(&b.3));
    let Some((display_id, bounds, inter, _)) = best else {
        return (main_display, None);
    };
    if inter.size.width < MIN_WINDOW_CROP_WIDTH || inter.size.height < MIN_WINDOW_CROP_HEIGHT {
        return (display_id, None);
    }
    let local = ffi::Rect {
        origin: ffi::Point {
            x: inter.origin.x - bounds.origin.x,
            y: inter.origin.y - bounds.origin.y,
        },
        size: inter.size,
    };
    (display_id, Some(local))
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

    fn rect(x: f64, y: f64, width: f64, height: f64) -> ffi::Rect {
        ffi::Rect {
            origin: ffi::Point { x, y },
            size: ffi::Size { width, height },
        }
    }

    #[test]
    fn bounds_preserve_aspect_without_upscaling() {
        assert_eq!(scaled_dimensions(3840, 2160), Some((1280, 720)));
        assert_eq!(scaled_dimensions(100, 200), Some((100, 200)));
        assert_eq!(scaled_dimensions(0, 200), None);
        assert_eq!(scaled_dimensions(1200, 1920), Some((450, 720)));
    }

    #[test]
    fn selects_secondary_display_and_local_crop_for_active_window() {
        let displays = [
            (1, rect(0.0, 0.0, 1920.0, 1080.0)),
            (2, rect(1920.0, 0.0, 2560.0, 1440.0)),
        ];
        // Window sits mostly on the secondary display (id = 2)
        let (display, crop) =
            select_capture_region(Some(rect(2100.0, 100.0, 1200.0, 900.0)), &displays, 1);
        assert_eq!(display, 2);
        let crop = crop.expect("large window should crop");
        assert!((crop.origin.x - 180.0).abs() < 0.01);
        assert!((crop.origin.y - 100.0).abs() < 0.01);
        assert!((crop.size.width - 1200.0).abs() < 0.01);
        assert!((crop.size.height - 900.0).abs() < 0.01);
    }

    #[test]
    fn small_popup_falls_back_to_its_display_without_tiny_crop() {
        let displays = [
            (1, rect(0.0, 0.0, 1920.0, 1080.0)),
            (2, rect(1920.0, 0.0, 2560.0, 1440.0)),
        ];
        let (display, crop) =
            select_capture_region(Some(rect(2000.0, 100.0, 180.0, 120.0)), &displays, 1);
        assert_eq!(display, 2);
        assert!(crop.is_none());
    }

    #[test]
    fn frame_similarity_skips_static_and_blinking_cursor_but_detects_real_change() {
        let base = [100u8; FINGERPRINT_LEN];
        assert!(frames_visually_similar(&base, &base));

        let mut cursor_blink = base;
        cursor_blink[42] = 180;
        cursor_blink[43] = 150;
        assert!(frames_visually_similar(&base, &cursor_blink));

        let mut scrolled = base;
        for cell in &mut scrolled[..20] {
            *cell = 160;
        }
        assert!(!frames_visually_similar(&base, &scrolled));
    }
}
