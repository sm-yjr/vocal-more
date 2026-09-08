"""Exercise real AppKit geometry and rendering without microphone/network access.

Run with: uv run python scripts/check_native_capsule.py [--snapshot-dir PATH]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from AppKit import NSApplication, NSBitmapImageFileTypePNG, NSPanel
from Foundation import NSDate, NSRunLoop

from vocal_more.ui.native_capsule_view import NativeCapsuleRenderer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", type=Path)
    args = parser.parse_args()
    NSApplication.sharedApplication()
    actions = []
    renderer = NativeCapsuleRenderer(
        width=240, height=80,
        on_cancel=lambda: actions.append("cancel"),
        on_finish=lambda: actions.append("finish"),
    )

    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        ((50, 50), (240, 80)), 128, 2, False
    )
    panel.setContentView_(renderer.view)
    if args.snapshot_dir:
        panel.orderFront_(None)

    def snapshot(name):
        if args.snapshot_dir:
            args.snapshot_dir.mkdir(parents=True, exist_ok=True)
            panel.orderFront_(None)
            panel.display()
            NSRunLoop.mainRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
            view = renderer.view
            bitmap = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
            view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), bitmap)
            data = bitmap.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
            data.writeToFile_atomically_(str(args.snapshot_dir / f"{name}.png"), True)

    cases = 0
    for language in ("zh", "en"):
        for mode in ("pushToTalk", "handsFree", "prompt", "promptPushToTalk", "command", "meeting"):
            renderer.set_interface_language(language)
            renderer.set_mode(mode)
            renderer.set_state("recording")
            for expanded in (False, True):
                width, height = (360, 200) if expanded else (240, 80)
                panel.setFrame_display_(((50, 50), (width, height)), True)
                renderer.set_container_size(width, height)
                renderer.set_expanded(expanded)
                renderer.set_streaming_text("说明文字 / Explain the task" if expanded else "")
                surface = renderer._surface.frame()
                assert surface.origin.x >= 0
                assert surface.origin.x + surface.size.width <= renderer._width
                label = renderer._recording_label
                if not label.isHidden():
                    assert label.frame().size.width >= label.intrinsicContentSize().width
                    if not renderer._cancel_button.isHidden():
                        button = renderer._cancel_button.frame()
                        assert label.frame().origin.x >= button.origin.x + button.size.width + 4
                for bar in renderer._waveform:
                    if not bar.isHidden():
                        frame = bar.frame()
                        assert frame.origin.x >= 0
                        assert frame.origin.x + frame.size.width <= surface.size.width
                        if not renderer._finish_button.isHidden():
                            assert frame.origin.x + frame.size.width <= renderer._finish_button.frame().origin.x - 4
                for _ in range(20):
                    renderer.set_audio_level(0.7)
                snapshot(f"{language}-{mode}-{'expanded' if expanded else 'compact'}")
                cases += 1
            renderer.set_state("hidden")
            assert renderer._surface.alphaValue() == 0.0

    # Exercise the real panel owner: grow only as wrapped transcript needs it.
    from vocal_more.ui.floating_capsule import FloatingCapsule
    capsule = FloatingCapsule()
    capsule._panel = panel
    capsule._renderer = renderer
    capsule._current_state = "recording"
    capsule._current_mode = "handsFree"
    renderer.set_mode("handsFree")
    renderer.set_state("recording")
    samples = (
        ("one-line", "再者呢，你看。"),
        ("two-lines", "第一行短内容\n第二行短内容"),
        ("wrapped", "你看一下现在这个波形，它的长度还是不够长，跟这个窗口的长度不成正比。再者呢，只有几个字的时候就展开了全部窗口。希望窗口可以随着文字增加，逐行展开，并始终保留清楚的波形。"),
        ("overflow", "长文本😀 English\n" * 100 + "LATEST END"),
    )
    heights = []
    for name, text in samples:
        capsule._update_streaming_text_on_main_thread(text)
        height = panel.frame().size.height
        heights.append(height)
        layout = renderer._streaming_label.layoutManager()
        container = renderer._streaming_label.textContainer()
        layout.ensureLayoutForTextContainer_(container)
        if name != "overflow":
            used = layout.usedRectForTextContainer_(container).size.height
            assert used <= renderer._streaming_scroll.contentSize().height + 1
        assert panel.frame().size.width == capsule.RECORDING_CAPSULE_WIDTH
        assert height == renderer.preferred_container_height(text, capsule.RECORDING_CAPSULE_WIDTH)
        surface = renderer._surface.frame()
        assert surface.origin.y + surface.size.height <= height
        visible_bars = [bar.frame() for bar in renderer._waveform if not bar.isHidden()]
        span = visible_bars[-1].origin.x + visible_bars[-1].size.width - visible_bars[0].origin.x
        assert span >= surface.size.width * .70
        for _ in range(20):
            renderer.set_audio_level(.7)
        snapshot(name)
    assert heights[0] < heights[1] < heights[2] <= heights[3] == 200
    assert heights[0] < 105 and heights[1] < 125
    # Corrections can shorten a partial. Shrink again without moving its base.
    base_y = panel.frame().origin.y
    capsule._update_streaming_text_on_main_thread(samples[0][1])
    assert panel.frame().size.height == heights[0]
    assert panel.frame().origin.y == base_y
    capsule._update_streaming_text_on_main_thread("")
    assert panel.frame().size.height == capsule.CAPSULE_HEIGHT

    from vocal_more.domain.connection_status import ConnectionStatus
    for mode in ("handsFree", "pushToTalk", "prompt", "promptPushToTalk"):
        capsule._current_state = "recording"
        capsule._current_mode = mode
        capsule._interface_language = "zh"
        renderer.set_mode(mode)
        renderer.set_state("recording")
        notice = ConnectionStatus("retrying", "连接超时：无法连接 dashscope.aliyuncs.com", retry=3, delay=4)
        capsule._show_connection_status_on_main_thread(notice)
        assert panel.frame().size.width == capsule.HINT_CAPSULE_WIDTH
        assert not panel.ignoresMouseEvents()
        assert not renderer._cancel_button.isHidden()
        assert renderer._finish_button.isHidden()
        assert all(bar.isHidden() for bar in renderer._waveform)
        assert "3/5" in str(renderer._streaming_label.string())
        snapshot(f"connection-retry-{mode}")
        capsule._update_state_on_main_thread("processing")
        assert renderer._state == "connection_error"
        capsule._show_connection_status_on_main_thread(ConnectionStatus("ready"))
        assert renderer._state == "processing"
        failed = ConnectionStatus("failed", "403：没有该模型的访问权限", retry=5)
        capsule._show_connection_status_on_main_thread(failed)
        capsule._update_state_on_main_thread("hidden")
        assert renderer._state == "connection_error"
        assert not renderer._cancel_button.isHidden()
        snapshot(f"connection-failed-{mode}")
        capsule._hide_on_main_thread()
        capsule._show_connection_status_on_main_thread(notice)
        assert capsule._current_state == "hidden"
        assert renderer._state == "hidden"
        if capsule._hide_timer:
            capsule._hide_timer.invalidate()
            capsule._hide_timer = None

    renderer._cancel_button.performClick_(None)
    renderer._finish_button.performClick_(None)
    assert actions == ["cancel", "finish"]
    renderer.set_state("processing")
    panel.setFrame_display_(((50, 50), (400, 200)), True)
    renderer.set_container_size(400, 200)
    renderer.set_expanded(True)
    for stage in ("transcribing", "polishing", "understanding", "searching", "generating", "meeting_transcribing", "meeting_summarizing"):
        renderer.set_processing_stage(stage)
        assert renderer._thinking_label.intrinsicContentSize().width <= renderer._thinking_label.frame().size.width
    renderer.set_streaming_text("长文本😀 English\n" * 600 + "LATEST END")
    clip = renderer._streaming_scroll.contentView().bounds()
    document = renderer._streaming_label.frame()
    assert clip.origin.y > 0, "Long text must scroll to its latest output"
    assert abs(clip.origin.y + clip.size.height - document.size.height) < 2
    assert str(renderer._streaming_label.string()).endswith("LATEST END")
    snapshot("long-streaming")
    for _ in range(500):
        active = renderer.advance_progress()
    assert not active
    assert 0.89 < renderer._progress <= 0.9

    renderer.set_state("hidden")
    renderer.set_state("recording")
    assert not renderer._is_expanded()
    assert renderer._stage == "transcribing"
    assert not any(renderer._smoothed_levels)
    assert all(bar.frame().size.height == 2 for bar in renderer._waveform)
    renderer.set_expanded(True)
    renderer.set_streaming_text("benchmark")
    start = time.perf_counter()
    for index in range(3000):
        renderer.set_audio_level((index % 60) / 60)
    waveform_ms = (time.perf_counter() - start) * 1000 / 3000
    start = time.perf_counter()
    for index in range(1000):
        renderer.set_streaming_text("流式文字 " * 100 + str(index))
    streaming_ms = (time.perf_counter() - start) * 1000 / 1000
    panel.orderOut_(None)
    print(json.dumps({"layout_cases": cases, "waveform_ms_per_call": waveform_ms,
                      "streaming_ms_per_call": streaming_ms, "checks": "passed"}))


if __name__ == "__main__":
    main()
