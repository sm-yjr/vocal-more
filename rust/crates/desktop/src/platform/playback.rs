// SPDX-License-Identifier: GPL-3.0-only
use super::{class, ffi, ns};
use crate::bridge::CommandSink;
use anyhow::{Context, Result, bail};
use base64::{Engine, engine::general_purpose::STANDARD};
use objc2::{msg_send, rc::Retained, runtime::AnyObject};
use serde_json::json;
use std::{
    path::Path,
    ptr,
    time::{Duration, Instant},
};

pub struct Playback {
    commands: CommandSink,
    player: Option<Retained<AnyObject>>,
    id: Option<String>,
    preview: Option<Retained<AnyObject>>,
    last_poll: Instant,
}
impl Playback {
    pub fn new(commands: CommandSink) -> Self {
        Self {
            commands,
            player: None,
            id: None,
            preview: None,
            last_poll: Instant::now(),
        }
    }
    pub fn next_tick_delay(&self) -> Option<Duration> {
        (self.player.is_some() || self.preview.is_some())
            .then(|| Duration::from_millis(250).saturating_sub(self.last_poll.elapsed()))
    }
    pub fn play(&mut self, id: &str, path: &Path) -> Result<()> {
        self.stop(None);
        self.stop_preview();
        if !path.is_file() {
            bail!("录音文件不存在");
        }
        let path = path.to_str().context("录音路径不是 UTF-8")?;
        let url: Retained<AnyObject> =
            unsafe { msg_send![class(c"NSURL"),fileURLWithPath:&*ns(path)] };
        let player: Option<Retained<AnyObject>> =
            unsafe { msg_send![class(c"AVPlayer"),playerWithURL:&*url] };
        let player = player.context("无法打开录音播放器")?;
        unsafe {
            let _: () = msg_send![&*player, play];
        }
        self.player = Some(player);
        self.id = Some(id.into());
        self.commands.request(
            "platform_event",
            json!({"method":"recordingPlaybackStarted","params":{"id":id}}),
        );
        Ok(())
    }
    pub fn stop(&mut self, id: Option<&str>) {
        if id.is_some_and(|id| self.id.as_deref() != Some(id)) {
            return;
        }
        if let Some(player) = self.player.take() {
            unsafe {
                let _: () = msg_send![&*player, pause];
                let _: () =
                    msg_send![&*player,replaceCurrentItemWithPlayerItem:ptr::null::<AnyObject>()];
            }
        }
        if let Some(id) = self.id.take() {
            self.commands.request(
                "platform_event",
                json!({"method":"recordingPlaybackEnded","params":{"id":id}}),
            );
        }
    }
    pub fn preview(&mut self, encoded: &str) -> Result<()> {
        self.stop(None);
        self.stop_preview();
        if encoded.len() > 4 * 1024 * 1024 {
            bail!("麦克风试听数据超过限制");
        }
        let bytes = STANDARD.decode(encoded).context("麦克风试听数据无效")?;
        let data: Retained<AnyObject> = unsafe {
            msg_send![class(c"NSData"),dataWithBytes:bytes.as_ptr().cast::<std::ffi::c_void>(),length:bytes.len()]
        };
        let allocated: objc2::rc::Allocated<AnyObject> =
            unsafe { msg_send![class(c"AVAudioPlayer"), alloc] };
        let mut error: *mut AnyObject = ptr::null_mut();
        let player: Option<Retained<AnyObject>> =
            unsafe { msg_send![allocated,initWithData:&*data,error:&mut error] };
        let player = player.context("无法播放麦克风试听")?;
        let ok: bool = unsafe { msg_send![&*player, play] };
        if !ok {
            bail!("无法启动麦克风试听");
        }
        self.preview = Some(player);
        Ok(())
    }
    pub fn stop_preview(&mut self) {
        if let Some(player) = self.preview.take() {
            unsafe {
                let _: () = msg_send![&*player, stop];
            }
            self.commands.request(
                "platform_event",
                json!({"method":"micTestPlaybackEnded","params":{}}),
            );
        }
    }
    pub fn tick(&mut self) {
        if self.last_poll.elapsed() < Duration::from_millis(250) {
            return;
        }
        self.last_poll = Instant::now();
        let ended = self.player.as_ref().is_some_and(|player| unsafe {
            let error: Option<Retained<AnyObject>> = msg_send![&**player, error];
            if error.is_some() {
                return true;
            }
            let item: Option<Retained<AnyObject>> = msg_send![&**player, currentItem];
            let Some(item) = item else {
                return true;
            };
            let error: Option<Retained<AnyObject>> = msg_send![&*item, error];
            if error.is_some() {
                return true;
            }
            let duration: ffi::Time = msg_send![&*item, duration];
            let position: ffi::Time = msg_send![&**player, currentTime];
            let duration = ffi::CMTimeGetSeconds(duration);
            let position = ffi::CMTimeGetSeconds(position);
            duration.is_finite() && duration >= 0.0 && position >= duration
        });
        if ended {
            self.stop(None);
        }
        let preview_ended = self.preview.as_ref().is_some_and(|player| unsafe {
            let playing: bool = msg_send![&**player, isPlaying];
            !playing
        });
        if preview_ended {
            self.stop_preview();
        }
    }
    pub fn close(&mut self) {
        self.stop(None);
        self.stop_preview();
    }
}
