// SPDX-License-Identifier: GPL-3.0-only
use super::{class, ns};
use objc2::{
    DefinedClass, MainThreadMarker, MainThreadOnly, define_class, msg_send,
    rc::Retained,
    runtime::{AnyClass, AnyObject},
};
use objc2_foundation::{NSObject, NSObjectProtocol, NSString};
use serde_json::Value;
use std::cell::Cell;

/// Every build reads one signed feed. Beta items carry `<sparkle:channel>beta`
/// and are offered only when the updater allows that channel, so beta installs
/// receive both betas and stable releases while stable installs never see betas.
const FEED: &str =
    "https://github.com/sm-yjr/vocal-more/releases/download/sparkle-feed/appcast.xml";
const BETA: &str = "beta";
struct DelegateIvars {
    beta: Cell<bool>,
}
define_class!(
    #[unsafe(super=NSObject)]
    #[name="VMRustSparkleDelegate"]
    #[thread_kind=MainThreadOnly]
    #[ivars=DelegateIvars]
    struct Delegate;
    unsafe impl NSObjectProtocol for Delegate {}
    impl Delegate {
        // Overrides any feed URL a pre-0.6 build left behind (such as the
        // retired alpha feed) so upgraded installs converge on one feed.
        #[unsafe(method_id(feedURLStringForUpdater:))]
        fn feed(&self,_updater:&AnyObject)->Option<Retained<NSString>> {Some(ns(FEED))}
        #[unsafe(method_id(allowedChannelsForUpdater:))]
        fn channels(&self,_updater:&AnyObject)->Retained<AnyObject> {
            unsafe {
                if self.ivars().beta.get() {
                    msg_send![class(c"NSSet"),setWithObject:&*ns(BETA)]
                } else {
                    msg_send![class(c"NSSet"), set]
                }
            }
        }
    }
);
impl Delegate {
    fn new(mtm: MainThreadMarker, beta: bool) -> Retained<Self> {
        let this = Self::alloc(mtm).set_ivars(DelegateIvars {
            beta: Cell::new(beta),
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
        let delegate = Delegate::new(mtm, channel == BETA);
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
            .filter(|v| matches!(*v, "stable" | "beta"))
        else {
            return;
        };
        if self.channel == channel {
            return;
        }
        self.channel = channel.into();
        self.delegate.ivars().beta.set(channel == BETA);
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
        serde_json::json!({"available":self.controller.is_some(),"channel":self.channel,"feed":FEED,"startup_error":self.error})
    }
}
/// An explicit preference wins; otherwise a beta bundle follows beta and every
/// other build (including retired alpha preferences) follows stable.
pub fn effective_channel(configured: Option<&str>, release: &str) -> &'static str {
    match configured {
        Some("beta") => "beta",
        Some("stable") => "stable",
        _ if release == "beta" => "beta",
        _ => "stable",
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn explicit_channels_override_the_bundle_and_retired_alpha_goes_stable() {
        assert_eq!(effective_channel(None, "beta"), "beta");
        assert_eq!(effective_channel(None, "stable"), "stable");
        assert_eq!(effective_channel(None, "alpha"), "stable");
        assert_eq!(effective_channel(Some("nightly"), "stable"), "stable");
        assert_eq!(effective_channel(Some("stable"), "beta"), "stable");
        assert_eq!(effective_channel(Some("beta"), "stable"), "beta");
        assert_eq!(effective_channel(Some("invalid"), "stable"), "stable");
    }
}
