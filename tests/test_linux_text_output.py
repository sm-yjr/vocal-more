from __future__ import annotations

import threading

from vocal_more.linux_text_output import LinuxTextOutputAdapter


def test_linux_text_output_waits_for_shell_confirmation():
    clipboard = []
    adapter = None

    def request(request_id: int) -> None:
        threading.Timer(0.01, adapter.complete_paste, args=(request_id, True)).start()

    adapter = LinuxTextOutputAdapter(
        write_clipboard=lambda text, _timeout: clipboard.append(text) or True,
        request_paste=request,
        paste_timeout=0.2,
    )

    outcome = adapter.paste_text("private dictated text")

    assert outcome.success is True
    assert clipboard == ["private dictated text"]


def test_linux_text_output_keeps_clipboard_on_injection_failure():
    clipboard = []
    adapter = None

    def request(request_id: int) -> None:
        adapter.complete_paste(request_id, False, "virtual keyboard unavailable")

    adapter = LinuxTextOutputAdapter(
        write_clipboard=lambda text, _timeout: clipboard.append(text) or True,
        request_paste=request,
    )

    outcome = adapter.paste_text("kept on clipboard")

    assert outcome.success is False
    assert outcome.error == "virtual keyboard unavailable"
    assert clipboard == ["kept on clipboard"]


def test_linux_text_output_times_out_without_marking_success():
    adapter = LinuxTextOutputAdapter(
        write_clipboard=lambda _text, _timeout: True,
        request_paste=lambda _request_id: None,
        paste_timeout=0.01,
    )

    outcome = adapter.paste_text("text")

    assert outcome.success is False
    assert "confirmation timed out" in outcome.error


def test_linux_text_output_close_unblocks_pending_request():
    requested = threading.Event()
    adapter = LinuxTextOutputAdapter(
        write_clipboard=lambda _text, _timeout: True,
        request_paste=lambda _request_id: requested.set(),
        paste_timeout=2,
    )
    result = []
    worker = threading.Thread(target=lambda: result.append(adapter.paste_text("text")))
    worker.start()
    assert requested.wait(0.2)

    adapter.close()
    worker.join(0.2)

    assert result and result[0].success is False
    assert "shutting down" in result[0].error
