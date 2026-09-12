// SPDX-License-Identifier: GPL-3.0-only
//! Opt-in phase timings; never log audio, text, paths or credentials.
use std::{sync::LazyLock, time::Instant};

static TIMINGS: LazyLock<bool> =
    LazyLock::new(|| std::env::var_os("VOCAL_MORE_TRACE_TIMINGS").is_some());

pub struct Timing(&'static str, Option<Instant>);
impl Timing {
    pub fn new(name: &'static str) -> Self {
        Self(name, (*TIMINGS).then(Instant::now))
    }
}
impl Drop for Timing {
    fn drop(&mut self) {
        if let Some(started) = self.1 {
            eprintln!(
                "[perf] {} ms={:.3}",
                self.0,
                started.elapsed().as_secs_f64() * 1000.0
            );
        }
    }
}
