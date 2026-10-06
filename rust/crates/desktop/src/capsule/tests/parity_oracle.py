"""Compare Rust capsule pixels/geometry with the shipping Python AppKit source.

This is test tooling only; the Rust desktop does not load Python. Run from the
repository root with ``uv run --with pillow python .../parity_oracle.py``.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from unittest.mock import patch

from AppKit import NSApplication, NSBitmapImageFileTypePNG, NSPanel
from Foundation import NSDate, NSRunLoop

from vocal_more.domain.connection_status import ConnectionStatus
from vocal_more.ui.floating_capsule import FloatingCapsule
from vocal_more.ui.native_capsule_view import NativeCapsuleRenderer


def frame(value):
    return dict(x=value.origin.x, y=value.origin.y,
                width=value.size.width, height=value.size.height)


def metadata(panel, renderer):
    clip = renderer._streaming_scroll.contentView().bounds()
    count = renderer._active_bar_count()
    layout = dict(
        surface=frame(renderer._surface.frame()),
        cancel=frame(renderer._cancel_button.frame()),
        finish=frame(renderer._finish_button.frame()),
        recording_label=frame(renderer._recording_label.frame()),
        thinking_label=frame(renderer._thinking_label.frame()),
        progress_track=frame(renderer._progress_track.frame()),
        progress_fill=frame(renderer._progress_fill.frame()),
        streaming=frame(renderer._streaming_scroll.frame()),
        bars=[frame(bar.frame()) for bar in renderer._waveform[:count]],
        cancel_visible=not renderer._cancel_button.isHidden(),
        finish_visible=not renderer._finish_button.isHidden(),
        recording_label_visible=not renderer._recording_label.isHidden(),
        thinking_label_visible=not renderer._thinking_label.isHidden(),
        progress_visible=not renderer._progress_track.isHidden(),
        streaming_visible=not renderer._streaming_scroll.isHidden(),
        waveform_visible=renderer._state == "recording",
    )
    return dict(
        panel=frame(panel.frame()), state=renderer._state, mode=renderer._mode,
        language=renderer._language, stage=renderer._stage,
        surface_alpha=renderer._surface.alphaValue(),
        ignores_mouse_events=panel.ignoresMouseEvents(),
        can_become_key=panel.canBecomeKeyWindow(),
        can_become_main=panel.canBecomeMainWindow(),
        recording_label=str(renderer._recording_label.stringValue()),
        thinking_label=str(renderer._thinking_label.stringValue()),
        visible_text=str(renderer._streaming_label.string()),
        scroll_x=clip.origin.x, scroll_y=clip.origin.y, scroll_width=clip.size.width, scroll_height=clip.size.height,
        document_width=renderer._streaming_label.frame().size.width,
        document_height=renderer._streaming_label.frame().size.height,
        progress=renderer._progress, layout=layout,
    )


def generate(directory):
    NSApplication.sharedApplication()
    directory.mkdir(parents=True, exist_ok=True)
    actions = []
    renderer = NativeCapsuleRenderer(width=240, height=80,
                                    on_cancel=lambda: actions.append("cancel"),
                                    on_finish=lambda: actions.append("finish"))
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        ((50, 50), (240, 80)), 128, 2, False)
    panel.setBackgroundColor_( __import__("AppKit").NSColor.clearColor())
    panel.setOpaque_(False)
    panel.setHasShadow_(False)
    panel.setContentView_(renderer.view)
    capsule = FloatingCapsule()
    capsule._panel = panel
    capsule._renderer = renderer
    capsule._config_provider = lambda: __import__("types").SimpleNamespace(
        enable_polish=False, llm=__import__("types").SimpleNamespace(polish_mode="dictation"))
    output = {}

    def capture(name):
        # Fixtures supply exact animation timestamps. Do not let a real timer
        # advance one implementation during the backing-layer commit below.
        capsule._stop_push_timer()
        capsule._stop_progress_timer()
        panel.orderFront_(None)
        panel.setFrameOrigin_((50, 50))
        panel.display()
        NSRunLoop.mainRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
        view = renderer.view
        bitmap = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
        view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), bitmap)
        data = bitmap.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
        data.writeToFile_atomically_(str(directory / f"{name}.png"), True)
        output[name] = metadata(panel, renderer)

    def waveform(reduce_motion=False):
        renderer._phase = 0.0
        renderer._smoothed_levels = [0.0] * renderer.NUM_BARS
        renderer._reduce_motion = reduce_motion
        renderer._last_waveform_tick = 0.0
        for index in range(20):
            with patch("vocal_more.ui.native_capsule_view.time.monotonic",
                       return_value=(index + 1) / 120.0):
                renderer.set_audio_level(0.7)

    for language in ("zh", "en"):
        capsule._set_interface_language_on_main_thread(language)
        for mode in ("pushToTalk", "handsFree", "prompt", "promptPushToTalk", "command", "meeting"):
            capsule._show_on_main_thread(mode, prompt_mode=False)
            renderer.set_streaming_text("")
            for expanded in (False, True):
                width, height = (360, 200) if expanded else (240, 80)
                panel.setFrame_display_(((50, 50), (width, height)), True)
                renderer.set_container_size(width, height)
                renderer.set_expanded(expanded)
                renderer.set_streaming_text("说明文字 / Explain the task" if expanded else "")
                waveform()
                capture(f"{language}-{mode}-{'expanded' if expanded else 'compact'}")
            capsule._hide_on_main_thread()

    capsule._set_interface_language_on_main_thread("zh")
    capsule._show_on_main_thread("handsFree", prompt_mode=False)
    samples = (
        ("one-line", "再者呢，你看。"),
        ("two-lines", "第一行短内容\n第二行短内容"),
        ("wrapped", "你看一下现在这个波形，它的长度还是不够长，跟这个窗口的长度不成正比。再者呢，只有几个字的时候就展开了全部窗口。希望窗口可以随着文字增加，逐行展开，并始终保留清楚的波形。"),
        ("overflow", "长文本😀 English\n" * 100 + "LATEST END"),
    )
    for name, text in samples:
        capsule._update_streaming_text_on_main_thread(text)
        waveform()
        capture(name)
    capsule._update_streaming_text_on_main_thread(samples[0][1])
    capsule._update_streaming_text_on_main_thread("")
    for mode in ("handsFree", "pushToTalk", "prompt", "promptPushToTalk"):
        capsule._show_on_main_thread(mode, prompt_mode=False)
        capsule._show_connection_status_on_main_thread(ConnectionStatus(
            "retrying", "连接超时：无法连接 dashscope.aliyuncs.com", retry=3, delay=4))
        capture(f"connection-retry-{mode}")
        capsule._update_state_on_main_thread("processing")
        capsule._show_connection_status_on_main_thread(ConnectionStatus("ready"))
        capsule._show_connection_status_on_main_thread(ConnectionStatus(
            "failed", "403：没有该模型的访问权限", retry=5))
        capsule._update_state_on_main_thread("hidden")
        capsule._show_failure_on_main_thread("generic failure")
        capture(f"connection-failed-{mode}")
        capsule._hide_on_main_thread()
    capsule._show_on_main_thread("handsFree", prompt_mode=False)
    capsule._update_state_on_main_thread("processing")
    capsule._show_failure_on_main_thread("识别服务返回 403")
    capsule._update_state_on_main_thread("hidden")
    capsule._show_connection_status_on_main_thread(ConnectionStatus("retrying", retry=3, delay=4))
    capture("failure-notice")
    capsule._clear_failure_notice()
    capsule._hide_on_main_thread()
    capsule._show_on_main_thread("handsFree", prompt_mode=False)
    capsule._update_state_on_main_thread("processing")
    panel.setFrame_display_(((50, 50), (400, 200)), True)
    renderer.set_container_size(400, 200)
    renderer.set_expanded(True)
    for stage in ("transcribing", "polishing", "understanding", "searching", "generating", "meeting_transcribing", "meeting_summarizing"):
        renderer.set_processing_stage(stage)
    renderer.set_streaming_text("长文本😀 English\n" * 600 + "LATEST END")
    capture("long-streaming")
    def progress(ticks):
        renderer._progress = 0.0
        renderer._last_progress_tick = 0.0
        for index in range(ticks):
            with patch("vocal_more.ui.native_capsule_view.time.monotonic",
                       return_value=(index + 1) / 120.0):
                renderer.advance_progress()

    for language in ("zh", "en"):
        capsule._set_interface_language_on_main_thread(language)
        for stage in ("transcribing", "polishing", "understanding", "searching", "generating", "meeting_transcribing", "meeting_summarizing"):
            capsule._show_on_main_thread("handsFree", prompt_mode=False)
            capsule._update_state_on_main_thread("processing")
            renderer.set_processing_stage(stage)
            progress(20)
            capture(f"processing-{language}-{stage}")
        progress(500)
        capture(f"progress-asymptote-{language}")
        capsule._show_on_main_thread("handsFree", prompt_mode=False)
        capsule._update_streaming_text_on_main_thread("说明文字 / Explain the task")
        panel.setFrame_display_(((50, 50), (360, 200)), True)
        renderer.set_container_size(360, 200)
        renderer.set_expanded(True)
        waveform(reduce_motion=True)
        capture(f"reduce-motion-{language}")
        for mode in ("pushToTalk", "handsFree"):
            capsule._hide_on_main_thread()
            capsule._show_on_main_thread(mode, prompt_mode=True)
            capture(f"prompt-coach-{language}-{mode}")
    capsule._hide_on_main_thread()
    capsule._show_on_main_thread("handsFree", prompt_mode=False)
    renderer._cancel_button.performClick_(None)
    renderer._finish_button.performClick_(None)
    assert actions == ["cancel", "finish"]
    panel.orderOut_(None)
    (directory / "fixtures.json").write_text(json.dumps(output, ensure_ascii=False, indent=2))
    return output


def compare(reference, candidate):
    from PIL import Image, ImageChops, ImageStat
    expected = json.loads((reference / "fixtures.json").read_text())
    actual = json.loads((candidate / "fixtures.json").read_text())
    assert set(expected) == set(actual), "fixture set differs"
    differences = []
    inactive_scroll_differences = 0

    def equal(left, right, path):
        if isinstance(left, dict):
            assert set(left) == set(right), f"{path}: metadata keys differ"
            for key in left:
                equal(left[key], right[key], f"{path}.{key}")
        elif isinstance(left, list):
            assert len(left) == len(right), f"{path}: list length differs"
            for index, (a, b) in enumerate(zip(left, right)):
                equal(a, b, f"{path}[{index}]")
        elif isinstance(left, (int, float)) and not isinstance(left, bool):
            assert math.isclose(left, right, abs_tol=1e-9), f"{path}: {left} != {right}"
        else:
            assert left == right, f"{path}: {left!r} != {right!r}"

    for name in expected:
        left, right = dict(expected[name]), dict(actual[name])
        if not left["layout"]["streaming_visible"]:
            # AppKit's SDK compatibility behavior may clamp a hidden clip view
            # at a different Y while its old document remains retained. The
            # subsequent visible range is checked strictly in expanded cases.
            inactive_scroll_differences += left["scroll_y"] != right["scroll_y"]
            del left["scroll_y"], right["scroll_y"]
        equal(left, right, name)
        a = Image.open(reference / f"{name}.png").convert("RGBA")
        b = Image.open(candidate / f"{name}.png").convert("RGBA")
        assert a.size == b.size, f"{name}: bitmap size differs"
        diff = ImageChops.difference(a, b)
        maximum = max(high for low, high in diff.getextrema())
        if maximum:
            rmse = math.sqrt(sum(value * value for value in ImageStat.Stat(diff).rms) / 4)
            differences.append(dict(fixture=name, max_channel_difference=maximum, rmse=rmse))
            diff.save(candidate / f"{name}-difference.png")
    result = dict(layout_cases=24, snapshot_cases=len(expected),
                  metadata="visible geometry identical", pixels="identical" if not differences else "different",
                  inactive_text_scroll_y_differences=inactive_scroll_differences,
                  differences=differences)
    print(json.dumps(result, ensure_ascii=False))
    assert not differences, "Rust/Python capsule pixels differ; inspect difference PNGs"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--compare-only", action="store_true")
    args = parser.parse_args()
    if not args.compare_only:
        generate(args.reference)
    if args.candidate:
        compare(args.reference, args.candidate)


if __name__ == "__main__":
    main()
