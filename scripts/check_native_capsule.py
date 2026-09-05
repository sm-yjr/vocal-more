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
                width, height = (400, 200) if expanded else (240, 80)
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

    renderer._cancel_button.performClick_(None)
    renderer._finish_button.performClick_(None)
    assert actions == ["cancel", "finish"]
    renderer.set_state("processing")
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
