// SPDX-License-Identifier: GPL-3.0-only
//! User-selected portal/PipeWire capture. One owned lane, no pixels on disk.
use anyhow::{Context, Result, ensure};
use ashpd::desktop::{
    PersistMode,
    screencast::{CursorMode, Screencast, SelectSourcesOptions, SourceType},
};
use futures_lite::StreamExt;
use gstreamer as gst;
use gstreamer_video::{VideoCropMeta, VideoFrameRef, VideoInfo, prelude::*};
use image::{DynamicImage, codecs::jpeg::JpegEncoder};
use std::{
    os::fd::AsRawFd,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::Duration,
};
use tokio::sync::Notify;

pub const MAX_JPEG_BYTES: usize = 190 * 1024;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Key {
    pub request: u64,
    pub epoch: u64,
}
pub struct Frame {
    pub key: Key,
    pub jpeg: Result<Vec<u8>>,
}
struct Start {
    key: Key,
    serial: u64,
}
struct Shared {
    serial: AtomicU64,
    closed: AtomicBool,
    changed: Notify,
    finished: AtomicBool,
}
pub struct ScreenWorker {
    jobs: async_channel::Sender<Start>,
    pending: async_channel::Receiver<Start>,
    frames: async_channel::Receiver<Frame>,
    shared: Arc<Shared>,
}
impl ScreenWorker {
    pub fn new() -> Result<Self> {
        let (jobs, pending) = async_channel::bounded::<Start>(1);
        let (output, frames) = async_channel::bounded(2);
        let receiver = pending.clone();
        let shared = Arc::new(Shared {
            serial: AtomicU64::new(0),
            closed: AtomicBool::new(false),
            changed: Notify::new(),
            finished: AtomicBool::new(false),
        });
        let state = shared.clone();
        std::thread::Builder::new()
            .name("vocal-more-linux-screen".into())
            .spawn(move || {
                let runtime = tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build();
                if let Ok(runtime) = runtime {
                    runtime.block_on(async {
                        let mut restore = None;
                        while let Ok(job) = receiver.recv().await {
                            if state.closed.load(Ordering::Acquire) {
                                break;
                            }
                            if state.serial.load(Ordering::Acquire) != job.serial {
                                continue;
                            }
                            let result = capture(&job, &state, &output, &mut restore).await;
                            if let Err(error) = result
                                && state.serial.load(Ordering::Acquire) == job.serial
                                && !state.closed.load(Ordering::Acquire)
                            {
                                let _ = output.try_send(Frame {
                                    key: job.key,
                                    jpeg: Err(error),
                                });
                            }
                        }
                    });
                }
                state.finished.store(true, Ordering::Release);
            })?;
        Ok(Self {
            jobs,
            pending,
            frames,
            shared,
        })
    }
    pub fn start(&self, key: Key) -> Result<()> {
        let serial = self.shared.serial.fetch_add(1, Ordering::AcqRel) + 1;
        self.shared.changed.notify_waiters();
        while self.pending.try_recv().is_ok() {}
        self.jobs
            .try_send(Start { key, serial })
            .context("screen capture queue is unavailable")
    }
    pub fn stop(&self) {
        self.shared.serial.fetch_add(1, Ordering::AcqRel);
        self.shared.changed.notify_waiters();
        while self.pending.try_recv().is_ok() {}
    }
    pub fn poll(&self) -> Option<Frame> {
        self.frames.try_recv().ok()
    }
    pub fn close(&self) {
        self.shared.closed.store(true, Ordering::Release);
        self.stop();
        self.jobs.close();
    }
    pub fn finished(&self) -> bool {
        self.shared.finished.load(Ordering::Acquire)
    }
}
impl Drop for ScreenWorker {
    fn drop(&mut self) {
        self.close();
    }
}

struct Pipeline(gst::Pipeline);
impl Drop for Pipeline {
    fn drop(&mut self) {
        let _ = self.0.set_state(gst::State::Null);
    }
}
async fn capture(
    job: &Start,
    shared: &Shared,
    frames: &async_channel::Sender<Frame>,
    restore: &mut Option<String>,
) -> Result<()> {
    gst::init().context("GStreamer initialization failed")?;
    let changed = shared.changed.notified();
    tokio::pin!(changed);
    changed.as_mut().enable();
    if shared.serial.load(Ordering::Acquire) != job.serial {
        return Ok(());
    }
    let portal = tokio::time::timeout(Duration::from_secs(3), Screencast::new()).await??;
    let session = tokio::time::timeout(
        Duration::from_secs(3),
        portal.create_session(Default::default()),
    )
    .await??;
    let mut closed = session.receive_closed().await?;
    let operation = async {
        let previous_token = restore.take();
        portal
            .select_sources(
                &session,
                SelectSourcesOptions::default()
                    .set_sources(SourceType::Monitor | SourceType::Window)
                    .set_multiple(false)
                    .set_cursor_mode(CursorMode::Hidden)
                    .set_persist_mode(PersistMode::Application)
                    .set_restore_token(previous_token.as_deref()),
            )
            .await?
            .response()?;
        let response = portal
            .start(&session, None, Default::default())
            .await?
            .response()?;
        *restore = response.restore_token().map(str::to_owned);
        let stream = response.streams().first().context("没有选择共享画面")?;
        let remote = portal
            .open_pipe_wire_remote(&session, Default::default())
            .await?;
        let pipeline = gst::parse::launch(&format!(
            "pipewiresrc fd={} path={} do-timestamp=true ! videorate drop-only=true ! video/x-raw,framerate=1/2 ! videoconvert ! video/x-raw,format=RGB ! appsink name=frames max-buffers=1 drop=true sync=false",
            remote.as_raw_fd(),stream.pipe_wire_node_id()))?.downcast::<gst::Pipeline>().map_err(|_| anyhow::anyhow!("invalid screen pipeline"))?;
        let pipeline = Pipeline(pipeline);
        let sink = pipeline
            .0
            .by_name("frames")
            .context("screen sample sink missing")?
            .downcast::<gstreamer_app::AppSink>()
            .map_err(|_| anyhow::anyhow!("invalid sample sink"))?;
        pipeline.0.set_state(gst::State::Playing)?;
        while shared.serial.load(Ordering::Acquire) == job.serial
            && !shared.closed.load(Ordering::Acquire)
        {
            if let Some(sample) = sink.try_pull_sample(gst::ClockTime::from_mseconds(30)) {
                let encoded = encode_sample(&sample)?;
                if shared.serial.load(Ordering::Acquire) == job.serial {
                    let _ = frames.try_send(Frame {
                        key: job.key,
                        jpeg: Ok(encoded),
                    });
                }
            }
            if let Some(bus) = pipeline.0.bus()
                && let Some(message) =
                    bus.pop_filtered(&[gst::MessageType::Error, gst::MessageType::Eos])
            {
                anyhow::bail!("屏幕共享已结束：{:?}", message.type_());
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        Ok::<_, anyhow::Error>(())
    };
    let result = tokio::select! {
        biased;
        _ = &mut changed => Ok(()),
        result = operation => result,
        _ = closed.next() => Ok(()),
    };
    let _ = tokio::time::timeout(Duration::from_millis(500), session.close()).await;
    result
}

fn encode_sample(sample: &gst::Sample) -> Result<Vec<u8>> {
    let info = VideoInfo::from_caps(sample.caps().context("screen frame has no caps")?)?;
    ensure!(
        info.format() == gstreamer_video::VideoFormat::Rgb,
        "unexpected screen pixel format"
    );
    let buffer = sample.buffer().context("screen frame has no buffer")?;
    let frame = VideoFrameRef::from_buffer_ref_readable(buffer, &info)?;
    let crop = buffer.meta::<VideoCropMeta>().map(|m| m.rect()).unwrap_or((
        0,
        0,
        info.width(),
        info.height(),
    ));
    #[cfg(test)]
    eprintln!(
        "screen sample canvas={}x{} crop={crop:?} stride={}",
        info.width(),
        info.height(),
        frame.plane_stride()[0]
    );
    let image = copy_region(
        frame.plane_data(0)?,
        frame.plane_stride()[0],
        (info.width(), info.height()),
        crop,
    )?;
    encode_image(&image)
}
/// PipeWire window streams can use a larger backing buffer with VideoCropMeta.
/// Copy only the authorized region, respecting row padding before JPEG encoding.
fn copy_region(
    plane: &[u8],
    stride: i32,
    canvas: (u32, u32),
    crop: (u32, u32, u32, u32),
) -> Result<DynamicImage> {
    let (x, y, width, height) = crop;
    ensure!(
        canvas.0 > 0 && canvas.1 > 0 && canvas.0 <= 8192 && canvas.1 <= 8192,
        "screen canvas exceeds limits"
    );
    ensure!(
        width > 0
            && height > 0
            && x.checked_add(width).is_some_and(|v| v <= canvas.0)
            && y.checked_add(height).is_some_and(|v| v <= canvas.1),
        "invalid screen crop"
    );
    ensure!(
        stride > 0 && stride as usize >= canvas.0 as usize * 3,
        "invalid screen row stride"
    );
    let row_bytes = width as usize * 3;
    let size = row_bytes * height as usize;
    ensure!(
        size <= 64 * 1024 * 1024,
        "screen region exceeds allocation limit"
    );
    let mut pixels = Vec::with_capacity(size);
    for row in y..y + height {
        let begin = (row as usize)
            .checked_mul(stride as usize)
            .and_then(|n| n.checked_add(x as usize * 3))
            .context("screen offset overflow")?;
        let end = begin
            .checked_add(row_bytes)
            .context("screen row overflow")?;
        pixels.extend_from_slice(plane.get(begin..end).context("truncated screen plane")?);
    }
    Ok(DynamicImage::ImageRgb8(
        image::RgbImage::from_raw(width, height, pixels).context("invalid RGB screen")?,
    ))
}
fn encode_image(image: &DynamicImage) -> Result<Vec<u8>> {
    ensure!(
        image.width() > 0 && image.height() > 0 && image.width() <= 8192 && image.height() <= 8192,
        "invalid screen dimensions"
    );
    for width in [1280, 1024, 800] {
        let frame = image
            .thumbnail(width.min(image.width()), 720.min(image.height()))
            .to_rgb8();
        for quality in [72, 55, 38] {
            let mut bytes = Vec::new();
            JpegEncoder::new_with_quality(&mut bytes, quality).encode_image(&frame)?;
            if bytes.len() <= MAX_JPEG_BYTES {
                return Ok(bytes);
            }
        }
    }
    anyhow::bail!("屏幕帧无法压缩到模型限制内")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn screen_frames_are_bounded_jpeg_without_upscaling() {
        let image = DynamicImage::new_rgb8(2560, 1440);
        let bytes = encode_image(&image).unwrap();
        assert!(bytes.starts_with(&[255, 216, 255]) && bytes.len() <= MAX_JPEG_BYTES);
        let decoded = image::load_from_memory(&bytes).unwrap();
        assert_eq!((decoded.width(), decoded.height()), (1280, 720));
        let small = DynamicImage::new_rgb8(320, 200);
        let small = image::load_from_memory(&encode_image(&small).unwrap()).unwrap();
        assert_eq!((small.width(), small.height()), (320, 200));
    }
    #[test]
    fn raw_gstreamer_crop_metadata_reaches_jpeg_encoder() {
        gst::init().unwrap();
        let info = VideoInfo::builder(gstreamer_video::VideoFormat::Rgb, 5, 4)
            .build()
            .unwrap();
        let mut buffer = gst::Buffer::with_size(info.size()).unwrap();
        {
            let mut map = buffer.get_mut().unwrap().map_writable().unwrap();
            map.as_mut_slice().fill(255);
            let stride = info.stride()[0] as usize;
            for y in 1..3 {
                for x in 1..3 {
                    map.as_mut_slice()[y * stride + x * 3..y * stride + x * 3 + 3]
                        .copy_from_slice(&[20, 58, 128]);
                }
            }
        }
        VideoCropMeta::add(buffer.get_mut().unwrap(), (1, 1, 2, 2));
        let caps = info.to_caps().unwrap();
        let sample = gst::Sample::builder().buffer(&buffer).caps(&caps).build();
        let jpeg = encode_sample(&sample).unwrap();
        let decoded = image::load_from_memory(&jpeg).unwrap().to_rgb8();
        assert_eq!((decoded.width(), decoded.height()), (2, 2));
        assert!(
            decoded
                .pixels()
                .all(|pixel| pixel.0[2] > 100 && pixel.0[0] < 40)
        );
    }
    #[test]
    fn crop_metadata_removes_backing_padding_and_respects_stride() {
        let mut plane = vec![255u8; 4 * 16];
        for y in 1..3 {
            for x in 1..3 {
                plane[y * 16 + x * 3..y * 16 + x * 3 + 3].copy_from_slice(&[20, 58, 128]);
            }
        }
        let region = copy_region(&plane, 16, (4, 4), (1, 1, 2, 2))
            .unwrap()
            .to_rgb8();
        assert_eq!((region.width(), region.height()), (2, 2));
        assert!(region.pixels().all(|p| p.0 == [20, 58, 128]));
        assert!(copy_region(&plane, 16, (4, 4), (3, 3, 2, 2)).is_err());
        assert!(copy_region(&plane[..10], 16, (4, 4), (1, 1, 2, 2)).is_err());
        assert!(copy_region(&plane, -16, (4, 4), (1, 1, 2, 2)).is_err());
        assert!(copy_region(&plane, 16, (4, 4), (u32::MAX, 0, 1, 1)).is_err());
    }
}
