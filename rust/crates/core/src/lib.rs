// SPDX-License-Identifier: GPL-3.0-only
pub mod audio;
pub mod diagnostics;
pub mod protocol;
pub mod recording;
pub mod runtime;

pub const SAMPLE_RATE: u32 = 16_000;
pub const BLOCK_FRAMES: usize = 640;
pub const BLOCK_BYTES: usize = BLOCK_FRAMES * 2;
pub const NETWORK_FRAME_BYTES: usize = 3_200;
pub const AUDIO_QUEUE_BLOCKS: usize = 160;
