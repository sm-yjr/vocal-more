// SPDX-License-Identifier: GPL-3.0-only
//! Narrow C ABI for the native services used by the desktop host.
use std::{
    ffi::{c_char, c_void},
    ptr::NonNull,
};

pub type Ref = *const c_void;
pub type MutRef = *mut c_void;
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct Range {
    pub location: isize,
    pub length: isize,
}
#[repr(C)]
#[derive(Clone, Copy)]
pub struct Point {
    pub x: f64,
    pub y: f64,
}
#[repr(C)]
#[derive(Clone, Copy)]
pub struct Size {
    pub width: f64,
    pub height: f64,
}
#[repr(C)]
#[derive(Clone, Copy)]
pub struct Rect {
    pub origin: Point,
    pub size: Size,
}
#[repr(C)]
#[derive(Clone, Copy)]
pub struct Time {
    pub value: i64,
    pub timescale: i32,
    pub flags: u32,
    pub epoch: i64,
}
unsafe impl objc2::Encode for Time {
    const ENCODING: objc2::encode::Encoding = objc2::encode::Encoding::Struct(
        "?",
        &[
            objc2::encode::Encoding::LongLong,
            objc2::encode::Encoding::Int,
            objc2::encode::Encoding::UInt,
            objc2::encode::Encoding::LongLong,
        ],
    );
}

pub struct Owned(NonNull<c_void>);
impl Owned {
    pub unsafe fn from_create(value: Ref) -> Option<Self> {
        NonNull::new(value.cast_mut()).map(Self)
    }
    pub fn as_ptr(&self) -> Ref {
        self.0.as_ptr()
    }
}
impl Clone for Owned {
    fn clone(&self) -> Self {
        unsafe {
            CFRetain(self.as_ptr());
            Self(self.0)
        }
    }
}
impl Drop for Owned {
    fn drop(&mut self) {
        unsafe { CFRelease(self.as_ptr()) }
    }
}

#[link(name = "CoreFoundation", kind = "framework")]
unsafe extern "C" {
    pub fn CFRetain(value: Ref) -> Ref;
    pub fn CFRelease(value: Ref);
    pub fn CFGetTypeID(value: Ref) -> usize;
    pub fn CFHash(value: Ref) -> usize;
    pub fn CFStringGetTypeID() -> usize;
    pub fn CFNumberGetTypeID() -> usize;
    pub fn CFBooleanGetTypeID() -> usize;
    pub fn CFStringGetLength(value: Ref) -> isize;
    pub fn CFStringGetMaximumSizeForEncoding(length: isize, encoding: u32) -> isize;
    pub fn CFStringGetCString(value: Ref, buffer: *mut c_char, size: isize, encoding: u32) -> bool;
    pub fn CFDataCreateMutable(allocator: Ref, capacity: isize) -> Ref;
    pub fn CFDataGetLength(data: Ref) -> isize;
    pub fn CFDataGetBytePtr(data: Ref) -> *const u8;
    pub fn CFDictionaryCreate(
        allocator: Ref,
        keys: *const Ref,
        values: *const Ref,
        count: isize,
        key_callbacks: Ref,
        value_callbacks: Ref,
    ) -> Ref;
    pub fn CFDictionaryGetValue(dictionary: Ref, key: Ref) -> Ref;
    pub static kCFTypeDictionaryKeyCallBacks: [usize; 6];
    pub static kCFTypeDictionaryValueCallBacks: [usize; 5];
    pub static kCFBooleanTrue: Ref;
    pub static kCFPreferencesCurrentUser: Ref;
    pub static kCFPreferencesAnyHost: Ref;
    pub fn CFPreferencesCopyValue(key: Ref, domain: Ref, user: Ref, host: Ref) -> Ref;
    pub fn CFPreferencesSetValue(key: Ref, value: Ref, domain: Ref, user: Ref, host: Ref);
    pub fn CFPreferencesSynchronize(domain: Ref, user: Ref, host: Ref) -> bool;
    pub fn CFMachPortCreateRunLoopSource(allocator: Ref, port: Ref, order: isize) -> Ref;
    pub fn CFMachPortInvalidate(port: Ref);
    pub fn CFRunLoopGetCurrent() -> Ref;
    pub fn CFRunLoopAddSource(run_loop: Ref, source: Ref, mode: Ref);
    pub fn CFRunLoopRemoveSource(run_loop: Ref, source: Ref, mode: Ref);
    pub fn CFRunLoopRun();
    pub fn CFRunLoopStop(run_loop: Ref);
    pub static kCFRunLoopCommonModes: Ref;
}

pub type TapCallback = unsafe extern "C" fn(MutRef, u32, MutRef, MutRef) -> MutRef;
#[link(name = "ApplicationServices", kind = "framework")]
unsafe extern "C" {
    pub fn AXIsProcessTrusted() -> bool;
    pub fn AXIsProcessTrustedWithOptions(options: Ref) -> bool;
    pub fn AXUIElementCreateSystemWide() -> Ref;
    pub fn AXUIElementCopyAttributeValue(element: Ref, name: Ref, out: *mut Ref) -> i32;
    pub fn AXUIElementGetPid(element: Ref, pid: *mut i32) -> i32;
    pub fn AXUIElementSetMessagingTimeout(element: Ref, seconds: f32) -> i32;
    pub fn AXValueGetType(value: Ref) -> u32;
    pub fn AXValueGetValue(value: Ref, kind: u32, out: MutRef) -> bool;
    pub fn CGEventTapCreate(
        location: u32,
        placement: u32,
        options: u32,
        mask: u64,
        callback: TapCallback,
        user: MutRef,
    ) -> Ref;
    pub fn CGEventTapEnable(tap: Ref, enable: bool);
    pub fn CGEventTapIsEnabled(tap: Ref) -> bool;
    pub fn CGEventGetIntegerValueField(event: Ref, field: u32) -> i64;
    pub fn CGEventGetFlags(event: Ref) -> u64;
    pub fn CGEventSourceCreate(state: i32) -> Ref;
    pub fn CGEventCreateKeyboardEvent(source: Ref, key: u16, down: bool) -> Ref;
    pub fn CGEventSetFlags(event: Ref, flags: u64);
    pub fn CGEventSetIntegerValueField(event: Ref, field: u32, value: i64);
    pub fn CGEventPost(location: u32, event: Ref);
}

#[link(name = "CoreGraphics", kind = "framework")]
unsafe extern "C" {
    pub fn CGMainDisplayID() -> u32;
    pub fn CGSessionCopyCurrentDictionary() -> Ref;
    pub fn CGDisplayCreateImage(display: u32) -> Ref;
    pub fn CGPreflightScreenCaptureAccess() -> bool;
    pub fn CGRequestScreenCaptureAccess() -> bool;
    pub fn CGImageGetWidth(image: Ref) -> usize;
    pub fn CGImageGetHeight(image: Ref) -> usize;
    pub fn CGColorSpaceCreateDeviceRGB() -> Ref;
    pub fn CGBitmapContextCreate(
        data: MutRef,
        width: usize,
        height: usize,
        bits: usize,
        stride: usize,
        space: Ref,
        info: u32,
    ) -> Ref;
    pub fn CGContextSetInterpolationQuality(context: Ref, quality: i32);
    pub fn CGContextDrawImage(context: Ref, rect: Rect, image: Ref);
    pub fn CGBitmapContextCreateImage(context: Ref) -> Ref;
}
#[link(name = "ImageIO", kind = "framework")]
unsafe extern "C" {
    pub static kCGImageDestinationLossyCompressionQuality: Ref;
    pub fn CGImageDestinationCreateWithData(
        data: Ref,
        kind: Ref,
        count: usize,
        options: Ref,
    ) -> Ref;
    pub fn CGImageDestinationAddImage(destination: Ref, image: Ref, properties: Ref);
    pub fn CGImageDestinationFinalize(destination: Ref) -> bool;
}
#[link(name = "CoreMedia", kind = "framework")]
unsafe extern "C" {
    pub fn CMTimeGetSeconds(time: Time) -> f64;
}
#[link(name = "AVFoundation", kind = "framework")]
unsafe extern "C" {}
#[link(name = "AppKit", kind = "framework")]
unsafe extern "C" {}
unsafe extern "C" {
    pub fn dlopen(path: *const c_char, mode: i32) -> MutRef;
    pub fn dlsym(handle: MutRef, symbol: *const c_char) -> MutRef;
    pub fn dlclose(handle: MutRef) -> i32;
}

pub fn cf_string(value: Ref) -> Option<String> {
    if value.is_null() {
        return None;
    }
    unsafe {
        if CFGetTypeID(value) != CFStringGetTypeID() {
            return None;
        }
        let length = CFStringGetMaximumSizeForEncoding(CFStringGetLength(value), 0x08000100);
        if !(0..=16 * 1024 * 1024).contains(&length) {
            return None;
        }
        let mut bytes = vec![0; length as usize + 1];
        if !CFStringGetCString(
            value,
            bytes.as_mut_ptr().cast(),
            bytes.len() as isize,
            0x08000100,
        ) {
            return None;
        }
        let end = bytes.iter().position(|v| *v == 0).unwrap_or(bytes.len());
        String::from_utf8(bytes[..end].to_vec()).ok()
    }
}
