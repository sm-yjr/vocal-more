// SPDX-License-Identifier: GPL-3.0-only
//! The backend and AX adapter each own one claimed paste. Keep the entire
//! claim/capture/prepare/deliver transaction serial, including asynchronous AX.
use std::collections::VecDeque;

pub const PASTE_QUEUE_CAPACITY: usize = 64;
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Delivery {
    pub token: String,
    pub epoch: u64,
    pub generation: u64,
}
#[derive(Default)]
pub struct PasteLane {
    active: Option<Delivery>,
    queued: VecDeque<Delivery>,
}
impl PasteLane {
    pub fn enqueue(&mut self, delivery: Delivery) -> Result<(), &'static str> {
        if self
            .active
            .as_ref()
            .is_some_and(|active| active.token == delivery.token)
            || self
                .queued
                .iter()
                .any(|queued| queued.token == delivery.token)
        {
            return Ok(());
        }
        if self.queued.len() >= PASTE_QUEUE_CAPACITY {
            return Err("Paste delivery queue is full");
        }
        self.queued.push_back(delivery);
        Ok(())
    }
    pub fn next(&mut self, epoch: u64, generation: u64) -> Option<Delivery> {
        if self.active.is_some() {
            return None;
        }
        while let Some(delivery) = self.queued.pop_front() {
            if delivery.epoch == epoch && delivery.generation == generation {
                self.active = Some(delivery.clone());
                return Some(delivery);
            }
        }
        None
    }
    pub fn complete(&mut self, delivery: &Delivery) {
        if self.active.as_ref() == Some(delivery) {
            self.active = None;
        }
    }
    pub fn clear(&mut self) {
        self.active = None;
        self.queued.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn job(token: &str, epoch: u64, generation: u64) -> Delivery {
        Delivery {
            token: token.into(),
            epoch,
            generation,
        }
    }
    #[test]
    fn asynchronous_capture_preserves_single_claim_and_fifo() {
        let mut lane = PasteLane::default();
        lane.enqueue(job("first", 1, 7)).unwrap();
        let first = lane.next(1, 7).unwrap();
        lane.enqueue(job("second", 1, 7)).unwrap();
        lane.enqueue(job("third", 1, 7)).unwrap();
        assert_eq!(lane.next(1, 7), None);
        lane.complete(&first);
        let second = lane.next(1, 7).unwrap();
        assert_eq!(second.token, "second");
        lane.complete(&first); // A delayed duplicate cannot complete second.
        assert_eq!(lane.next(1, 7), None);
        lane.complete(&second);
        assert_eq!(lane.next(1, 7).unwrap().token, "third");
    }
    #[test]
    fn cancellation_isolates_queued_and_late_active_work() {
        let mut lane = PasteLane::default();
        lane.enqueue(job("old", 1, 7)).unwrap();
        let old = lane.next(1, 7).unwrap();
        lane.clear();
        lane.enqueue(job("new", 3, 8)).unwrap();
        let new = lane.next(3, 8).unwrap();
        lane.complete(&old);
        assert_eq!(lane.next(3, 8), None);
        lane.complete(&new);
        lane.enqueue(job("late-old", 1, 7)).unwrap();
        assert_eq!(lane.next(3, 8), None);
    }
    #[test]
    fn repeated_resync_is_deduplicated_and_overflow_is_bounded() {
        let mut lane = PasteLane::default();
        for _ in 0..100 {
            lane.enqueue(job("one", 1, 7)).unwrap();
        }
        assert_eq!(lane.queued.len(), 1);
        for index in 1..PASTE_QUEUE_CAPACITY {
            lane.enqueue(job(&index.to_string(), 1, 7)).unwrap();
        }
        assert!(lane.enqueue(job("overflow", 1, 7)).is_err());
        assert_eq!(lane.queued.len(), PASTE_QUEUE_CAPACITY);
    }
}
