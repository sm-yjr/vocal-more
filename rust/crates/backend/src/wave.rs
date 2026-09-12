// SPDX-License-Identifier: GPL-3.0-only
//! File-based, bounded chunking with the existing 250 ms / 12 s silence policy.
use anyhow::{Result, ensure};
use std::{
    fs::File,
    io::{BufReader, Cursor},
    path::Path,
};

pub struct WaveChunks {
    reader: hound::WavReader<BufReader<File>>,
    next_frame: u32,
    max_frames: usize,
}
impl WaveChunks {
    pub fn open(path: &Path) -> Result<Self> {
        Self::with_duration(path, 180)
    }
    pub fn with_duration(path: &Path, seconds: u32) -> Result<Self> {
        ensure!(
            (1..=180).contains(&seconds),
            "chunk duration must be 1–180 seconds"
        );
        let reader = hound::WavReader::open(path)?;
        let spec = reader.spec();
        ensure!(
            spec.sample_rate == 16000
                && spec.channels == 1
                && spec.bits_per_sample == 16
                && spec.sample_format == hound::SampleFormat::Int,
            "recording must use 16 kHz mono PCM16"
        );
        Ok(Self {
            reader,
            next_frame: 0,
            max_frames: seconds as usize * 16000,
        })
    }
    pub fn duration(&self) -> f64 {
        self.reader.duration() as f64 / 16000.0
    }
    pub fn next_chunk(&mut self) -> Result<Option<Vec<u8>>> {
        if self.next_frame >= self.reader.duration() {
            return Ok(None);
        }
        self.reader.seek(self.next_frame)?;
        let remaining = self.reader.duration() - self.next_frame;
        let samples: Vec<i16> = self
            .reader
            .samples::<i16>()
            .take(self.max_frames)
            .collect::<Result<_, _>>()?;
        let count = if remaining as usize > self.max_frames {
            silence_end(&samples)
        } else {
            samples.len()
        };
        ensure!(count > 0, "WAV ended before its declared frame count");
        self.next_frame += count as u32;
        Ok(Some(
            samples[..count]
                .iter()
                .flat_map(|s| s.to_le_bytes())
                .collect(),
        ))
    }
}

fn silence_end(samples: &[i16]) -> usize {
    let window = 4000;
    if samples.len() <= window {
        return samples.len();
    }
    let begin = window.max(samples.len().saturating_sub(12 * 16000));
    let mut best = samples.len();
    let mut best_rms = f64::INFINITY;
    let mut end = samples.len();
    while end >= begin {
        let window = &samples[end.saturating_sub(window)..end];
        let rms = (window.iter().map(|s| (*s as f64).powi(2)).sum::<f64>() / window.len() as f64)
            .sqrt()
            / 32767.0;
        if rms < best_rms {
            best_rms = rms;
            best = end;
        }
        if rms <= 0.015 {
            return end;
        }
        if end < 4000 {
            break;
        }
        end -= 4000;
    }
    best
}

pub fn wav_bytes(pcm: &[u8]) -> Result<Vec<u8>> {
    ensure!(pcm.len().is_multiple_of(2), "incomplete PCM frame");
    let mut cursor = Cursor::new(Vec::with_capacity(pcm.len() + 44));
    let mut writer = hound::WavWriter::new(
        &mut cursor,
        hound::WavSpec {
            channels: 1,
            sample_rate: 16000,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        },
    )?;
    for p in pcm.chunks_exact(2) {
        writer.write_sample(i16::from_le_bytes([p[0], p[1]]))?;
    }
    writer.finalize()?;
    Ok(cursor.into_inner())
}

pub fn join_segments(segments: &[String]) -> String {
    let mut output = String::new();
    for segment in segments.iter().map(|s| s.trim()).filter(|s| !s.is_empty()) {
        if !output.is_empty()
            && !output.ends_with(['。', '！', '？', '!', '?', '\n'])
            && !segment.starts_with([
                '，', '。', '！', '？', '、', '；', '：', ',', '.', '!', '?', ';', ':',
            ])
        {
            output.push('\n');
        }
        output.push_str(segment);
    }
    output
}
