// SPDX-License-Identifier: GPL-3.0-only
//! Rust-owned AppKit capsule. This is a direct port of the shipping native
//! renderer; it never creates a WebView or enters the application's key window.
mod model;

use crate::bridge::CommandSink;
use anyhow::{Context, Result};
use model::*;
use objc2::rc::Retained;
use objc2::runtime::AnyObject;
use objc2::{DefinedClass, MainThreadMarker, MainThreadOnly, define_class, msg_send, sel};
use objc2_app_kit::{
    NSAttributedStringNSStringDrawingDeprecated, NSBackingStoreType, NSBitmapImageFileType,
    NSButton, NSColor, NSEvent, NSFont, NSFontAttributeName, NSFontWeightMedium, NSPanel, NSScreen,
    NSScrollView, NSStringDrawingOptions, NSTextAlignment, NSTextField, NSTextView, NSView,
    NSWindowCollectionBehavior, NSWindowStyleMask, NSWorkspace,
};
use objc2_core_foundation::{CGPoint, CGRect, CGSize};
use objc2_core_graphics::CGColor;
use objc2_foundation::{
    NSAttributedString, NSDictionary, NSObject, NSObjectProtocol, NSPoint, NSRange, NSRect, NSSize,
    NSString,
};
use objc2_quartz_core::{CALayer, CATransaction};
use serde_json::{Value, json};
use std::{cell::Cell, path::Path, rc::Rc, time::Instant};

struct ActionIvars {
    commands: CommandSink,
    cancel: Rc<Cell<bool>>,
    cancel_count: Cell<u64>,
    finish_count: Cell<u64>,
}

define_class!(
    // SAFETY: NSObject has no subclassing requirements. AppKit invokes actions
    // on the main thread; the main-thread class also confines its Rc ownership.
    #[unsafe(super = NSObject)]
    #[thread_kind = MainThreadOnly]
    #[ivars = ActionIvars]
    struct CapsuleActionTarget;
    unsafe impl NSObjectProtocol for CapsuleActionTarget {}
    impl CapsuleActionTarget {
        #[unsafe(method(cancel:))]
        fn cancel(&self, _sender: Option<&AnyObject>) {
            self.ivars().cancel_count.set(self.ivars().cancel_count.get().saturating_add(1));
            self.ivars().cancel.set(true);
            self.ivars().commands.request("cancel", json!({}));
        }
        #[unsafe(method(finish:))]
        fn finish(&self, _sender: Option<&AnyObject>) {
            self.ivars().finish_count.set(self.ivars().finish_count.get().saturating_add(1));
            self.ivars().commands.request("finish", json!({}));
        }
    }
);

define_class!(
    // SAFETY: NSPanel is subclassable. This panel owns no delegate or foreign
    // pointer and explicitly refuses becoming the focused/main window.
    #[unsafe(super = NSPanel)]
    #[thread_kind = MainThreadOnly]
    struct CapsulePanel;
    unsafe impl NSObjectProtocol for CapsulePanel {}
    impl CapsulePanel {
        #[unsafe(method(canBecomeKeyWindow))]
        fn can_become_key_window(&self) -> bool { false }
        #[unsafe(method(canBecomeMainWindow))]
        fn can_become_main_window(&self) -> bool { false }
    }
);

struct NoLayerActions;
impl NoLayerActions {
    fn begin() -> Self {
        CATransaction::begin();
        CATransaction::setDisableActions(true);
        Self
    }
}
impl Drop for NoLayerActions {
    fn drop(&mut self) {
        CATransaction::commit();
    }
}

fn rect(frame: Frame) -> NSRect {
    NSRect::new(
        NSPoint::new(frame.x, frame.y),
        NSSize::new(frame.width, frame.height),
    )
}
fn cgrect(frame: Frame) -> CGRect {
    CGRect::new(
        CGPoint::new(frame.x, frame.y),
        CGSize::new(frame.width, frame.height),
    )
}
fn configure_layer(view: &NSView, white: f64, alpha: f64, radius: f64) -> Retained<CALayer> {
    view.setWantsLayer(true);
    let layer = view.layer().expect("layer-backed view must have a layer");
    layer.setBackgroundColor(Some(&CGColor::new_generic_rgb(white, white, white, alpha)));
    layer.setCornerRadius(radius);
    layer
}
fn label(mtm: MainThreadMarker) -> Retained<NSTextField> {
    let label = NSTextField::labelWithString(&NSString::from_str(""), mtm);
    label.setFont(Some(&NSFont::systemFontOfSize_weight(12.0, unsafe {
        NSFontWeightMedium
    })));
    label.setTextColor(Some(&NSColor::colorWithWhite_alpha(1.0, 0.82)));
    label.setAlignment(NSTextAlignment::Center);
    label
}

struct Renderer {
    view: Retained<NSView>,
    surface: Retained<NSView>,
    cancel_button: Retained<NSButton>,
    finish_button: Retained<NSButton>,
    recording_label: Retained<NSTextField>,
    thinking_label: Retained<NSTextField>,
    streaming_scroll: Retained<NSScrollView>,
    streaming_label: Retained<NSTextView>,
    progress_track: Retained<NSView>,
    progress_fill: Retained<NSView>,
    waveform: Vec<Retained<CALayer>>,
    _action_target: Retained<CapsuleActionTarget>,
    width: f64,
    height: f64,
    mode: String,
    state: State,
    language: String,
    stage: String,
    streaming_text: String,
    connection_title: String,
    expanded: bool,
    reduce_motion: bool,
    animation: Animation,
    layout: Layout,
}

impl Renderer {
    fn new(mtm: MainThreadMarker, commands: CommandSink, cancel: Rc<Cell<bool>>) -> Self {
        let _transaction = NoLayerActions::begin();
        let view = NSView::initWithFrame(
            NSView::alloc(mtm),
            rect(Frame::new(0.0, 0.0, COMPACT_WIDTH, COMPACT_HEIGHT)),
        );
        view.setWantsLayer(true);
        let surface =
            NSView::initWithFrame(NSView::alloc(mtm), rect(Frame::new(0.0, 0.0, 64.0, 36.0)));
        let layer = configure_layer(&surface, 0.0, 1.0, 18.0);
        layer.setBorderWidth(1.0);
        layer.setBorderColor(Some(&CGColor::new_generic_rgb(1.0, 1.0, 1.0, 0.32)));
        layer.setShadowColor(Some(&CGColor::new_generic_rgb(0.0, 0.0, 0.0, 1.0)));
        layer.setShadowOpacity(0.35);
        layer.setShadowRadius(15.0);
        layer.setShadowOffset(CGSize::new(0.0, -8.0));
        view.addSubview(&surface);
        let target = CapsuleActionTarget::alloc(mtm).set_ivars(ActionIvars {
            commands,
            cancel,
            cancel_count: Cell::new(0),
            finish_count: Cell::new(0),
        });
        let target: Retained<CapsuleActionTarget> = unsafe { msg_send![super(target), init] };
        let make_button = |title: &str, action| {
            let b = NSButton::initWithFrame(
                NSButton::alloc(mtm),
                rect(Frame::new(0.0, 0.0, 22.0, 22.0)),
            );
            b.setTitle(&NSString::from_str(title));
            b.setBordered(false);
            b.setFont(Some(&NSFont::systemFontOfSize_weight(13.0, unsafe {
                NSFontWeightMedium
            })));
            b.setContentTintColor(Some(&NSColor::colorWithWhite_alpha(1.0, 0.82)));
            // SAFETY: Retained target outlives buttons, selectors have matching
            // sender signatures, and buttons cannot change the key window.
            unsafe {
                b.setTarget(Some(&target));
                b.setAction(Some(action));
            }
            configure_layer(&b, 1.0, 0.15, 11.0);
            b
        };
        let cancel_button = make_button("×", sel!(cancel:));
        let finish_button = make_button("✓", sel!(finish:));
        let recording_label = label(mtm);
        let thinking_label = label(mtm);
        let streaming_scroll = NSScrollView::initWithFrame(
            NSScrollView::alloc(mtm),
            rect(Frame::new(0.0, 0.0, 336.0, 122.0)),
        );
        streaming_scroll.setDrawsBackground(false);
        streaming_scroll.setHasVerticalScroller(false);
        let streaming_label = NSTextView::initWithFrame(
            NSTextView::alloc(mtm),
            rect(Frame::new(0.0, 0.0, 336.0, 122.0)),
        );
        streaming_label.setEditable(false);
        streaming_label.setSelectable(false);
        streaming_label.setDrawsBackground(false);
        streaming_label.setFont(Some(&NSFont::systemFontOfSize(12.0)));
        streaming_label.setTextColor(Some(&NSColor::colorWithWhite_alpha(1.0, 0.72)));
        streaming_label.setTextContainerInset(NSSize::new(0.0, 0.0));
        // SAFETY: The standard NSTextView owns and retains its text container.
        let container = unsafe { streaming_label.textContainer() }.expect("text container");
        container.setLineFragmentPadding(0.0);
        streaming_label.setVerticallyResizable(true);
        streaming_label.setHorizontallyResizable(false);
        container.setWidthTracksTextView(true);
        streaming_scroll.setDocumentView(Some(&streaming_label));
        let waveform = (0..BAR_COUNT)
            .map(|_| {
                let b = CALayer::layer();
                b.setFrame(cgrect(Frame::new(0.0, 0.0, 2.0, 2.0)));
                b.setBackgroundColor(Some(&CGColor::new_generic_rgb(1.0, 1.0, 1.0, 0.9)));
                b.setCornerRadius(1.0);
                layer.addSublayer(&b);
                b
            })
            .collect();
        let progress_track =
            NSView::initWithFrame(NSView::alloc(mtm), rect(Frame::new(0.0, 0.0, 40.0, 3.0)));
        configure_layer(&progress_track, 1.0, 0.15, 1.5).setMasksToBounds(true);
        let progress_fill =
            NSView::initWithFrame(NSView::alloc(mtm), rect(Frame::new(0.0, 0.0, 0.0, 3.0)));
        configure_layer(&progress_fill, 1.0, 0.7, 1.5);
        progress_track.addSubview(&progress_fill);
        for v in [
            &*cancel_button as &NSView,
            &*finish_button,
            &*recording_label,
            &*thinking_label,
            &*streaming_scroll,
            &*progress_track,
        ] {
            surface.addSubview(v);
        }
        let animation = Animation::default();
        let initial_layout = layout(LayoutInput {
            width: COMPACT_WIDTH,
            height: COMPACT_HEIGHT,
            mode: "pushToTalk",
            state: State::Hidden,
            expanded: false,
            label_width: 0.0,
            thinking_width: 112.0,
            progress: 0.0,
            bar_heights: &animation.heights,
        });
        let mut result = Self {
            view,
            surface,
            cancel_button,
            finish_button,
            recording_label,
            thinking_label,
            streaming_scroll,
            streaming_label,
            progress_track,
            progress_fill,
            waveform,
            _action_target: target,
            width: COMPACT_WIDTH,
            height: COMPACT_HEIGHT,
            mode: "pushToTalk".into(),
            state: State::Hidden,
            language: "en".into(),
            stage: "transcribing".into(),
            streaming_text: String::new(),
            connection_title: String::new(),
            expanded: false,
            reduce_motion: Self::read_reduce_motion(),
            animation,
            layout: initial_layout,
        };
        result.surface.setAlphaValue(0.0);
        result.update_labels();
        result.relayout();
        result
    }
    fn read_reduce_motion() -> bool {
        NSWorkspace::sharedWorkspace().accessibilityDisplayShouldReduceMotion()
    }
    fn is_expanded(&self) -> bool {
        self.expanded && !self.streaming_text.trim().is_empty()
    }
    fn update_labels(&self) {
        self.recording_label
            .setStringValue(&NSString::from_str(if is_prompt(&self.mode) {
                translation(&self.language, "prompt")
            } else {
                ""
            }));
        self.thinking_label.setStringValue(&NSString::from_str(
            if self.state == State::ConnectionError {
                &self.connection_title
            } else {
                translation(&self.language, &self.stage)
            },
        ));
    }
    fn set_state(&mut self, state: State) {
        if self.state == state {
            return;
        }
        self.state = state;
        let _transaction = NoLayerActions::begin();
        self.surface
            .setAlphaValue(if state == State::Hidden { 0.0 } else { 1.0 });
        if matches!(state, State::Hidden | State::Recording | State::Processing) {
            self.streaming_text.clear();
            self.expanded = false;
            self.animation.progress = 0.0;
        }
        if state == State::Recording {
            self.stage = "transcribing".into();
            self.reduce_motion = Self::read_reduce_motion();
            self.animation.reset_waveform();
            for b in &self.waveform {
                b.setFrame(cgrect(Frame::new(0.0, 0.0, 2.0, 2.0)));
            }
        }
        self.update_labels();
        self.relayout();
    }
    fn relayout(&mut self) {
        let _transaction = NoLayerActions::begin();
        let previous_scroll_x = self.streaming_scroll.contentView().bounds().origin.x;
        self.layout = layout(LayoutInput {
            width: self.width,
            height: self.height,
            mode: &self.mode,
            state: self.state,
            expanded: self.is_expanded(),
            label_width: self.recording_label.intrinsicContentSize().width,
            thinking_width: self.thinking_label.intrinsicContentSize().width,
            progress: self.animation.progress,
            bar_heights: &self.animation.heights,
        });
        let l = &self.layout;
        self.surface.setFrame(rect(l.surface));
        self.cancel_button.setHidden(!l.cancel_visible);
        self.finish_button.setHidden(!l.finish_visible);
        self.recording_label.setHidden(!l.recording_label_visible);
        self.thinking_label.setHidden(!l.thinking_label_visible);
        self.progress_track.setHidden(!l.progress_visible);
        self.streaming_scroll.setHidden(!l.streaming_visible);
        for (i, bar) in self.waveform.iter().enumerate() {
            bar.setHidden(!l.waveform_visible || i >= l.bars.len());
        }
        self.cancel_button.setFrame(rect(l.cancel));
        self.finish_button.setFrame(rect(l.finish));
        self.recording_label.setFrame(rect(l.recording_label));
        for (i, frame) in l.bars.iter().enumerate() {
            self.waveform[i].setFrame(cgrect(*frame));
        }
        self.thinking_label.setFrame(rect(l.thinking_label));
        self.progress_track.setFrame(rect(l.progress_track));
        self.progress_fill.setFrame(rect(l.progress_fill));
        self.streaming_scroll.setFrame(rect(l.streaming));
        self.pin_text_document_width();
        self.preserve_horizontal_scroll(previous_scroll_x);
    }
    fn pin_text_document_width(&self) {
        // The shipping Python executable is linked with macOS SDK 15.5, whose
        // NSTextView keeps this initial document width. SDK 27 auto-shrinks it
        // to the clip width instead, changing wrapping and scrollRangeToVisible.
        // Make that established native presentation explicit across SDKs.
        let minimum = self.streaming_label.minSize();
        self.streaming_label
            .setMinSize(NSSize::new(336.0, minimum.height));
        let frame = self.streaming_label.frame();
        if frame.size.width != 336.0 {
            self.streaming_label
                .setFrameSize(NSSize::new(336.0, frame.size.height));
        }
    }
    fn preserve_horizontal_scroll(&self, previous: f64) {
        // Older AppKit retains the horizontal offset while scrolling a
        // non-editable document to its latest UTF-16 range. New SDK behavior
        // recenters short final lines. Retain the established bounded offset.
        let clip = self.streaming_scroll.contentView();
        let bounds = clip.bounds();
        let x = previous
            .max(bounds.origin.x)
            .min((336.0 - bounds.size.width).max(0.0));
        if x != bounds.origin.x {
            clip.scrollToPoint(NSPoint::new(x, bounds.origin.y));
            self.streaming_scroll.reflectScrolledClipView(&clip);
        }
    }
    fn preferred_height(&self, text: &str, width: f64) -> f64 {
        let visible = NSString::from_str(&visible_text(text));
        let font = self.streaming_label.font().expect("streaming font");
        // SAFETY: The attributes use the system font under its documented key.
        let attributes =
            NSDictionary::from_slices(&[unsafe { NSFontAttributeName }], &[&*font as &AnyObject]);
        let attributed = unsafe { NSAttributedString::new_with_attributes(&visible, &attributes) };
        let bounds = attributed.boundingRectWithSize_options(
            NSSize::new((width - 64.0).max(1.0), 100000.0),
            NSStringDrawingOptions::UsesLineFragmentOrigin
                | NSStringDrawingOptions::UsesFontLeading,
        );
        let manager = unsafe { self.streaming_label.layoutManager() }.expect("layout manager");
        preferred_height(bounds.size.height, manager.defaultLineHeightForFont(&font))
    }
    fn set_text(&mut self, text: &str) {
        if self.streaming_text == text {
            return;
        }
        let previous_scroll_x = self.streaming_scroll.contentView().bounds().origin.x;
        let was_expanded = self.is_expanded();
        self.streaming_text = text.into();
        let visible = visible_text(text);
        self.streaming_label
            .setString(&NSString::from_str(&visible));
        if was_expanded != self.is_expanded() {
            self.relayout();
        }
        self.pin_text_document_width();
        self.streaming_label
            .scrollRangeToVisible(NSRange::new(visible.encode_utf16().count(), 0));
        self.preserve_horizontal_scroll(previous_scroll_x);
    }
    fn set_container(&mut self, width: f64, height: f64, expanded: bool) {
        if self.width != width || self.height != height {
            self.width = width;
            self.height = height;
            self.view
                .setFrame(rect(Frame::new(0.0, 0.0, width, height)));
            self.relayout();
        }
        if self.expanded != expanded {
            self.expanded = expanded;
            self.relayout();
        }
    }
    fn waveform_tick(&mut self, level: f64, elapsed: f64, scale: f64) {
        let _transaction = NoLayerActions::begin();
        self.animation.waveform(
            level,
            elapsed,
            self.layout.bars.len(),
            scale,
            self.reduce_motion,
        );
        let row_center = if self.is_expanded() {
            self.layout.surface.height - 18.0
        } else {
            18.0
        };
        for (i, bar) in self
            .waveform
            .iter()
            .take(self.layout.bars.len())
            .enumerate()
        {
            let height = self.animation.heights[i];
            let f = bar.frame();
            if f.size.height != height {
                bar.setFrame(cgrect(Frame::new(
                    f.origin.x,
                    row_center - height / 2.0,
                    2.0,
                    height,
                )));
            }
            self.layout.bars[i].height = height;
            self.layout.bars[i].y = row_center - height / 2.0;
        }
    }
    fn progress_tick(&mut self, elapsed: f64) -> bool {
        let _transaction = NoLayerActions::begin();
        let active = self.animation.advance_progress(elapsed);
        self.layout.progress_fill.width = 48.0 * self.animation.progress;
        self.progress_fill.setFrame(rect(self.layout.progress_fill));
        active
    }
}

/// All AppKit objects and mutable presentation state are confined to the main
/// thread. The host supplies one common-run-loop 60 Hz tick while visible.
pub struct Capsule {
    mtm: MainThreadMarker,
    panel: Retained<CapsulePanel>,
    renderer: Renderer,
    state: State,
    connection_notice: Option<Value>,
    failure_deadline: Option<Instant>,
    hide_deadline: Option<Instant>,
    latest_transcript: String,
    prompt_hint: String,
    latest_audio_level: f64,
    last_animation_tick: Instant,
    progress_active: bool,
    pending_cancel: Rc<Cell<bool>>,
}

impl Capsule {
    pub fn new(mtm: MainThreadMarker, commands: CommandSink) -> Result<Self> {
        let frame = NSScreen::mainScreen(mtm)
            .map(|s| s.frame())
            .unwrap_or_else(|| NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(1440.0, 900.0)));
        let content = NSRect::new(
            NSPoint::new(
                frame.origin.x + (frame.size.width - COMPACT_WIDTH) / 2.0,
                frame.origin.y + 20.0,
            ),
            NSSize::new(COMPACT_WIDTH, COMPACT_HEIGHT),
        );
        let allocated = CapsulePanel::alloc(mtm).set_ivars(());
        // SAFETY: Matches NSWindow's designated initializer. Rust retains the
        // window, so AppKit's releasedWhenClosed ownership is disabled below.
        let panel: Retained<CapsulePanel> = unsafe {
            msg_send![super(allocated), initWithContentRect: content, styleMask: NSWindowStyleMask::Borderless | NSWindowStyleMask::NonactivatingPanel, backing: NSBackingStoreType::Buffered, defer: false]
        };
        unsafe {
            panel.setReleasedWhenClosed(false);
        }
        panel.setLevel(1000);
        panel.setBackgroundColor(Some(&NSColor::clearColor()));
        panel.setOpaque(false);
        panel.setHasShadow(false);
        panel.setIgnoresMouseEvents(true);
        panel.setCollectionBehavior(
            NSWindowCollectionBehavior::CanJoinAllSpaces
                | NSWindowCollectionBehavior::FullScreenAuxiliary
                | NSWindowCollectionBehavior::Stationary,
        );
        panel.setHidesOnDeactivate(false);
        let pending_cancel = Rc::new(Cell::new(false));
        let renderer = Renderer::new(mtm, commands, pending_cancel.clone());
        panel.setContentView(Some(&renderer.view));
        Ok(Self {
            mtm,
            panel,
            renderer,
            state: State::Hidden,
            connection_notice: None,
            failure_deadline: None,
            hide_deadline: None,
            latest_transcript: String::new(),
            prompt_hint: String::new(),
            latest_audio_level: 0.0,
            last_animation_tick: Instant::now(),
            progress_active: false,
            pending_cancel,
        })
    }
    pub fn show(&mut self, mode: &str, prompt_mode: bool, hint: &str) {
        self.connection_notice = None;
        self.failure_deadline = None;
        self.hide_deadline = None;
        self.pending_cancel.set(false);
        self.latest_transcript.clear();
        self.prompt_hint = hint.into();
        self.state = State::Recording;
        self.renderer.mode = display_mode(mode, prompt_mode);
        self.resize(false, "");
        self.panel
            .setIgnoresMouseEvents(is_push_to_talk(&self.renderer.mode));
        let mouse = NSEvent::mouseLocation();
        let active = NSScreen::screens(self.mtm)
            .iter()
            .find(|screen| {
                let f = screen.frame();
                mouse.x >= f.origin.x
                    && mouse.x < f.origin.x + f.size.width
                    && mouse.y >= f.origin.y
                    && mouse.y < f.origin.y + f.size.height
            })
            .or_else(|| NSScreen::mainScreen(self.mtm));
        if let Some(screen) = active {
            let f = screen.frame();
            self.panel.setFrameOrigin(NSPoint::new(
                f.origin.x + (f.size.width - COMPACT_WIDTH) / 2.0,
                f.origin.y + 20.0,
            ));
        }
        self.renderer.update_labels();
        self.renderer.relayout();
        self.renderer.set_state(State::Recording);
        if is_prompt(&self.renderer.mode) {
            let initial = if hint.is_empty() {
                local_prompt_hint("", &self.renderer.language)
            } else {
                hint.into()
            };
            self.update_prompt_hint(&initial);
        }
        self.panel.orderFront(None);
        self.latest_audio_level = 0.0;
        self.progress_active = false;
        self.last_animation_tick = Instant::now();
    }
    pub fn hide(&mut self) {
        if self.failure_deadline.is_some() {
            return;
        }
        self.connection_notice = None;
        self.state = State::Hidden;
        self.renderer.set_state(State::Hidden);
        self.panel.setIgnoresMouseEvents(true);
        self.resize(false, "");
        self.hide_deadline = Some(Instant::now() + std::time::Duration::from_secs_f64(HIDE_DELAY));
    }
    /// Explicit host teardown must dismiss a terminal notice immediately even
    /// when ordinary idle/hide events preserve it for the reading deadline.
    pub fn close(&mut self) {
        self.failure_deadline = None;
        self.hide_deadline = None;
        self.connection_notice = None;
        self.progress_active = false;
        self.latest_audio_level = 0.0;
        self.pending_cancel.set(false);
        self.state = State::Hidden;
        self.renderer.set_state(State::Hidden);
        self.panel.setIgnoresMouseEvents(true);
        self.panel.orderOut(None);
    }
    pub fn update_state(&mut self, state: &str) {
        let next = match state {
            "recording" => State::Recording,
            "processing" => State::Processing,
            "hidden" => State::Hidden,
            _ => return,
        };
        if self.failure_deadline.is_some() || self.connection_notice.is_some() {
            if next != State::Hidden {
                self.state = next;
            }
            return;
        }
        if next == State::Hidden {
            self.hide();
            return;
        }
        if self.state == State::Hidden || self.state == next {
            return;
        }
        self.state = next;
        self.renderer.set_state(next);
        self.last_animation_tick = Instant::now();
        if next == State::Processing {
            self.resize(false, "");
            self.progress_active = true;
            self.panel.setIgnoresMouseEvents(true);
        }
    }
    pub fn set_language(&mut self, language: &str) {
        self.renderer.language = if language.to_lowercase().starts_with("zh") {
            "zh"
        } else {
            "en"
        }
        .into();
        self.renderer.update_labels();
        self.renderer.relayout();
        if let Some(notice) = self.connection_notice.clone() {
            self.present_connection(&notice);
        } else if self.state == State::Recording && is_prompt(&self.renderer.mode) {
            let hint = local_prompt_hint(&self.latest_transcript, &self.renderer.language);
            self.update_prompt_hint(&hint);
        }
    }
    pub fn set_processing_stage(&mut self, stage: &str) {
        let stage = if stage.is_empty() {
            "transcribing"
        } else {
            stage
        };
        if self.renderer.stage != stage {
            self.renderer.stage = stage.into();
            self.renderer.update_labels();
            self.renderer.relayout();
        }
    }
    pub fn update_audio_level(&mut self, level: f64) {
        self.latest_audio_level = if level.is_finite() {
            level.clamp(0.0, 1.0)
        } else {
            0.0
        };
    }
    pub fn update_prompt_hint(&mut self, hint: &str) {
        self.prompt_hint = hint.into();
        if self.state == State::Recording
            && is_prompt(&self.renderer.mode)
            && self.connection_notice.is_none()
            && self.failure_deadline.is_none()
        {
            self.resize(!hint.trim().is_empty(), hint);
            self.renderer.set_text(hint);
        }
    }
    pub fn update_streaming_text(&mut self, text: &str) {
        self.latest_transcript = text.into();
        if self.connection_notice.is_some()
            || self.failure_deadline.is_some()
            || self.state == State::Hidden
        {
            return;
        }
        if self.state == State::Recording && is_prompt(&self.renderer.mode) {
            let hint = local_prompt_hint(text, &self.renderer.language);
            self.update_prompt_hint(&hint);
            return;
        }
        self.resize(!text.trim().is_empty(), text);
        self.renderer.set_text(text);
    }
    pub fn show_failure(&mut self, message: &str) {
        if self.connection_notice.is_some() {
            return;
        }
        self.hide_deadline = None;
        self.progress_active = false;
        self.failure_deadline =
            Some(Instant::now() + std::time::Duration::from_secs_f64(FAILURE_DURATION));
        self.state = State::ConnectionError;
        self.renderer.connection_title = translation(&self.renderer.language, "failure").into();
        self.resize(!message.trim().is_empty(), message);
        self.renderer.set_state(State::ConnectionError);
        self.renderer.update_labels();
        self.renderer.set_text(message);
        self.renderer.relayout();
        self.panel.setIgnoresMouseEvents(true);
        self.panel.orderFront(None);
    }
    pub fn show_connection(&mut self, status: &Value) {
        if self.failure_deadline.is_some() || self.state == State::Hidden {
            return;
        }
        let status = status
            .get("status")
            .filter(|v| v.is_object())
            .unwrap_or(status);
        let phase = status["phase"].as_str().unwrap_or("");
        if phase == "connecting" && status["retry"].as_u64().unwrap_or(0) == 0 {
            return;
        }
        if phase == "ready" {
            if self.connection_notice.take().is_none() {
                return;
            }
            self.renderer.set_state(self.state);
            self.resize(false, "");
            if self.state == State::Recording && is_prompt(&self.renderer.mode) {
                let hint = self.prompt_hint.clone();
                self.update_prompt_hint(&hint);
            } else {
                let text = self.latest_transcript.clone();
                self.update_streaming_text(&text);
            }
            self.panel.setIgnoresMouseEvents(
                self.state != State::Recording || is_push_to_talk(&self.renderer.mode),
            );
            self.progress_active = self.state == State::Processing;
            self.latest_audio_level = 0.0;
            self.last_animation_tick = Instant::now();
            return;
        }
        self.connection_notice = Some(status.clone());
        self.progress_active = false;
        self.present_connection(status);
    }
    fn present_connection(&mut self, status: &Value) {
        let (title, detail) = connection_text(status, &self.renderer.language);
        self.renderer.connection_title = title;
        self.resize(true, &detail);
        self.renderer.set_state(State::ConnectionError);
        self.renderer.update_labels();
        self.renderer.set_text(&detail);
        self.renderer.relayout();
        self.panel.setIgnoresMouseEvents(false);
    }
    fn resize(&mut self, expanded: bool, text: &str) {
        let width = if !expanded {
            COMPACT_WIDTH
        } else if self.state == State::Recording
            && self.connection_notice.is_none()
            && self.failure_deadline.is_none()
        {
            RECORDING_WIDTH
        } else {
            NOTICE_WIDTH
        };
        let height = if expanded {
            self.renderer.preferred_height(text, width)
        } else {
            COMPACT_HEIGHT
        };
        let frame = self.panel.frame();
        let center = frame.origin.x + frame.size.width / 2.0;
        if frame.size.width != width || frame.size.height != height {
            self.panel.setFrame_display(
                NSRect::new(
                    NSPoint::new(center - width / 2.0, frame.origin.y),
                    NSSize::new(width, height),
                ),
                true,
            );
        }
        self.renderer.set_container(width, height, expanded);
    }
    /// Call from the host's common-run-loop timer at 60 Hz. Idle/hidden calls do
    /// no rendering work; deadlines share this timer and never spawn threads.
    pub fn tick(&mut self) {
        let now = Instant::now();
        if self.pending_cancel.replace(false) {
            self.hide();
        }
        if self
            .failure_deadline
            .is_some_and(|deadline| now >= deadline)
        {
            self.failure_deadline = None;
            self.hide();
        }
        if self.hide_deadline.is_some_and(|deadline| now >= deadline) {
            self.panel.orderOut(None);
            self.hide_deadline = None;
        }
        if self.connection_notice.is_some() || self.failure_deadline.is_some() {
            return;
        }
        let elapsed = now.duration_since(self.last_animation_tick).as_secs_f64();
        self.last_animation_tick = now;
        if self.state == State::Recording {
            self.renderer.waveform_tick(
                self.latest_audio_level,
                elapsed,
                self.panel.backingScaleFactor(),
            );
        } else if self.state == State::Processing && self.progress_active {
            self.progress_active = self.renderer.progress_tick(elapsed);
        }
    }
    pub fn needs_tick(&self) -> bool {
        self.state == State::Recording
            || self.progress_active
            || self.connection_notice.is_some()
            || self.hide_deadline.is_some()
            || self.failure_deadline.is_some()
            || self.pending_cancel.get()
    }
    pub fn is_visible(&self) -> bool {
        self.panel.isVisible()
    }
    pub fn is_failure(&self) -> bool {
        self.failure_deadline.is_some()
            || self.connection_notice.as_ref().is_some_and(|v| {
                matches!(v["phase"].as_str(), Some("failed" | "microphone_failed"))
            })
    }
    pub fn current_state(&self) -> &'static str {
        if self.failure_deadline.is_some() {
            "failure"
        } else if self.connection_notice.is_some() {
            "connection_error"
        } else {
            match self.state {
                State::Hidden => "hidden",
                State::Recording => "recording",
                State::Processing => "processing",
                State::ConnectionError => "connection_error",
            }
        }
    }
    /// Export actual AppKit pixels (including its system font rasterization).
    pub fn save_snapshot(&self, path: &Path) -> Result<()> {
        let bounds = self.renderer.view.bounds();
        let bitmap = self
            .renderer
            .view
            .bitmapImageRepForCachingDisplayInRect(bounds)
            .context("capsule bitmap allocation failed")?;
        self.renderer
            .view
            .cacheDisplayInRect_toBitmapImageRep(bounds, &bitmap);
        let properties = NSDictionary::new();
        // SAFETY: Empty properties use AppKit's default PNG encoder settings.
        let data = unsafe {
            bitmap.representationUsingType_properties(NSBitmapImageFileType::PNG, &properties)
        }
        .context("capsule PNG encoding failed")?;
        std::fs::write(path, data.to_vec()).context("write capsule snapshot")
    }
    pub fn snapshot_metadata(&self) -> Value {
        let frame = self.panel.frame();
        let clip = self.renderer.streaming_scroll.contentView().bounds();
        let document = self.renderer.streaming_label.frame();
        json!({ "panel": Frame::new(frame.origin.x, frame.origin.y, frame.size.width, frame.size.height), "state": self.renderer.state, "mode": self.renderer.mode, "language": self.renderer.language, "stage": self.renderer.stage, "surface_alpha": self.renderer.surface.alphaValue(), "ignores_mouse_events": self.panel.ignoresMouseEvents(), "can_become_key": self.panel.canBecomeKeyWindow(), "can_become_main": self.panel.canBecomeMainWindow(), "recording_label": self.renderer.recording_label.stringValue().to_string(), "thinking_label": self.renderer.thinking_label.stringValue().to_string(), "visible_text": self.renderer.streaming_label.string().to_string(), "scroll_x": clip.origin.x, "scroll_y": clip.origin.y, "scroll_width": clip.size.width, "scroll_height": clip.size.height, "document_width": document.size.width, "document_height": document.size.height, "progress": self.renderer.animation.progress, "layout": self.renderer.layout })
    }
    /// Deterministic fixture frames. Normal operation uses tick's monotonic
    /// elapsed time; fixtures can compare equal inputs at an exact timestamp.
    pub fn advance_fixture(&mut self, level: f64, elapsed: f64, reduce_motion: bool) {
        self.renderer.reduce_motion = reduce_motion;
        if self.renderer.state == State::Recording {
            self.renderer
                .waveform_tick(level, elapsed, self.panel.backingScaleFactor());
        } else if self.renderer.state == State::Processing {
            self.renderer.progress_tick(elapsed);
        }
    }
    /// Only used by parity tooling to reproduce the original 24 renderer cases.
    pub fn set_fixture_container(&mut self, width: f64, height: f64, expanded: bool) {
        let frame = self.panel.frame();
        self.panel
            .setFrame_display(NSRect::new(frame.origin, NSSize::new(width, height)), true);
        self.renderer.set_container(width, height, expanded);
    }
    /// Exercise the actual AppKit renderer and export a reproducible fixture
    /// set. No microphone, ASR, paste or model access is requested.
    pub fn export_parity_fixtures(&mut self, directory: &Path) -> Result<Value> {
        std::fs::create_dir_all(directory)?;
        let mut metadata = serde_json::Map::new();
        for language in ["zh", "en"] {
            self.set_language(language);
            for mode in [
                "pushToTalk",
                "handsFree",
                "prompt",
                "promptPushToTalk",
                "command",
                "meeting",
            ] {
                self.show(mode, false, "");
                // Renderer-level fixtures intentionally omit the local coach.
                self.renderer.set_text("");
                for expanded in [false, true] {
                    self.set_fixture_container(
                        if expanded { 360.0 } else { 240.0 },
                        if expanded { 200.0 } else { 80.0 },
                        expanded,
                    );
                    self.renderer.set_text(if expanded {
                        "说明文字 / Explain the task"
                    } else {
                        ""
                    });
                    self.renderer.animation.phase = 0.0;
                    self.renderer.animation.reset_waveform();
                    for _ in 0..20 {
                        self.advance_fixture(0.7, 1.0 / 120.0, false);
                    }
                    self.validate_recording_geometry()?;
                    let name = format!(
                        "{language}-{mode}-{}",
                        if expanded { "expanded" } else { "compact" }
                    );
                    self.capture_fixture(directory, &name, &mut metadata)?;
                }
                self.hide();
                anyhow::ensure!(
                    self.renderer.surface.alphaValue() == 0.0,
                    "hidden surface still visible"
                );
            }
        }
        self.set_language("zh");
        self.show("handsFree", false, "");
        let samples = [
            ("one-line", "再者呢，你看。".to_owned()),
            ("two-lines", "第一行短内容\n第二行短内容".to_owned()),
            ("wrapped", "你看一下现在这个波形，它的长度还是不够长，跟这个窗口的长度不成正比。再者呢，只有几个字的时候就展开了全部窗口。希望窗口可以随着文字增加，逐行展开，并始终保留清楚的波形。".to_owned()),
            ("overflow", format!("{}LATEST END", "长文本😀 English\n".repeat(100))),
        ];
        let mut heights = Vec::new();
        for (name, text) in &samples {
            self.update_streaming_text(text);
            heights.push(self.panel.frame().size.height);
            let last_bar = self.renderer.layout.bars.last().unwrap();
            let span = last_bar.x + last_bar.width - self.renderer.layout.bars[0].x;
            anyhow::ensure!(
                span >= self.renderer.layout.surface.width * 0.70,
                "expanded waveform too short"
            );
            if *name != "overflow" {
                let manager = unsafe { self.renderer.streaming_label.layoutManager() }
                    .context("text layout manager")?;
                let container = unsafe { self.renderer.streaming_label.textContainer() }
                    .context("text container")?;
                manager.ensureLayoutForTextContainer(&container);
                let used = manager.usedRectForTextContainer(&container).size.height;
                anyhow::ensure!(
                    used <= self.renderer.streaming_scroll.contentSize().height + 1.0,
                    "wrapped transcript clipped"
                );
            }
            self.renderer.animation.phase = 0.0;
            self.renderer.animation.reset_waveform();
            for _ in 0..20 {
                self.advance_fixture(0.7, 1.0 / 120.0, false);
            }
            self.capture_fixture(directory, name, &mut metadata)?;
        }
        anyhow::ensure!(
            heights[0] < heights[1]
                && heights[1] < heights[2]
                && heights[2] <= heights[3]
                && heights[3] == 200.0,
            "transcript height progression differs from original"
        );
        let base_y = self.panel.frame().origin.y;
        self.update_streaming_text(&samples[0].1);
        anyhow::ensure!(
            self.panel.frame().size.height == heights[0] && self.panel.frame().origin.y == base_y,
            "correction did not shrink in place"
        );
        self.update_streaming_text("");
        anyhow::ensure!(
            self.panel.frame().size.height == 80.0,
            "empty transcript not compact"
        );
        for mode in ["handsFree", "pushToTalk", "prompt", "promptPushToTalk"] {
            self.show(mode, false, "");
            let retry = json!({"phase":"retrying","error":"连接超时：无法连接 dashscope.aliyuncs.com","retry":3,"delay":4});
            self.show_connection(&retry);
            anyhow::ensure!(
                !self.panel.ignoresMouseEvents()
                    && !self.renderer.cancel_button.isHidden()
                    && self.renderer.finish_button.isHidden(),
                "connection controls differ"
            );
            self.capture_fixture(
                directory,
                &format!("connection-retry-{mode}"),
                &mut metadata,
            )?;
            self.update_state("processing");
            anyhow::ensure!(
                self.renderer.state == State::ConnectionError,
                "notice lost on processing"
            );
            self.show_connection(&json!({"phase":"ready"}));
            anyhow::ensure!(
                self.renderer.state == State::Processing,
                "ready did not restore processing"
            );
            self.show_connection(
                &json!({"phase":"failed","error":"403：没有该模型的访问权限","retry":5}),
            );
            self.update_state("hidden");
            self.show_failure("generic failure");
            anyhow::ensure!(
                self.connection_notice.is_some() && self.failure_deadline.is_none(),
                "terminal connection notice replaced"
            );
            self.capture_fixture(
                directory,
                &format!("connection-failed-{mode}"),
                &mut metadata,
            )?;
            self.hide();
        }
        self.show("handsFree", false, "");
        self.update_state("processing");
        self.show_failure("识别服务返回 403");
        self.update_state("hidden");
        self.show_connection(&json!({"phase":"retrying","retry":3,"delay":4}));
        anyhow::ensure!(
            self.failure_deadline.is_some()
                && self.renderer.thinking_label.stringValue().to_string() == "听写失败",
            "failure not preserved through late idle/connection"
        );
        self.capture_fixture(directory, "failure-notice", &mut metadata)?;
        self.failure_deadline = None;
        self.hide();
        self.show("handsFree", false, "");
        self.update_state("processing");
        self.set_fixture_container(400.0, 200.0, true);
        for stage in [
            "transcribing",
            "polishing",
            "understanding",
            "searching",
            "generating",
            "meeting_transcribing",
            "meeting_summarizing",
        ] {
            self.set_processing_stage(stage);
            anyhow::ensure!(
                self.renderer.thinking_label.intrinsicContentSize().width
                    <= self.renderer.layout.thinking_label.width,
                "processing label clipped"
            );
        }
        self.renderer
            .set_text(&format!("{}LATEST END", "长文本😀 English\n".repeat(600)));
        let clip = self.renderer.streaming_scroll.contentView().bounds();
        let document = self.renderer.streaming_label.frame();
        anyhow::ensure!(
            clip.origin.y > 0.0
                && (clip.origin.y + clip.size.height - document.size.height).abs() < 2.0,
            "long transcript did not scroll to latest output"
        );
        self.capture_fixture(directory, "long-streaming", &mut metadata)?;
        for _ in 0..500 {
            self.advance_fixture(0.0, 1.0 / 120.0, false);
        }
        anyhow::ensure!(
            self.renderer.animation.progress > 0.89 && self.renderer.animation.progress <= 0.9,
            "progress exceeded original asymptote"
        );
        for language in ["zh", "en"] {
            self.set_language(language);
            for stage in [
                "transcribing",
                "polishing",
                "understanding",
                "searching",
                "generating",
                "meeting_transcribing",
                "meeting_summarizing",
            ] {
                self.show("handsFree", false, "");
                self.update_state("processing");
                self.set_processing_stage(stage);
                for _ in 0..20 {
                    self.advance_fixture(0.0, 1.0 / 120.0, false);
                }
                self.capture_fixture(
                    directory,
                    &format!("processing-{language}-{stage}"),
                    &mut metadata,
                )?;
            }
            self.renderer.animation.progress = 0.0;
            for _ in 0..500 {
                self.advance_fixture(0.0, 1.0 / 120.0, false);
            }
            self.capture_fixture(
                directory,
                &format!("progress-asymptote-{language}"),
                &mut metadata,
            )?;
            self.show("handsFree", false, "");
            self.update_streaming_text("说明文字 / Explain the task");
            self.set_fixture_container(360.0, 200.0, true);
            self.renderer.animation.phase = 0.0;
            for _ in 0..20 {
                self.advance_fixture(0.7, 1.0 / 120.0, true);
            }
            self.capture_fixture(
                directory,
                &format!("reduce-motion-{language}"),
                &mut metadata,
            )?;
            for mode in ["pushToTalk", "handsFree"] {
                self.hide();
                self.show(mode, true, "");
                self.capture_fixture(
                    directory,
                    &format!("prompt-coach-{language}-{mode}"),
                    &mut metadata,
                )?;
            }
        }
        self.hide();
        self.show("handsFree", false, "");
        let front_before = NSWorkspace::sharedWorkspace()
            .frontmostApplication()
            .map(|app| app.processIdentifier());
        let cancel_before = self.renderer._action_target.ivars().cancel_count.get();
        let finish_before = self.renderer._action_target.ivars().finish_count.get();
        // SAFETY: Native buttons retain a valid action target with selectors
        // matching the control sender ABI. These intents never start recording.
        unsafe {
            self.renderer.cancel_button.performClick(None);
            self.renderer.finish_button.performClick(None);
        }
        anyhow::ensure!(
            self.renderer._action_target.ivars().cancel_count.get() == cancel_before + 1
                && self.renderer._action_target.ivars().finish_count.get() == finish_before + 1,
            "native button actions did not dispatch"
        );
        anyhow::ensure!(
            !self.panel.canBecomeKeyWindow() && !self.panel.canBecomeMainWindow(),
            "capsule can steal key/main window"
        );
        anyhow::ensure!(
            NSWorkspace::sharedWorkspace()
                .frontmostApplication()
                .map(|app| app.processIdentifier())
                == front_before,
            "native capsule action changed foreground application"
        );
        self.tick();
        anyhow::ensure!(
            self.current_state() == "hidden",
            "cancel did not dismiss capsule"
        );
        self.hide();
        self.show_failure("Shutdown teardown fixture");
        anyhow::ensure!(
            self.panel.isVisible() && self.is_failure(),
            "terminal notice did not appear"
        );
        self.pending_cancel.set(true);
        self.close();
        anyhow::ensure!(
            !self.panel.isVisible() && !self.needs_tick() && self.current_state() == "hidden",
            "host teardown left a failure panel or timer active"
        );
        let output = Value::Object(metadata);
        std::fs::write(
            directory.join("fixtures.json"),
            serde_json::to_vec_pretty(&output)?,
        )?;
        Ok(
            json!({"checks":"passed", "layout_cases":24, "snapshot_cases":output.as_object().unwrap().len(), "directory":directory}),
        )
    }
    fn validate_recording_geometry(&self) -> Result<()> {
        let l = &self.renderer.layout;
        anyhow::ensure!(
            l.surface.x >= 0.0 && l.surface.x + l.surface.width <= self.renderer.width,
            "capsule outside container"
        );
        if l.recording_label_visible {
            anyhow::ensure!(
                l.recording_label.width
                    >= self.renderer.recording_label.intrinsicContentSize().width,
                "recording label clipped"
            );
        }
        if l.recording_label_visible && l.cancel_visible {
            anyhow::ensure!(
                l.recording_label.x >= l.cancel.x + l.cancel.width + 4.0,
                "recording label touches cancel"
            );
        }
        for bar in &l.bars {
            anyhow::ensure!(
                bar.x >= 0.0 && bar.x + bar.width <= l.surface.width,
                "waveform outside surface"
            );
            if l.finish_visible {
                anyhow::ensure!(
                    bar.x + bar.width <= l.finish.x - 4.0,
                    "waveform touches finish"
                );
            }
        }
        Ok(())
    }
    fn capture_fixture(
        &self,
        directory: &Path,
        name: &str,
        metadata: &mut serde_json::Map<String, Value>,
    ) -> Result<()> {
        self.panel.orderFront(None);
        self.panel.setFrameOrigin(NSPoint::new(50.0, 50.0));
        self.panel.display();
        // AppKit commits backing layers at a run-loop boundary. This bounded
        // fixture-only pump matches the old native checker and owns no timer.
        objc2_foundation::NSRunLoop::mainRunLoop()
            .runUntilDate(&objc2_foundation::NSDate::dateWithTimeIntervalSinceNow(0.1));
        self.save_snapshot(&directory.join(format!("{name}.png")))?;
        metadata.insert(name.into(), self.snapshot_metadata());
        Ok(())
    }
}

impl Drop for Capsule {
    fn drop(&mut self) {
        self.panel.orderOut(None);
        self.panel.setContentView(None);
    }
}

fn connection_text(status: &Value, language: &str) -> (String, String) {
    if let (Some(title), Some(detail)) = (status["title"].as_str(), status["detail"].as_str()) {
        return (title.into(), detail.into());
    }
    let zh = language == "zh";
    let phase = status["phase"].as_str().unwrap_or("");
    let retry = status["retry"].as_u64().unwrap_or(0);
    let max = status["max_retries"].as_u64().unwrap_or(5);
    let error = status["error"].as_str().unwrap_or("");
    let (title, hint) = if phase == "microphone_failed" {
        match status["recovery"]["busy"].as_bool().or_else(|| status["busy"].as_bool()) {
            Some(true) => (if zh { "麦克风正在恢复" } else { "Microphone recovery pending" }.into(), if zh { "上次音频任务尚未结束，结束后可再次按听写键。若持续无响应，请重启应用。点击 × 关闭。" } else { "Waiting for the previous audio task to finish before retrying. If it stays unresponsive, restart the app. Click × to close." }.into()),
            Some(false) => (if zh { "麦克风可以重试" } else { "Microphone retry available" }.into(), if zh { "上次音频任务已结束，请再次按听写键重试。点击 × 关闭。" } else { "The previous audio task has finished. Press your dictation key to retry. Click × to close." }.into()),
            None => (if zh { "麦克风启动失败" } else { "Microphone startup failed" }.into(), if zh { "处理后请再次按听写键，点击 × 关闭。" } else { "Press your dictation key again when ready. Click × to close." }.into()),
        }
    } else if phase == "failed" {
        (
            if zh {
                "连接失败"
            } else {
                "Connection failed"
            }
            .into(),
            if zh {
                format!("已重试 {retry} 次，点击 × 关闭。")
            } else {
                format!("Stopped after {retry} retries. Click × to close.")
            },
        )
    } else if phase == "retrying" {
        let delay = status["delay"].as_f64().unwrap_or(0.0);
        (
            if zh {
                "连接失败，等待重试"
            } else {
                "Connection failed · retrying"
            }
            .into(),
            if zh {
                format!("{delay} 秒后重试（{retry}/{max}），点击 × 取消。")
            } else {
                format!("Retry {retry}/{max} in {delay}s. Click × to cancel.")
            },
        )
    } else if retry == 0 {
        (
            if zh { "正在连接" } else { "Connecting" }.into(),
            if zh {
                "正在建立听写连接，点击 × 取消。"
            } else {
                "Connecting to dictation. Click × to cancel."
            }
            .into(),
        )
    } else {
        (
            if zh {
                format!("正在重连（{retry}/{max}）")
            } else {
                format!("Reconnecting ({retry}/{max})")
            },
            if zh {
                "点击 × 取消。"
            } else {
                "Click × to cancel."
            }
            .into(),
        )
    };
    (title, format!("{error}\n{hint}").trim().into())
}

fn local_prompt_hint(text: &str, language: &str) -> String {
    // Reuse the exact contract-backed Rust business implementation. This also
    // makes the initial frame and a live interface-language change synchronous.
    vocal_more_backend::coach::assess(text, language)["hint"]
        .as_str()
        .unwrap_or("")
        .into()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn notices_include_retry_and_localized_recovery_without_network() {
        let (title, detail) = connection_text(
            &json!({"phase":"retrying","error":"timeout","retry":3,"delay":4}),
            "zh",
        );
        assert_eq!(title, "连接失败，等待重试");
        assert_eq!(detail, "timeout\n4 秒后重试（3/5），点击 × 取消。");
        let (title, _) = connection_text(
            &json!({"phase":"microphone_failed","recovery":{"busy":false}}),
            "en",
        );
        assert_eq!(title, "Microphone retry available");
    }
}
