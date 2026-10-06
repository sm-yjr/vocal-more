// SPDX-License-Identifier: GPL-3.0-only
use super::{class, ns};
use objc2::{
    DefinedClass, MainThreadMarker, MainThreadOnly, define_class, msg_send,
    rc::Retained,
    runtime::{AnyClass, AnyObject},
};
use objc2_foundation::{NSObject, NSObjectProtocol, NSString};
use serde_json::Value;
use std::cell::RefCell;

const STABLE: &str =
    "https://github.com/sm-yjr/vocal-more/releases/download/sparkle-feed/appcast.xml";
const NIGHTLY: &str =
    "https://github.com/sm-yjr/vocal-more/releases/download/sparkle-feed-alpha/appcast.xml";
struct DelegateIvars {
    feed: RefCell<Option<String>>,
}
define_class!(
    #[unsafe(super=NSObject)]
    #[name="VMRustSparkleDelegate"]
    #[thread_kind=MainThreadOnly]
    #[ivars=DelegateIvars]
    struct Delegate;
    unsafe impl NSObjectProtocol for Delegate {}
    impl Delegate {
        #[unsafe(method_id(feedURLStringForUpdater:))]
        fn feed(&self,_updater:&AnyObject)->Option<Retained<NSString>> {self.ivars().feed.borrow().as_deref().map(ns)}
    }
);
impl Delegate {
    fn new(mtm: MainThreadMarker, configured: Option<&str>) -> Retained<Self> {
        let this = Self::alloc(mtm).set_ivars(DelegateIvars {
            feed: RefCell::new(feed_override(configured).map(str::to_owned)),
        });
        unsafe { msg_send![super(this), init] }
    }
}
pub struct Updater {
    controller: Option<Retained<AnyObject>>,
    delegate: Retained<Delegate>,
    channel: String,
    error: Option<String>,
}
impl Updater {
    pub fn new(mtm: MainThreadMarker, config: &Value) -> Self {
        let configured = config["update_channel"].as_str();
        let release = unsafe {
            let bundle: Retained<AnyObject> = msg_send![class(c"NSBundle"), mainBundle];
            let channel: Option<Retained<NSString>> =
                msg_send![&*bundle,objectForInfoDictionaryKey:&*ns("VocalMoreReleaseChannel")];
            channel.map(|v| v.to_string()).unwrap_or_default()
        };
        let channel = effective_channel(configured, &release).to_owned();
        // No explicit preference means Sparkle owns the embedded SUFeedURL.
        // In particular, a beta bundle must retain its separate beta feed even
        // though its user-facing default channel label is Nightly.
        let delegate = Delegate::new(mtm, configured);
        let mut this = Self {
            controller: None,
            delegate,
            channel,
            error: None,
        };
        unsafe {
            let main: Retained<AnyObject> = msg_send![class(c"NSBundle"), mainBundle];
            let path: Option<Retained<NSString>> = msg_send![&*main, privateFrameworksPath];
            let Some(path) = path else {
                return this;
            };
            let path = format!("{}/Sparkle.framework", path);
            if !std::path::Path::new(&path).is_dir() {
                return this;
            }
            let bundle: Option<Retained<AnyObject>> =
                msg_send![class(c"NSBundle"),bundleWithPath:&*ns(&path)];
            let Some(bundle) = bundle else {
                this.error = Some("无法加载 Sparkle.framework".into());
                return this;
            };
            let ok: bool = msg_send![&*bundle, load];
            if !ok {
                this.error = Some("无法加载 Sparkle.framework".into());
                return this;
            }
            let Some(controller_class) = AnyClass::get(c"SPUStandardUpdaterController") else {
                this.error = Some("Sparkle updater class unavailable".into());
                return this;
            };
            let allocated: objc2::rc::Allocated<AnyObject> = msg_send![controller_class, alloc];
            this.controller = msg_send![allocated,initWithStartingUpdater:true,updaterDelegate:&*this.delegate,userDriverDelegate:std::ptr::null::<AnyObject>()];
            if this.controller.is_none() {
                this.error = Some("Sparkle updater could not be initialized".into());
            }
        }
        this
    }
    pub fn update(&mut self, config: &Value) {
        let Some(channel) = config["update_channel"]
            .as_str()
            .filter(|v| matches!(*v, "stable" | "nightly"))
        else {
            return;
        };
        if self.channel == channel
            && self.delegate.ivars().feed.borrow().as_deref() == Some(feed(channel))
        {
            return;
        }
        self.channel = channel.into();
        *self.delegate.ivars().feed.borrow_mut() = Some(feed(channel).into());
        if let Some(controller) = &self.controller {
            unsafe {
                let updater: Retained<AnyObject> = msg_send![&**controller, updater];
                let _: () = msg_send![&*updater, resetUpdateCycleAfterShortDelay];
            }
        }
    }
    pub fn check(&self) -> bool {
        if let Some(controller) = &self.controller {
            unsafe {
                let _: () = msg_send![&**controller,checkForUpdates:std::ptr::null::<AnyObject>()];
            }
            true
        } else {
            false
        }
    }
    pub fn status(&self) -> Value {
        serde_json::json!({"available":self.controller.is_some(),"channel":self.channel,"feed_override":*self.delegate.ivars().feed.borrow(),"startup_error":self.error})
    }
}
fn feed(channel: &str) -> &'static str {
    if channel == "nightly" {
        NIGHTLY
    } else {
        STABLE
    }
}
fn feed_override(configured: Option<&str>) -> Option<&'static str> {
    configured
        .filter(|channel| matches!(*channel, "stable" | "nightly"))
        .map(feed)
}
pub fn effective_channel(configured: Option<&str>, release: &str) -> &'static str {
    match configured {
        Some("nightly") => "nightly",
        Some("stable") => "stable",
        _ if matches!(release, "alpha" | "beta") => "nightly",
        _ => "stable",
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn explicit_channels_override_bundle_and_alpha_beta_default_to_nightly() {
        assert_eq!(effective_channel(None, "alpha"), "nightly");
        assert_eq!(effective_channel(None, "beta"), "nightly");
        assert_eq!(effective_channel(Some("stable"), "alpha"), "stable");
        assert_eq!(effective_channel(Some("nightly"), "stable"), "nightly");
        assert_eq!(effective_channel(Some("invalid"), "stable"), "stable");
    }
    #[test]
    fn missing_preference_preserves_the_embedded_beta_feed() {
        assert_eq!(feed_override(None), None);
        assert_eq!(feed_override(Some("invalid")), None);
        assert_eq!(feed_override(Some("nightly")), Some(NIGHTLY));
        assert_eq!(feed_override(Some("stable")), Some(STABLE));
    }
}
