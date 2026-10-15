// SPDX-License-Identifier: GPL-3.0-only
//! Bounded BLE text framing. No keyboard/control events are part of this protocol.
use anyhow::{Result, ensure};
use std::{
    collections::VecDeque,
    time::{Duration, Instant},
};

pub const HEADER: usize = 16;
pub const MAX_TEXT: usize = 32 * 1024;
pub const TRANSFER_TIMEOUT: Duration = Duration::from_secs(30);
fn text_is_valid(text: &str) -> bool {
    !text
        .chars()
        .any(|c| c.is_control() && !matches!(c, '\n' | '\r' | '\t'))
}
const MAX_RECEIPTS: usize = 128;

#[derive(Debug, PartialEq, Eq)]
pub enum Received {
    Partial,
    Duplicate(u64),
    Text(u64, String),
}
struct Assembly {
    id: u64,
    total: usize,
    bytes: Vec<u8>,
    started: Instant,
}
#[derive(Default)]
pub struct Receiver {
    active: Option<Assembly>,
    pending: Option<u64>,
    // Retained across disconnects while the receiver process stays open.
    receipts: VecDeque<(u64, bool)>,
}
fn header(kind: u8, id: u64, total: u16, offset: u16) -> Vec<u8> {
    let mut bytes = Vec::with_capacity(HEADER);
    bytes.extend_from_slice(b"VM");
    bytes.extend_from_slice(&[2, kind]);
    bytes.extend_from_slice(&id.to_le_bytes());
    bytes.extend_from_slice(&total.to_le_bytes());
    bytes.extend_from_slice(&offset.to_le_bytes());
    bytes
}
pub fn frames(id: u64, text: &str, mtu: usize) -> Result<VecDeque<Vec<u8>>> {
    ensure!(
        id != 0 && !text.is_empty() && text.len() <= MAX_TEXT && text_is_valid(text),
        "蓝牙文本长度无效"
    );
    ensure!((20..=512).contains(&mtu), "蓝牙报文长度无效");
    let mut result = VecDeque::new();
    for (index, chunk) in text.as_bytes().chunks(mtu - HEADER).enumerate() {
        let mut bytes = header(1, id, text.len() as u16, (index * (mtu - HEADER)) as u16);
        bytes.extend_from_slice(chunk);
        result.push_back(bytes);
    }
    Ok(result)
}
fn decode(bytes: &[u8], kind: u8) -> Result<(u64, usize, usize)> {
    ensure!(
        bytes.len() >= HEADER && bytes[..4] == [b'V', b'M', 2, kind],
        "蓝牙报文无效"
    );
    let id = u64::from_le_bytes(bytes[4..12].try_into().unwrap());
    ensure!(id != 0, "蓝牙消息编号无效");
    Ok((
        id,
        u16::from_le_bytes(bytes[12..14].try_into().unwrap()) as usize,
        u16::from_le_bytes(bytes[14..16].try_into().unwrap()) as usize,
    ))
}
pub fn receipt(id: u64, submitted: bool) -> Vec<u8> {
    header(2, id, u16::from(submitted), 0)
}
pub fn read_receipt(bytes: &[u8]) -> Result<(u64, bool)> {
    let (id, status, offset) = decode(bytes, 2)?;
    ensure!(
        bytes.len() == HEADER && status <= 1 && offset == 0,
        "蓝牙确认报文无效"
    );
    Ok((id, status == 1))
}
impl Receiver {
    /// Receipts are scoped to a trusted peer, while an ordinary reconnect to
    /// that same peer keeps them. IDs alone are not a cross-device identity.
    pub fn reset_peer(&mut self) {
        *self = Self::default();
    }
    pub fn reset_connection(&mut self) {
        self.active = None;
    }
    pub fn completed(&self, id: u64) -> Option<bool> {
        self.receipts
            .iter()
            .find(|(seen, _)| *seen == id)
            .map(|(_, result)| *result)
    }
    pub fn finish(&mut self, id: u64, submitted: bool) {
        if self.pending == Some(id) {
            self.pending = None;
        }
        if self.completed(id).is_none() {
            if self.receipts.len() == MAX_RECEIPTS {
                self.receipts.pop_front();
            }
            self.receipts.push_back((id, submitted));
        }
    }
    pub fn expire(&mut self, now: Instant) -> bool {
        if self
            .active
            .as_ref()
            .is_some_and(|a| now.duration_since(a.started) >= TRANSFER_TIMEOUT)
        {
            self.active = None;
            true
        } else {
            false
        }
    }
    pub fn accept(&mut self, bytes: &[u8], now: Instant) -> Result<Received> {
        self.expire(now);
        let result = self.accept_inner(bytes, now);
        if result.is_err() {
            self.active = None;
        }
        result
    }
    fn accept_inner(&mut self, bytes: &[u8], now: Instant) -> Result<Received> {
        let (id, total, offset) = decode(bytes, 1)?;
        ensure!(
            bytes.len() <= 512 && bytes.len() > HEADER && (1..=MAX_TEXT).contains(&total),
            "蓝牙分片长度无效"
        );
        if self.completed(id).is_some() || self.pending == Some(id) {
            return Ok(Received::Duplicate(id));
        }
        ensure!(self.pending.is_none(), "接收端仍在输入上一条文本");
        if self.active.is_none() {
            ensure!(offset == 0, "蓝牙首个分片缺失");
            self.active = Some(Assembly {
                id,
                total,
                bytes: Vec::with_capacity(total),
                started: now,
            });
        }
        let active = self.active.as_mut().unwrap();
        ensure!(
            active.id == id && active.total == total && active.bytes.len() == offset,
            "蓝牙分片乱序"
        );
        ensure!(offset + bytes.len() - HEADER <= total, "蓝牙分片溢出");
        active.bytes.extend_from_slice(&bytes[HEADER..]);
        if active.bytes.len() != total {
            return Ok(Received::Partial);
        }
        let complete = self.active.take().unwrap();
        let text =
            String::from_utf8(complete.bytes).map_err(|_| anyhow::anyhow!("蓝牙文本编码无效"))?;
        ensure!(text_is_valid(&text), "蓝牙文本包含无效字符");
        self.pending = Some(id);
        Ok(Received::Text(id, text))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn receipt_is_retained_for_reconnect_but_not_reused_for_another_peer() {
        let packet = frames(17, "fixed fixture", 512)
            .unwrap()
            .pop_front()
            .unwrap();
        let mut receiver = Receiver::default();
        assert!(matches!(
            receiver.accept(&packet, Instant::now()).unwrap(),
            Received::Text(17, _)
        ));
        receiver.finish(17, true);
        receiver.reset_connection();
        assert_eq!(
            receiver.accept(&packet, Instant::now()).unwrap(),
            Received::Duplicate(17)
        );
        receiver.reset_peer();
        assert!(matches!(
            receiver.accept(&packet, Instant::now()).unwrap(),
            Received::Text(17, _)
        ));
    }
    #[test]
    fn unicode_round_trip_at_minimum_and_large_mtu() {
        let text = "离线测试🙂，Bluetooth text only。".repeat(200);
        for mtu in [20, 23, 64, 185, 512] {
            let packets = frames(17, &text, mtu).unwrap();
            assert!(packets.iter().all(|p| p.len() <= mtu));
            let mut receiver = Receiver::default();
            let mut output = None;
            for packet in packets {
                if let Received::Text(id, value) = receiver.accept(&packet, Instant::now()).unwrap()
                {
                    output = Some((id, value));
                }
            }
            assert_eq!(output, Some((17, text.clone())));
        }
    }
    #[test]
    fn invalid_or_missing_frames_never_publish_partial_text() {
        let now = Instant::now();
        let packets: Vec<_> = frames(4, "abcdef🙂", 20).unwrap().into();
        let mut receiver = Receiver::default();
        assert!(receiver.accept(&packets[1], now).is_err());
        assert_eq!(
            receiver.accept(&packets[0], now).unwrap(),
            Received::Partial
        );
        assert!(receiver.accept(&packets[0], now).is_err());
        assert!(receiver.accept(&packets[1], now).is_err());
        assert_eq!(
            receiver.accept(&packets[0], now).unwrap(),
            Received::Partial
        );
        assert!(receiver.expire(now + TRANSFER_TIMEOUT));
        assert!(
            receiver
                .accept(&packets[1], now + TRANSFER_TIMEOUT)
                .is_err()
        );
    }
    #[test]
    fn receipt_replay_survives_connection_reset_without_reinsertion() {
        let packet = frames(33, "fixed fixture", 512)
            .unwrap()
            .pop_front()
            .unwrap();
        let mut receiver = Receiver::default();
        assert!(matches!(
            receiver.accept(&packet, Instant::now()).unwrap(),
            Received::Text(33, _)
        ));
        receiver.finish(33, true);
        receiver.reset_connection();
        assert_eq!(
            receiver.accept(&packet, Instant::now()).unwrap(),
            Received::Duplicate(33)
        );
        assert_eq!(read_receipt(&receipt(33, true)).unwrap(), (33, true));
        assert_eq!(read_receipt(&receipt(33, false)).unwrap(), (33, false));
        assert_eq!(receiver.completed(33), Some(true));
    }
    #[test]
    fn limits_and_invalid_encoding_reject_before_delivery() {
        for text in [String::new(), "x".repeat(MAX_TEXT + 1), "x\0y".into()] {
            assert!(frames(1, &text, 512).is_err());
        }
        assert!(frames(0, "x", 512).is_err());
        assert!(frames(1, "x", HEADER).is_err());
        let mut receiver = Receiver::default();
        let mut packet = header(1, 2, 1, 0);
        packet.push(255);
        assert!(receiver.accept(&packet, Instant::now()).is_err());
        for kind in [0, 3, 255] {
            let mut packet = header(kind, 2, 1, 0);
            packet.push(b'x');
            assert!(receiver.accept(&packet, Instant::now()).is_err());
        }
        let mut packet = receipt(2, true);
        packet.push(0);
        assert!(read_receipt(&packet).is_err());
    }
}
