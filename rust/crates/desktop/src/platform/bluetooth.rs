// SPDX-License-Identifier: GPL-3.0-only
//! Main-thread Core Bluetooth adapter. Never transports hotkey intents.
use crate::{
    bluetooth_protocol::{self as protocol, Received, Receiver},
    bridge::CommandSink,
};
use anyhow::{Context, Result, ensure};
use libloading::Library;
use serde_json::{Value, json};
use std::{
    collections::VecDeque,
    ffi::{CString, c_char, c_void},
    path::PathBuf,
    time::{Duration, Instant},
};

type Create = unsafe extern "C" fn(u32, *const c_char, i32) -> *mut c_void;
type Close = unsafe extern "C" fn(*mut c_void);
type Peer = unsafe extern "C" fn(*mut c_void, *const c_char);
type Mtu = unsafe extern "C" fn(*mut c_void) -> u32;
type Packet = unsafe extern "C" fn(*mut c_void, *const u8, u32) -> i32;
type Poll = unsafe extern "C" fn(*mut c_void, *mut u32, *mut u32, *mut u8, u32, *mut u32) -> i32;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(20);
const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(1);
const PEER_LEASE: Duration = Duration::from_secs(5);
struct Api {
    authorization: unsafe extern "C" fn() -> u32,
    create: Create,
    close: Close,
    peer: Peer,
    mtu: Mtu,
    send: Packet,
    notify: Packet,
    poll: Poll,
    _library: Library,
}
impl Api {
    fn load() -> Result<Self> {
        let path = std::env::var_os("VOCAL_MORE_NATIVE_LIBRARY")
            .map(PathBuf::from)
            .or_else(|| {
                std::env::current_exe()
                    .ok()
                    .and_then(|p| {
                        p.parent()?
                            .parent()
                            .map(|p| p.join("Frameworks/libvocal_more_audio.dylib"))
                    })
                    .filter(|p| p.is_file())
            })
            .unwrap_or_else(|| {
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("../../../.build/native/libvocal_more_audio.dylib")
            });
        // SAFETY: symbols come only from the app's explicit native library;
        // it stays loaded for the entire lifetime of all main-thread handles.
        unsafe {
            let library = Library::new(path).context("蓝牙原生组件不可用，请重新构建应用")?;
            let version = *library.get::<unsafe extern "C" fn() -> u32>(b"vm_bt_abi_version\0")?;
            ensure!(version() == 2, "蓝牙原生接口版本不兼容");
            Ok(Self {
                authorization: *library.get(b"vm_bt_authorization\0")?,
                create: *library.get(b"vm_bt_create\0")?,
                close: *library.get(b"vm_bt_close\0")?,
                peer: *library.get(b"vm_bt_set_peer\0")?,
                mtu: *library.get(b"vm_bt_mtu\0")?,
                send: *library.get(b"vm_bt_send\0")?,
                notify: *library.get(b"vm_bt_notify\0")?,
                poll: *library.get(b"vm_bt_poll\0")?,
                _library: library,
            })
        }
    }
}
pub fn authorization() -> Result<u32> {
    let api = Api::load()?;
    // This getter never constructs a manager or requests permission.
    Ok(unsafe { (api.authorization)() })
}
struct Outgoing {
    id: u64,
    frames: VecDeque<Vec<u8>>,
    epoch: u64,
    generation: u64,
    started: Instant,
}
pub enum Notice {
    Text(u64, String),
    Submitted(bool),
    Disconnected,
    Failed,
    Trusted,
}
pub struct Bluetooth {
    api: Option<Api>,
    handle: *mut c_void,
    role: u32,
    peer: String,
    state: u32,
    state_since: Instant,
    run_requested: bool,
    outgoing: Option<Outgoing>,
    receiver: Receiver,
    devices: Vec<String>,
    pending_peer: String,
    pairing_code: String,
    last_delivery: &'static str,
    last_latency_ms: Option<u128>,
    auto_connect: bool,
    manually_stopped: bool,
    reconnect_at: Option<Instant>,
    reconnect_attempt: u8,
    peer_target_revision: u64,
    last_advertised_target: Option<(u64, u32)>,
    last_advertised_at: Option<Instant>,
    last_peer_signal: Option<Instant>,
    trust_announced: bool,
    connection_revision: u64,
}
impl Default for Bluetooth {
    fn default() -> Self {
        Self {
            api: None,
            handle: std::ptr::null_mut(),
            role: 0,
            peer: String::new(),
            state: 0,
            state_since: Instant::now(),
            run_requested: false,
            outgoing: None,
            receiver: Receiver::default(),
            devices: vec![],
            pending_peer: String::new(),
            pairing_code: String::new(),
            last_delivery: "none",
            last_latency_ms: None,
            auto_connect: false,
            manually_stopped: false,
            reconnect_at: None,
            reconnect_attempt: 0,
            peer_target_revision: 0,
            last_advertised_target: None,
            last_advertised_at: None,
            last_peer_signal: None,
            trust_announced: false,
            connection_revision: 0,
        }
    }
}
impl Bluetooth {
    pub fn configure(&mut self, config: &Value, request_permission: bool) {
        let role = match config["bluetooth"]["mode"].as_str() {
            Some("send") => 1,
            Some("receive") => 2,
            _ => 0,
        };
        let peer = config["bluetooth"]["peer_id"].as_str().unwrap_or("");
        if peer != self.peer {
            // Also clear when changing a saved destination while stopped;
            // otherwise a later start could adopt the new ID with old receipts.
            self.receiver.reset_peer();
            self.peer_target_revision = 0;
            self.connection_revision = 0;
            self.last_peer_signal = None;
            self.trust_announced = false;
            self.pending_peer.clear();
            self.pairing_code.clear();
        }
        self.auto_connect = auto_connect_allowed(config, role, peer);
        if request_permission {
            self.manually_stopped = false;
        }
        let wanted = self.run_requested
            || (self.auto_connect && !self.manually_stopped)
            || request_permission;
        if !wanted || role == 0 {
            if role == 0 {
                self.close();
                self.manually_stopped = false;
            }
            self.role = role;
            self.peer = peer.into();
            self.state = 0;
            return;
        }
        if role == self.role && !request_permission && !self.handle.is_null() {
            if peer != self.peer {
                self.outgoing = None;
                self.peer = peer.into();
                self.state = 4;
                self.state_since = Instant::now();
                if let Some(api) = &self.api
                    && !self.handle.is_null()
                {
                    let peer = CString::new(peer).expect("validated UUID");
                    unsafe { (api.peer)(self.handle, peer.as_ptr()) };
                }
            }
            return;
        }
        self.drop_handle();
        self.role = role;
        self.peer = peer.into();
        self.devices.clear();
        self.pending_peer.clear();
        self.pairing_code.clear();
        if role == 0 {
            return;
        }
        self.run_requested = wanted;
        let result = self
            .api
            .take()
            .map(Ok)
            .unwrap_or_else(Api::load)
            .and_then(|api| {
                ensure!(
                    request_permission || unsafe { (api.authorization)() } == 3,
                    "蓝牙权限未授权，自动连接已暂停"
                );
                let peer = CString::new(peer).context("蓝牙设备ID无效")?;
                // No manager construction/permission request in the default off mode.
                let handle =
                    unsafe { (api.create)(role, peer.as_ptr(), i32::from(request_permission)) };
                ensure!(!handle.is_null(), "蓝牙组件初始化失败");
                self.handle = handle;
                self.api = Some(api);
                Ok(())
            });
        if result.is_err() {
            self.state = 7;
        }
    }
    pub fn peer(&self) -> &str {
        &self.peer
    }
    pub fn cancel_outgoing(&mut self) {
        if self.outgoing.is_some() {
            self.disconnected();
        }
    }
    pub fn peer_target_revision(&self) -> u64 {
        self.peer_target_revision
    }
    pub fn connection_revision(&self) -> u64 {
        self.connection_revision
    }
    pub fn advertise_target(&mut self, revision: u64, f1: u32) {
        if !self.connected() {
            return;
        }
        if self.last_advertised_target == Some((revision, f1))
            && self
                .last_advertised_at
                .is_some_and(|at| at.elapsed() < HEARTBEAT_INTERVAL)
        {
            return;
        }
        let mut data = vec![0xB2];
        data.extend_from_slice(&revision.to_le_bytes());
        data.extend_from_slice(&f1.to_le_bytes());
        if let Some(api) = &self.api
            && unsafe { (api.notify)(self.handle, data.as_ptr(), data.len() as u32) } == 1
        {
            self.last_advertised_target = Some((revision, f1));
            self.last_advertised_at = Some(Instant::now());
        }
    }
    fn drop_handle(&mut self) {
        if let Some(api) = &self.api
            && !self.handle.is_null()
        {
            unsafe { (api.close)(self.handle) };
        }
        self.handle = std::ptr::null_mut();
        self.outgoing = None;
        self.receiver.reset_connection();
        self.peer_target_revision = 0;
        self.last_advertised_target = None;
        self.last_advertised_at = None;
        self.last_peer_signal = None;
        self.connection_revision = 0;
    }
    fn disconnected(&mut self) {
        self.drop_handle();
        self.state = 6;
        if self.auto_connect && self.run_requested && !self.manually_stopped {
            let seconds = 1u64 << self.reconnect_attempt.min(4);
            self.reconnect_attempt = self.reconnect_attempt.saturating_add(1);
            self.reconnect_at = Some(Instant::now() + Duration::from_secs(seconds));
        }
    }
    fn write_next(&mut self) -> bool {
        let Some(packet) = self.outgoing.as_ref().and_then(|p| p.frames.front()) else {
            return true;
        };
        let Some(api) = &self.api else { return false };
        if unsafe { (api.send)(self.handle, packet.as_ptr(), packet.len() as u32) } != 1 {
            return false;
        }
        self.outgoing.as_mut().unwrap().frames.pop_front();
        true
    }
    pub fn send_text(&mut self, text: &str, commands: &CommandSink) -> Result<()> {
        ensure!(
            matches!(self.role, 1 | 2) && self.state == 5 && !self.handle.is_null(),
            "蓝牙尚未连接到已批准的接收设备"
        );
        ensure!(self.outgoing.is_none(), "上一条蓝牙文本仍在等待输入确认");
        ensure!(
            self.last_peer_signal
                .is_some_and(|at| at.elapsed() < PEER_LEASE),
            "对端会话已过期，未发送文字，请等待重新连接"
        );
        let api = self.api.as_ref().context("蓝牙组件不可用")?;
        let mtu = unsafe { (api.mtu)(self.handle) } as usize;
        let id = (uuid::Uuid::new_v4().as_u128() as u64).max(1);
        let epoch = commands.paste_epoch();
        let generation = commands.generation();
        ensure!(commands.can_paste(epoch, generation), "蓝牙结果已取消");
        self.outgoing = Some(Outgoing {
            id,
            frames: protocol::frames(id, text, mtu)?,
            epoch,
            generation,
            started: Instant::now(),
        });
        self.last_delivery = "sending";
        if !self.write_next() {
            self.disconnected();
            self.state = 6;
            self.last_delivery = "failed";
            anyhow::bail!("蓝牙发送未被接纳");
        }
        Ok(())
    }
    pub fn finish_receive(&mut self, id: u64, submitted: bool) {
        self.receiver.finish(id, submitted);
        // A completed message has one immutable result, including when an
        // earlier receipt was lost and a duplicate reaches the adapter.
        let submitted = self.receiver.completed(id).unwrap_or(false);
        let bytes = protocol::receipt(id, submitted);
        if let Some(api) = &self.api
            && !self.handle.is_null()
            && self.state == 5
        {
            let queued = unsafe { (api.notify)(self.handle, bytes.as_ptr(), bytes.len() as u32) };
            if queued != 1 {
                self.last_delivery = "receipt_failed";
                return;
            }
        }
        self.last_delivery = if submitted { "submitted" } else { "rejected" };
    }
    pub fn poll(&mut self, commands: &CommandSink) -> Vec<Notice> {
        let mut notices = Vec::new();
        if self.reconnect_at.is_some_and(|at| Instant::now() >= at)
            && self.run_requested
            && !self.manually_stopped
        {
            self.reconnect_at = None;
            if let Some(api) = &self.api
                && unsafe { (api.authorization)() } == 3
            {
                let peer = CString::new(self.peer.as_str()).expect("validated UUID");
                self.handle = unsafe { (api.create)(self.role, peer.as_ptr(), 0) };
                self.state = if self.handle.is_null() { 9 } else { 0 };
                self.state_since = Instant::now();
                if self.handle.is_null() {
                    self.disconnected();
                }
            } else {
                self.state = 7;
            }
        }
        if self.state == 3 && self.state_since.elapsed() >= CONNECT_TIMEOUT {
            self.disconnected();
            self.state = 6;
            self.last_delivery = "failed";
            notices.push(Notice::Failed);
            return notices;
        }
        if self.outgoing.as_ref().is_some_and(|p| {
            p.started.elapsed() >= protocol::TRANSFER_TIMEOUT
                || !commands.can_paste(p.epoch, p.generation)
        }) {
            self.disconnected();
            self.state = 6;
            self.last_delivery = "unknown";
            notices.push(Notice::Failed);
            return notices;
        }
        self.receiver.expire(Instant::now());
        for _ in 0..32 {
            let Some(api) = &self.api else { break };
            if self.handle.is_null() {
                break;
            }
            let (mut kind, mut code, mut length) = (0, 0, 0);
            let mut bytes = [0u8; 512];
            let result = unsafe {
                (api.poll)(
                    self.handle,
                    &mut kind,
                    &mut code,
                    bytes.as_mut_ptr(),
                    512,
                    &mut length,
                )
            };
            if result == 0 {
                break;
            }
            if result < 0 || length as usize > bytes.len() {
                self.disconnected();
                self.state = 9;
                notices.push(Notice::Failed);
                break;
            }
            let data = &bytes[..length as usize];
            match kind {
                1 => {
                    let disconnected = self.state == 5 && code != 5;
                    if code == 5 && self.state != 5 {
                        self.connection_revision = (uuid::Uuid::new_v4().as_u128() as u64).max(1);
                    }
                    if code != self.state {
                        self.state_since = Instant::now();
                    }
                    self.state = code;
                    if code == 5 {
                        self.last_peer_signal = Some(Instant::now());
                        self.pending_peer.clear();
                        self.reconnect_attempt = 0;
                        self.reconnect_at = None;
                        if !self.trust_announced {
                            self.trust_announced = true;
                            notices.push(Notice::Trusted);
                        }
                    }
                    if disconnected {
                        self.outgoing = None;
                        self.receiver.reset_connection();
                        self.peer_target_revision = 0;
                        self.last_advertised_target = None;
                        self.last_advertised_at = None;
                        self.last_peer_signal = None;
                        self.connection_revision = 0;
                        self.last_delivery = "unknown";
                        notices.push(Notice::Disconnected);
                    }
                    if matches!(code, 6 | 9) && self.auto_connect && self.run_requested {
                        self.disconnected();
                        break;
                    }
                }
                2 => {
                    if code != 0 || !self.write_next() {
                        self.disconnected();
                        self.state = 6;
                        self.last_delivery = "failed";
                        notices.push(Notice::Failed);
                        break;
                    }
                }
                3 if self.state == 5 => match self.receiver.accept(data, Instant::now()) {
                    Ok(Received::Text(id, text)) => notices.push(Notice::Text(id, text)),
                    Ok(Received::Duplicate(id)) => {
                        if let Some(done) = self.receiver.completed(id) {
                            self.finish_receive(id, done);
                        }
                    }
                    Ok(Received::Partial) => {}
                    Err(_) => {
                        self.last_delivery = "invalid_packet";
                        notices.push(Notice::Failed);
                    }
                },
                4 => {
                    if let Ok((id, submitted)) = protocol::read_receipt(data)
                        && self
                            .outgoing
                            .as_ref()
                            .is_some_and(|p| p.id == id && p.frames.is_empty())
                    {
                        let completed = self.outgoing.take().unwrap();
                        self.last_latency_ms = Some(completed.started.elapsed().as_millis());
                        self.last_delivery = if submitted { "submitted" } else { "rejected" };
                        notices.push(Notice::Submitted(submitted));
                    }
                }
                12 if self.state == 5 && data.len() == 13 && data[0] == 0xB2 => {
                    self.peer_target_revision = u64::from_le_bytes(data[1..9].try_into().unwrap());
                    self.last_peer_signal = Some(Instant::now());
                }
                13 => {
                    self.disconnected();
                    notices.push(Notice::Failed);
                    break;
                }
                8 if self.devices.len() < 32 => {
                    if let Ok(value) = std::str::from_utf8(data)
                        && uuid::Uuid::parse_str(value).is_ok()
                        && !self.devices.iter().any(|id| id == value)
                    {
                        self.devices.push(value.into());
                    }
                }
                9 => {
                    if let Ok(value) = std::str::from_utf8(data)
                        && uuid::Uuid::parse_str(value).is_ok()
                    {
                        self.pending_peer = value.into();
                    }
                }
                10 if data.len() == 6 && data.iter().all(u8::is_ascii_digit) => {
                    self.pairing_code = String::from_utf8(data.to_vec()).unwrap();
                }
                _ => {}
            }
        }
        // GATT service invalidation is not always accompanied by physical
        // disconnection. A one-second target heartbeat proves the peer app
        // session is alive, even when its input target is unready/unchanged.
        if self.connected()
            && self
                .last_peer_signal
                .is_some_and(|at| at.elapsed() >= PEER_LEASE)
        {
            self.disconnected();
            self.last_delivery = "unknown";
            notices.push(Notice::Disconnected);
        }
        notices
    }
    pub fn connected(&self) -> bool {
        self.state == 5
    }
    pub fn active(&self) -> bool {
        self.role != 0 && !self.handle.is_null()
    }
    pub fn next_tick(&self) -> Option<Duration> {
        self.reconnect_at
            .map(|at| at.saturating_duration_since(Instant::now()))
            .or_else(|| {
                self.active()
                    .then(|| Duration::from_millis(if self.connected() { 20 } else { 200 }))
            })
    }
    pub fn status(&self) -> Value {
        json!({"state":self.state,"mode":self.role,"devices":self.devices,"pending_peer":self.pending_peer,
            "pairing_code":self.pairing_code,"delivery":self.last_delivery,"latency_ms":self.last_latency_ms,
            "peer_target_ready":self.peer_target_revision!=0,"auto_connect":self.auto_connect,
            "reconnect_pending":self.reconnect_at.is_some()})
    }
    pub fn diagnostics(&self) -> Value {
        json!({"state":self.state,"mode":self.role,"delivery":self.last_delivery,"latency_ms":self.last_latency_ms,
            "sending":self.outgoing.is_some()})
    }
    pub fn close(&mut self) {
        self.run_requested = false;
        self.manually_stopped = true;
        self.reconnect_at = None;
        self.drop_handle();
        self.api = None;
        self.state = 0;
        self.state_since = Instant::now();
    }
}
fn auto_connect_allowed(config: &Value, role: u32, peer: &str) -> bool {
    matches!(role, 1 | 2)
        && config["bluetooth"]["auto_connect"] == true
        && uuid::Uuid::parse_str(peer).is_ok()
        && config["bluetooth"]["trusted_peer_id"] == peer
}
impl Drop for Bluetooth {
    fn drop(&mut self) {
        self.close();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    #[derive(Default)]
    struct Wire {
        events: VecDeque<(u32, u32, Vec<u8>)>,
        writes: Vec<Vec<u8>>,
        receipts: Vec<Vec<u8>>,
        fail_write: bool,
        closes: usize,
        creates: Vec<(u32, i32)>,
        denied: bool,
    }
    thread_local! { static WIRE: RefCell<Wire> = RefCell::default(); }
    unsafe extern "C" fn create(role: u32, _: *const c_char, permission: i32) -> *mut c_void {
        WIRE.with_borrow_mut(|wire| wire.creates.push((role, permission)));
        std::ptr::NonNull::<u8>::dangling().as_ptr().cast()
    }
    unsafe extern "C" fn close(_: *mut c_void) {
        WIRE.with_borrow_mut(|wire| {
            wire.closes += 1;
            wire.events.clear();
        });
    }
    unsafe extern "C" fn peer(_: *mut c_void, _: *const c_char) {
        WIRE.with_borrow_mut(|wire| wire.events.clear());
    }
    unsafe extern "C" fn mtu(_: *mut c_void) -> u32 {
        20
    }
    unsafe extern "C" fn authorization() -> u32 {
        WIRE.with_borrow(|wire| if wire.denied { 0 } else { 3 })
    }
    unsafe extern "C" fn send(_: *mut c_void, bytes: *const u8, length: u32) -> i32 {
        // The actual adapter passes a live immutable slice; the fake never
        // dereferences the opaque handle or calls a native Bluetooth API.
        let bytes = unsafe { std::slice::from_raw_parts(bytes, length as usize) }.to_vec();
        WIRE.with_borrow_mut(|wire| {
            if wire.fail_write {
                return 0;
            }
            wire.writes.push(bytes);
            wire.events.push_back((2, 0, vec![]));
            1
        })
    }
    unsafe extern "C" fn notify(_: *mut c_void, bytes: *const u8, length: u32) -> i32 {
        let bytes = unsafe { std::slice::from_raw_parts(bytes, length as usize) }.to_vec();
        WIRE.with_borrow_mut(|wire| wire.receipts.push(bytes));
        1
    }
    unsafe extern "C" fn poll(
        _: *mut c_void,
        kind: *mut u32,
        code: *mut u32,
        bytes: *mut u8,
        capacity: u32,
        length: *mut u32,
    ) -> i32 {
        WIRE.with_borrow_mut(|wire| {
            let Some((event, result, value)) = wire.events.pop_front() else {
                return 0;
            };
            assert!(value.len() <= capacity as usize);
            // Same caller-owned output buffers used by the real poll adapter.
            unsafe {
                *kind = event;
                *code = result;
                *length = value.len() as u32;
                std::ptr::copy_nonoverlapping(value.as_ptr(), bytes, value.len());
            }
            1
        })
    }
    fn mock(role: u32) -> Bluetooth {
        Bluetooth {
            api: Some(Api {
                authorization,
                create,
                close,
                peer,
                mtu,
                send,
                notify,
                poll,
                _library: libloading::os::unix::Library::this().into(),
            }),
            handle: std::ptr::NonNull::<u8>::dangling().as_ptr().cast(),
            role,
            state: 5,
            state_since: Instant::now(),
            run_requested: true,
            peer: String::new(),
            outgoing: None,
            receiver: Receiver::default(),
            devices: vec![],
            pending_peer: String::new(),
            pairing_code: String::new(),
            last_delivery: "none",
            last_latency_ms: None,
            last_peer_signal: Some(Instant::now()),
            ..Bluetooth::default()
        }
    }
    fn saved_config() -> Value {
        json!({"bluetooth":{"mode":"send","peer_id":"74DD663C-68EE-45B1-A969-C623083B0291",
            "trusted_peer_id":"74DD663C-68EE-45B1-A969-C623083B0291","auto_connect":true}})
    }
    #[test]
    fn expired_peer_lease_rejects_before_sending_even_when_poll_was_delayed() {
        WIRE.with_borrow_mut(|wire| *wire = Wire::default());
        let mut endpoint = mock(1);
        endpoint.last_peer_signal = Some(Instant::now() - PEER_LEASE);
        assert!(
            endpoint
                .send_text("fixed fixture", &CommandSink::fixture())
                .is_err()
        );
        assert!(WIRE.with_borrow(|wire| wire.writes.is_empty()));
        assert!(endpoint.outgoing.is_none());
    }
    #[test]
    fn peer_switch_never_uses_another_peers_receipt_or_target() {
        WIRE.with_borrow_mut(|wire| *wire = Wire::default());
        let mut endpoint = mock(1);
        endpoint.peer = "74DD663C-68EE-45B1-A969-C623083B0291".into();
        endpoint.peer_target_revision = 42;
        endpoint.receiver.finish(17, true);
        let mut config = saved_config();
        config["bluetooth"]["peer_id"] = json!("5FE6FD24-99E6-44B0-8A7D-88E653C5925D");
        endpoint.configure(&config, false);
        assert_eq!(endpoint.peer_target_revision, 0);
        assert!(!endpoint.connected());
        let packet = protocol::frames(17, "fixed fixture", 512)
            .unwrap()
            .pop_front()
            .unwrap();
        WIRE.with_borrow_mut(|wire| {
            wire.events.push_back((1, 5, vec![]));
            wire.events.push_back((3, 0, packet));
        });
        assert!(
            endpoint
                .poll(&CommandSink::fixture())
                .iter()
                .any(|notice| matches!(notice, Notice::Text(17, text) if text == "fixed fixture"))
        );
        assert!(WIRE.with_borrow(|wire| wire.receipts.is_empty()));
    }
    #[test]
    fn stopped_peer_change_clears_receipts_before_later_start() {
        WIRE.with_borrow_mut(|wire| *wire = Wire::default());
        let mut endpoint = mock(1);
        endpoint.peer = "74DD663C-68EE-45B1-A969-C623083B0291".into();
        endpoint.receiver.finish(17, true);
        endpoint.close();
        let mut config = saved_config();
        config["bluetooth"]["peer_id"] = json!("5FE6FD24-99E6-44B0-8A7D-88E653C5925D");
        endpoint.configure(&config, false);
        assert!(endpoint.receiver.completed(17).is_none());
        assert!(!endpoint.active());
    }
    #[test]
    fn duplicate_completion_preserves_the_actual_first_input_receipt() {
        WIRE.with_borrow_mut(|wire| *wire = Wire::default());
        let mut endpoint = mock(2);
        let packet = protocol::frames(17, "fixed fixture", 512)
            .unwrap()
            .pop_front()
            .unwrap();
        WIRE.with_borrow_mut(|wire| wire.events.push_back((3, 0, packet)));
        assert!(matches!(
            endpoint.poll(&CommandSink::fixture()).as_slice(),
            [Notice::Text(17, _)]
        ));
        endpoint.finish_receive(17, true);
        endpoint.finish_receive(17, false);
        let receipts = WIRE.with_borrow(|wire| wire.receipts.clone());
        assert_eq!(receipts.len(), 2);
        assert!(
            receipts
                .iter()
                .all(|bytes| protocol::read_receipt(bytes).unwrap() == (17, true))
        );
    }
    #[test]
    fn session_loss_invalidates_target_before_reconnection_without_auto_connect() {
        WIRE.with_borrow_mut(|wire| *wire = Wire::default());
        let mut endpoint = mock(1);
        endpoint.peer_target_revision = 42;
        WIRE.with_borrow_mut(|wire| wire.events.push_back((1, 3, vec![])));
        assert!(matches!(
            endpoint.poll(&CommandSink::fixture()).as_slice(),
            [Notice::Disconnected]
        ));
        assert_eq!(endpoint.peer_target_revision, 0);
        WIRE.with_borrow_mut(|wire| wire.events.push_back((1, 5, vec![])));
        endpoint.poll(&CommandSink::fixture());
        assert!(endpoint.connected());
        assert_eq!(
            endpoint.peer_target_revision, 0,
            "connection alone is not proof of an input target"
        );
    }
    #[test]
    fn app_restart_reconnects_only_matching_trusted_peer_with_existing_permission() {
        for (trusted, auto, denied, expected) in [
            (true, true, false, true),
            (false, true, false, false),
            (true, false, false, false),
            (true, true, true, false),
        ] {
            WIRE.with_borrow_mut(|wire| {
                *wire = Wire::default();
                wire.denied = denied;
            });
            let mut config = saved_config();
            if !trusted {
                config["bluetooth"]["trusted_peer_id"] = json!("");
            }
            config["bluetooth"]["auto_connect"] = json!(auto);
            let mut restarted = mock(0);
            restarted.handle = std::ptr::null_mut();
            restarted.run_requested = false;
            restarted.state = 0;
            restarted.configure(&config, false);
            assert_eq!(restarted.active(), expected);
            let creates = WIRE.with_borrow(|wire| wire.creates.clone());
            assert_eq!(creates, if expected { vec![(1, 0)] } else { vec![] });
        }
    }
    #[test]
    fn temporary_disconnect_restores_link_without_replaying_text_and_stop_cancels_retry() {
        WIRE.with_borrow_mut(|wire| *wire = Wire::default());
        let mut endpoint = mock(1);
        endpoint.auto_connect = true;
        endpoint.peer = "74DD663C-68EE-45B1-A969-C623083B0291".into();
        let sink = CommandSink::fixture();
        endpoint
            .send_text("fixed in-flight fixture", &sink)
            .unwrap();
        let written = WIRE.with_borrow(|wire| wire.writes.len());
        WIRE.with_borrow_mut(|wire| {
            wire.events.clear();
            wire.events.push_back((1, 6, vec![]));
        });
        assert!(matches!(
            endpoint.poll(&sink).as_slice(),
            [Notice::Disconnected]
        ));
        assert!(endpoint.outgoing.is_none());
        assert!(endpoint.reconnect_at.is_some());
        endpoint.reconnect_at = Some(Instant::now());
        endpoint.poll(&sink);
        assert!(endpoint.active());
        assert_eq!(WIRE.with_borrow(|wire| wire.creates.clone()), vec![(1, 0)]);
        assert_eq!(WIRE.with_borrow(|wire| wire.writes.len()), written);
        endpoint.close();
        endpoint.configure(&saved_config(), false);
        assert!(!endpoint.active());
        assert!(endpoint.reconnect_at.is_none());
        assert_eq!(WIRE.with_borrow(|wire| wire.creates.len()), 1);
    }
    #[test]
    fn unchanged_unready_target_still_sends_rate_limited_session_heartbeats() {
        WIRE.with_borrow_mut(|wire| *wire = Wire::default());
        let mut endpoint = mock(2);
        endpoint.advertise_target(0, 0);
        endpoint.advertise_target(0, 0);
        assert_eq!(WIRE.with_borrow(|wire| wire.receipts.len()), 1);
        endpoint.last_advertised_at = Some(Instant::now() - HEARTBEAT_INTERVAL);
        endpoint.advertise_target(0, 0);
        let heartbeats = WIRE.with_borrow(|wire| wire.receipts.clone());
        assert_eq!(heartbeats.len(), 2);
        assert_eq!(heartbeats[0], heartbeats[1]);
        assert_eq!(heartbeats[0], [vec![0xB2], vec![0; 12]].concat());
    }
    #[test]
    fn peer_app_restart_expires_stale_physical_link_without_replaying_text() {
        WIRE.with_borrow_mut(|wire| *wire = Wire::default());
        let mut endpoint = mock(1);
        endpoint.auto_connect = true;
        endpoint.peer_target_revision = 42;
        let sink = CommandSink::fixture();
        endpoint
            .send_text("fixed in-flight fixture", &sink)
            .unwrap();
        endpoint.last_peer_signal = Some(Instant::now() - PEER_LEASE);
        let written = WIRE.with_borrow(|wire| wire.writes.len());
        WIRE.with_borrow_mut(|wire| wire.events.clear());
        assert!(matches!(
            endpoint.poll(&sink).as_slice(),
            [Notice::Disconnected]
        ));
        assert!(!endpoint.active());
        assert!(endpoint.outgoing.is_none());
        assert_eq!(endpoint.peer_target_revision, 0);
        assert!(endpoint.reconnect_at.is_some());
        endpoint.reconnect_at = Some(Instant::now());
        endpoint.poll(&sink);
        assert!(endpoint.active());
        assert_eq!(WIRE.with_borrow(|wire| wire.writes.len()), written);
        assert_eq!(WIRE.with_borrow(|wire| wire.creates.clone()), vec![(1, 0)]);
    }
    #[test]
    fn queued_fresh_heartbeat_refreshes_lease_before_expiry_check() {
        WIRE.with_borrow_mut(|wire| *wire = Wire::default());
        let mut endpoint = mock(1);
        endpoint.last_peer_signal = Some(Instant::now() - PEER_LEASE);
        let mut heartbeat = vec![0xB2];
        heartbeat.extend_from_slice(&7u64.to_le_bytes());
        heartbeat.extend_from_slice(&0u32.to_le_bytes());
        WIRE.with_borrow_mut(|wire| wire.events.push_back((12, 0, heartbeat)));
        assert!(endpoint.poll(&CommandSink::fixture()).is_empty());
        assert!(endpoint.connected());
        assert_eq!(endpoint.peer_target_revision, 7);
        assert!(endpoint.last_peer_signal.unwrap().elapsed() < PEER_LEASE);
        assert_eq!(WIRE.with_borrow(|wire| wire.closes), 0);
    }
    #[test]
    fn stalled_connection_closes_with_a_bounded_deadline() {
        WIRE.with_borrow_mut(|wire| *wire = Wire::default());
        let mut sender = mock(1);
        sender.state = 3;
        sender.state_since = Instant::now() - CONNECT_TIMEOUT;
        assert!(matches!(
            sender.poll(&CommandSink::fixture()).as_slice(),
            [Notice::Failed]
        ));
        assert!(!sender.active());
        assert_eq!(sender.state, 6);
        assert_eq!(WIRE.with_borrow(|wire| wire.closes), 1);
    }
    #[test]
    fn saved_role_needs_explicit_start_and_stop_survives_settings_refresh() {
        WIRE.with_borrow_mut(|wire| *wire = Wire::default());
        let config = json!({"bluetooth":{"mode":"receive","peer_id":""}});
        let mut receiver = Bluetooth::default();
        receiver.configure(&config, false);
        assert!(!receiver.active());
        assert!(receiver.api.is_none());
        let mut receiver = mock(2);
        receiver.close();
        receiver.configure(&config, false);
        assert!(!receiver.active());
        assert!(!receiver.run_requested);
        assert_eq!(WIRE.with_borrow(|wire| wire.closes), 1);
    }
    #[test]
    fn mocked_two_endpoint_transfer_requires_input_receipt_and_suppresses_replay() {
        WIRE.with_borrow_mut(|wire| *wire = Wire::default());
        let sink = CommandSink::fixture();
        let mut sender = mock(1);
        let text = "VM-BT-FIXTURE，🙂";
        sender.send_text(text, &sink).unwrap();
        assert!(sender.poll(&sink).is_empty());
        let id = sender.outgoing.as_ref().unwrap().id;
        assert!(sender.outgoing.as_ref().unwrap().frames.is_empty());
        assert_eq!(sender.last_delivery, "sending");
        let packets = WIRE.with_borrow(|wire| wire.writes.clone());
        WIRE.with_borrow_mut(|wire| {
            for packet in &packets {
                wire.events.push_back((3, 0, packet.clone()));
            }
        });
        let mut receiver = mock(2);
        let received = receiver.poll(&sink);
        assert_eq!(received.len(), 1);
        assert!(matches!(&received[0], Notice::Text(seen, value) if *seen == id && value == text));
        assert!(WIRE.with_borrow(|wire| wire.receipts.is_empty()));
        receiver.finish_receive(id, true);
        let ack = WIRE.with_borrow(|wire| wire.receipts[0].clone());
        WIRE.with_borrow_mut(|wire| wire.events.push_back((4, 0, ack)));
        assert!(matches!(
            sender.poll(&sink).as_slice(),
            [Notice::Submitted(true)]
        ));
        assert!(sender.outgoing.is_none());
        WIRE.with_borrow_mut(|wire| {
            for packet in packets {
                wire.events.push_back((3, 0, packet));
            }
        });
        assert!(receiver.poll(&sink).is_empty());
        assert!(!sender.diagnostics().to_string().contains(text));
        assert!(!receiver.diagnostics().to_string().contains(text));
    }
    #[test]
    fn failed_write_closes_connection_without_retry_or_local_fallback() {
        WIRE.with_borrow_mut(|wire| {
            *wire = Wire {
                fail_write: true,
                ..Wire::default()
            }
        });
        let mut sender = mock(1);
        let sink = CommandSink::fixture();
        assert!(sender.send_text("fixed fixture", &sink).is_err());
        assert!(sender.send_text("second fixture", &sink).is_err());
        assert!(WIRE.with_borrow(|wire| wire.writes.is_empty() && wire.closes == 1));
    }
    #[test]
    fn source_cancel_stops_remaining_fragments_before_processing_write_callback() {
        WIRE.with_borrow_mut(|wire| *wire = Wire::default());
        let mut sender = mock(1);
        let sink = CommandSink::fixture();
        sender
            .send_text("fixed fixture requiring several frames", &sink)
            .unwrap();
        sink.request("cancel", json!({}));
        assert!(matches!(sender.poll(&sink).as_slice(), [Notice::Failed]));
        assert!(sender.outgoing.is_none());
        assert_eq!(WIRE.with_borrow(|wire| wire.writes.len()), 1);
        assert_eq!(WIRE.with_borrow(|wire| wire.closes), 1);
    }
    #[test]
    fn both_roles_accept_reverse_text_and_failed_admission_stops_sending() {
        for role in [1, 2] {
            WIRE.with_borrow_mut(|wire| *wire = Wire::default());
            let mut endpoint = mock(role);
            let sink = CommandSink::fixture();
            let frame = protocol::frames(22, "fixed fixture", 512)
                .unwrap()
                .pop_front()
                .unwrap();
            WIRE.with_borrow_mut(|wire| wire.events.push_back((3, 0, frame)));
            assert!(
                matches!(endpoint.poll(&sink).as_slice(), [Notice::Text(22, text)] if text == "fixed fixture")
            );
            endpoint.finish_receive(22, true);
            endpoint
                .send_text("fixed fixture requiring several frames", &sink)
                .unwrap();
            WIRE.with_borrow_mut(|wire| wire.events.front_mut().unwrap().1 = 1);
            assert!(matches!(endpoint.poll(&sink).as_slice(), [Notice::Failed]));
            assert_eq!(WIRE.with_borrow(|wire| wire.writes.len()), 1);
        }
    }
}
