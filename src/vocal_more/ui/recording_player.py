"""File-backed macOS history playback, owned by the settings main thread."""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path

from AVFoundation import AVPlayer
from CoreMedia import CMTimeGetSeconds
from Foundation import NSURL, NSRunLoop, NSRunLoopCommonModes, NSTimer


class RecordingPlayer:
    """Let AVFoundation stream WAV/FLAC instead of copying audio through JS."""

    def __init__(self, on_stopped: Callable[[str], None]) -> None:
        self._on_stopped = on_stopped
        self._player = None
        self._timer = None
        self._recording_id: str | None = None

    def play(self, recording_id: str, path: Path) -> bool:
        self.stop()
        if not path.is_file():
            return False
        self._player = AVPlayer.playerWithURL_(NSURL.fileURLWithPath_(str(path)))
        self._recording_id = recording_id
        self._player.play()
        self._timer = NSTimer.timerWithTimeInterval_repeats_block_(
            0.25, True, lambda _: self._check_finished()
        )
        NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, NSRunLoopCommonModes)
        return True

    def _check_finished(self) -> None:
        player = self._player
        if player is None:
            return
        item = player.currentItem()
        if player.error() is not None or item.error() is not None:
            self.stop()
            return
        duration = CMTimeGetSeconds(item.duration())
        position = CMTimeGetSeconds(player.currentTime())
        if math.isfinite(duration) and duration >= 0 and position >= duration:
            self.stop()

    def stop(self, recording_id: str | None = None) -> None:
        if recording_id is not None and recording_id != self._recording_id:
            return
        previous_id = self._recording_id
        self._recording_id = None
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        if self._player is not None:
            self._player.pause()
            self._player.replaceCurrentItemWithPlayerItem_(None)
            self._player = None
        if previous_id is not None:
            self._on_stopped(previous_id)
