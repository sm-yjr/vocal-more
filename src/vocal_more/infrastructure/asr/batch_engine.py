"""Pure helpers used by the batch ASR compatibility facade."""

from __future__ import annotations

import math
from array import array


def frame_bytes(channels: int) -> int:
    return max(1, channels * 2)


def audio_bytes_per_second(sample_rate: int, channels: int) -> int:
    return sample_rate * frame_bytes(channels)


def audio_duration_seconds(audio_data: bytes, sample_rate: int, channels: int) -> float:
    bytes_per_second = audio_bytes_per_second(sample_rate, channels)
    return (len(audio_data) / bytes_per_second) if bytes_per_second else 0.0


def analysis_window_frames(sample_rate: int, window_seconds: float) -> int:
    return max(1, int(sample_rate * window_seconds))


def silence_search_frames(sample_rate: int, search_seconds: float) -> int:
    return max(1, int(sample_rate * search_seconds))


def window_rms(
    samples: memoryview,
    *,
    channels: int,
    frame_start: int,
    frame_end: int,
) -> float:
    channel_count = max(1, channels)
    sample_start = frame_start * channel_count
    sample_end = frame_end * channel_count
    if sample_end <= sample_start:
        return float("inf")

    energy_sum = 0.0
    for sample in samples[sample_start:sample_end]:
        energy_sum += float(sample) * float(sample)

    return math.sqrt(energy_sum / (sample_end - sample_start)) / 32767.0


def find_silence_aware_chunk_end(
    samples: memoryview,
    *,
    sample_rate: int,
    channels: int,
    silence_window_seconds: float,
    silence_search_seconds: float,
    silence_rms_threshold: float,
    start_frame: int,
    target_end_frame: int,
    total_frames: int,
) -> int:
    window_frames = analysis_window_frames(sample_rate, silence_window_seconds)
    if target_end_frame >= total_frames:
        return total_frames
    if target_end_frame - start_frame <= window_frames:
        return target_end_frame

    search_start_frame = max(
        start_frame + window_frames,
        target_end_frame - silence_search_frames(sample_rate, silence_search_seconds),
    )
    best_end_frame = target_end_frame
    best_rms = float("inf")

    for candidate_end in range(
        target_end_frame,
        search_start_frame - 1,
        -window_frames,
    ):
        candidate_start = max(start_frame, candidate_end - window_frames)
        rms = window_rms(
            samples,
            channels=channels,
            frame_start=candidate_start,
            frame_end=candidate_end,
        )
        if rms < best_rms:
            best_rms = rms
            best_end_frame = candidate_end
        if rms <= silence_rms_threshold:
            return candidate_end

    return best_end_frame


def split_audio_for_batch(
    audio_data: bytes,
    *,
    sample_rate: int,
    channels: int,
    max_duration_seconds: int,
    silence_window_seconds: float,
    silence_search_seconds: float,
    silence_rms_threshold: float,
) -> list[bytes]:
    bytes_per_second = audio_bytes_per_second(sample_rate, channels)
    if bytes_per_second <= 0:
        return [audio_data]

    chunk_frame_bytes = frame_bytes(channels)
    chunk_bytes = max(bytes_per_second * max_duration_seconds, chunk_frame_bytes)
    chunk_bytes -= chunk_bytes % chunk_frame_bytes
    total_frames = len(audio_data) // chunk_frame_bytes
    max_chunk_frames = max(1, chunk_bytes // chunk_frame_bytes)
    if chunk_bytes <= 0 or total_frames <= max_chunk_frames:
        return [audio_data]

    try:
        sample_buffer = array("h")
        sample_buffer.frombytes(audio_data)
        samples = memoryview(sample_buffer)
    except Exception:
        return [
            audio_data[offset:offset + chunk_bytes]
            for offset in range(0, len(audio_data), chunk_bytes)
        ]

    chunks: list[bytes] = []
    start_frame = 0
    while start_frame < total_frames:
        remaining_frames = total_frames - start_frame
        if remaining_frames <= max_chunk_frames:
            end_frame = total_frames
        else:
            target_end_frame = start_frame + max_chunk_frames
            end_frame = find_silence_aware_chunk_end(
                samples,
                sample_rate=sample_rate,
                channels=channels,
                silence_window_seconds=silence_window_seconds,
                silence_search_seconds=silence_search_seconds,
                silence_rms_threshold=silence_rms_threshold,
                start_frame=start_frame,
                target_end_frame=target_end_frame,
                total_frames=total_frames,
            )
            if end_frame <= start_frame:
                end_frame = target_end_frame

        start_byte = start_frame * chunk_frame_bytes
        end_byte = end_frame * chunk_frame_bytes
        chunks.append(audio_data[start_byte:end_byte])
        start_frame = end_frame

    return chunks


__all__ = [
    "analysis_window_frames",
    "audio_bytes_per_second",
    "audio_duration_seconds",
    "find_silence_aware_chunk_end",
    "frame_bytes",
    "silence_search_frames",
    "split_audio_for_batch",
    "window_rms",
]

