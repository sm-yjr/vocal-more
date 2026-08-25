"""Tests for long-dictation streaming segment paste (paste while recording)."""

from __future__ import annotations

import time
from types import SimpleNamespace

import yaml


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _build_streaming_mode(
    tmp_path,
    monkeypatch,
    config_payload,
    *,
    stop_text="",
    stop_gate=None,
    paste_error_once=False,
):
    """Build a RealtimeLongMode wired to fakes.

    ``stop_gate`` is a (entered_event, release_event) tuple making
    FakeASREngine.stop() block until released, so tests can emit finals
    while the finish workflow is waiting on ASR finalization.
    """
    from vocal_more.config import Config, reload_config
    from vocal_more.modes import realtime_long as module

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))
    with open(config_path, "w") as f:
        yaml.dump(config_payload, f)
    reload_config()

    observed = {
        "pasted": [],
        "results": [],
        "errors": [],
        "polish_calls": [],
    }
    asr_instances = []
    paste_state = {"failed_once": False}

    class FakeASREngine:
        def __init__(
            self,
            on_partial_result=None,
            on_final_result=None,
            on_error=None,
        ):
            self.on_partial_result = on_partial_result
            self.on_final_result = on_final_result
            self.on_error = on_error
            asr_instances.append(self)

        def start(self, **_kwargs):
            return None

        def send_audio(self, _chunk):
            return None

        def stop(self, pcm_data=None):
            if stop_gate is not None:
                entered, release = stop_gate
                entered.set()
                assert release.wait(timeout=2.0)
            return stop_text

        def emit_final(self, text):
            assert self.on_final_result is not None
            self.on_final_result(SimpleNamespace(text=text, is_final=True))

    class FakeRecorder:
        def __init__(self, on_audio_level=None, on_audio_chunk=None):
            self.on_audio_chunk = on_audio_chunk

        def start(self):
            return None

        def stop(self):
            return b"\x01\x00" * 4000

    class FakeKeyboard:
        def paste_text(self, text):
            if paste_error_once and not paste_state["failed_once"]:
                paste_state["failed_once"] = True
                raise RuntimeError("accessibility unavailable")
            observed["pasted"].append(text)

    class FakePolisher:
        def polish(self, text):
            observed["polish_calls"].append(text)
            return SimpleNamespace(polished_text=text, billing=None)

    monkeypatch.setattr(module, "ASREngine", FakeASREngine)
    monkeypatch.setattr(module, "AudioRecorder", FakeRecorder)
    monkeypatch.setattr(module, "KeyboardSimulator", lambda: FakeKeyboard())

    mode = module.RealtimeLongMode(
        on_result=observed["results"].append,
        on_error=observed["errors"].append,
        text_polisher=FakePolisher(),
    )
    return mode, observed, asr_instances


_STREAMING_CONFIG = {
    "streaming_paste": True,
    "auto_paste": True,
    "enable_polish": True,
    # Non-inline-polish model: segments are raw transcripts.
    "asr": {"model": "qwen3-asr-flash-realtime-2026-02-10"},
}


def test_streaming_paste_pastes_segments_then_only_tail(tmp_path, monkeypatch):
    """Finalized segments paste during recording; finish pastes only the tail."""
    mode, observed, asr_instances = _build_streaming_mode(
        tmp_path,
        monkeypatch,
        dict(_STREAMING_CONFIG),
        stop_text="第一段。第二段。第三段。",
    )

    mode.on_hotkey_pressed()
    asr = asr_instances[0]
    asr.emit_final("第一段。")
    asr.emit_final("第二段。")
    assert _wait_until(lambda: len(observed["pasted"]) == 2)
    assert observed["pasted"] == ["第一段。", " 第二段。"]

    mode.on_hotkey_pressed()  # stop recording
    mode._processing_thread.join(timeout=2)

    # The tail paste is the only finish-time paste; no duplicates.
    assert observed["pasted"] == ["第一段。", " 第二段。", " 第三段。"]
    # Streaming sessions skip second-stage polish entirely.
    assert observed["polish_calls"] == []
    # Result/history still carry the complete aggregated text.
    assert observed["results"] == ["第一段。第二段。第三段。"]
    assert observed["errors"] == []
    mode.close()


def test_streaming_paste_tail_aligns_ignoring_whitespace(tmp_path, monkeypatch):
    """Mode-level separators must not break finish-time prefix alignment."""
    mode, observed, asr_instances = _build_streaming_mode(
        tmp_path,
        monkeypatch,
        dict(_STREAMING_CONFIG),
        # The engine accumulates segments by direct concatenation, with no
        # separator between them.
        stop_text="Hello world.Next part.",
    )

    mode.on_hotkey_pressed()
    asr = asr_instances[0]
    asr.emit_final("Hello world.")
    assert _wait_until(lambda: len(observed["pasted"]) == 1)

    mode.on_hotkey_pressed()
    mode._processing_thread.join(timeout=2)

    assert observed["pasted"] == ["Hello world.", " Next part."]
    assert observed["errors"] == []
    mode.close()


def test_streaming_paste_no_tail_pasties_nothing_extra(tmp_path, monkeypatch):
    """When every segment was already streamed, finish must not paste again."""
    mode, observed, asr_instances = _build_streaming_mode(
        tmp_path,
        monkeypatch,
        dict(_STREAMING_CONFIG),
        stop_text="第一段。第二段。",
    )

    mode.on_hotkey_pressed()
    asr = asr_instances[0]
    asr.emit_final("第一段。")
    asr.emit_final("第二段。")
    assert _wait_until(lambda: len(observed["pasted"]) == 2)

    mode.on_hotkey_pressed()
    mode._processing_thread.join(timeout=2)

    assert observed["pasted"] == ["第一段。", " 第二段。"]
    assert observed["results"] == ["第一段。第二段。"]
    assert observed["errors"] == []
    mode.close()


def test_streaming_paste_drops_segments_after_cancel(tmp_path, monkeypatch):
    """Rule 4: finals arriving after token invalidation must not paste."""
    mode, observed, asr_instances = _build_streaming_mode(
        tmp_path,
        monkeypatch,
        dict(_STREAMING_CONFIG),
        stop_text="第一段。",
    )

    mode.on_hotkey_pressed()
    asr = asr_instances[0]
    asr.emit_final("第一段。")
    assert _wait_until(lambda: len(observed["pasted"]) == 1)

    mode.cancel(reason="user_cancel")
    asr.emit_final("取消后到达的分段")

    # Give any (incorrectly scheduled) paste a chance to run.
    mode._processing_executor.close(wait=True)
    assert observed["pasted"] == ["第一段。"]
    mode.close()


def test_streaming_paste_late_segment_during_stop_not_duplicated(
    tmp_path, monkeypatch
):
    """A final landing while finish runs is covered by the tail, not re-pasted."""
    import threading

    entered = threading.Event()
    release = threading.Event()
    mode, observed, asr_instances = _build_streaming_mode(
        tmp_path,
        monkeypatch,
        dict(_STREAMING_CONFIG),
        stop_text="正常段。晚到段。",
        stop_gate=(entered, release),
    )

    mode.on_hotkey_pressed()
    asr = asr_instances[0]
    asr.emit_final("正常段。")
    assert _wait_until(lambda: len(observed["pasted"]) == 1)

    mode.on_hotkey_pressed()  # stop; finish blocks inside asr.stop()
    assert entered.wait(timeout=2.0)
    # Arrives after the finish task was queued: its paste task must drop
    # itself, while the tail paste still delivers the text exactly once.
    asr.emit_final("晚到段。")
    release.set()
    mode._processing_thread.join(timeout=2)

    assert observed["pasted"] == ["正常段。", " 晚到段。"]
    assert observed["errors"] == []
    mode.close()


def test_streaming_paste_disabled_when_switch_off(tmp_path, monkeypatch):
    """With streaming_paste off the original one-shot path must be unchanged."""
    config = dict(_STREAMING_CONFIG)
    config["streaming_paste"] = False
    mode, observed, asr_instances = _build_streaming_mode(
        tmp_path,
        monkeypatch,
        config,
        stop_text="完整文本",
    )

    mode.on_hotkey_pressed()
    asr = asr_instances[0]
    asr.emit_final("完整文本")
    # No segment paste while recording.
    time.sleep(0.05)
    assert observed["pasted"] == []

    mode.on_hotkey_pressed()
    mode._processing_thread.join(timeout=2)

    assert observed["pasted"] == ["完整文本"]
    assert observed["results"] == ["完整文本"]
    mode.close()


def test_streaming_paste_requires_auto_paste(tmp_path, monkeypatch):
    config = dict(_STREAMING_CONFIG)
    config["auto_paste"] = False
    mode, observed, asr_instances = _build_streaming_mode(
        tmp_path,
        monkeypatch,
        config,
        stop_text="第一段。",
    )

    mode.on_hotkey_pressed()
    asr_instances[0].emit_final("第一段。")
    time.sleep(0.05)
    assert observed["pasted"] == []

    mode.on_hotkey_pressed()
    mode._processing_thread.join(timeout=2)

    assert observed["pasted"] == []
    assert observed["results"] == ["第一段。"]
    mode.close()


def test_streaming_paste_disabled_for_inline_polish_model(tmp_path, monkeypatch):
    """Inline-polish models emit polished segments; streaming paste must skip."""
    config = dict(_STREAMING_CONFIG)
    config["asr"] = {"model": "qwen3.5-omni-flash-realtime"}
    mode, observed, asr_instances = _build_streaming_mode(
        tmp_path,
        monkeypatch,
        config,
        stop_text="润色后的完整文本",
    )

    mode.on_hotkey_pressed()
    asr_instances[0].emit_final("润色后的分段")
    time.sleep(0.05)
    assert observed["pasted"] == []

    mode.on_hotkey_pressed()
    mode._processing_thread.join(timeout=2)

    assert observed["pasted"] == ["润色后的完整文本"]
    mode.close()


def test_streaming_paste_disabled_for_command_intent(tmp_path, monkeypatch):
    """COMMAND intent keeps the command workflow; no segment paste happens."""
    from vocal_more.domain.input_intent import InputIntent

    config = dict(_STREAMING_CONFIG)
    # Command mode needs an Omni model; those are inline-polish too, so both
    # guards independently keep command sessions on the original path.
    config["asr"] = {"model": "qwen3.5-omni-flash-realtime"}
    mode, observed, asr_instances = _build_streaming_mode(
        tmp_path,
        monkeypatch,
        config,
        stop_text="命令结果",
    )

    mode.on_hotkey_pressed(intent=InputIntent.COMMAND)
    asr_instances[0].emit_final("命令分段")
    time.sleep(0.05)
    assert observed["pasted"] == []

    mode.cancel(reason="test_cleanup")
    assert observed["pasted"] == []
    mode.close()


def test_streaming_paste_failure_falls_back_to_full_finish(
    tmp_path, monkeypatch
):
    """If a segment paste fails, nothing is streamed and finish pastes all."""
    mode, observed, asr_instances = _build_streaming_mode(
        tmp_path,
        monkeypatch,
        dict(_STREAMING_CONFIG),
        stop_text="第一段。第二段。",
        paste_error_once=True,
    )

    mode.on_hotkey_pressed()
    asr = asr_instances[0]
    # First paste raises; streaming stops and no raw parts are recorded.
    asr.emit_final("第一段。")
    assert _wait_until(lambda: not mode._streaming_paste_active)
    asr.emit_final("第二段。")
    time.sleep(0.05)
    assert observed["pasted"] == []

    mode.on_hotkey_pressed()
    mode._processing_thread.join(timeout=2)

    # Nothing reached the app during recording, so the normal full-text
    # finish path applies (including polish eligibility).
    assert observed["pasted"] == ["第一段。第二段。"]
    assert observed["polish_calls"] == ["第一段。第二段。"]
    assert observed["errors"] == []
    mode.close()
