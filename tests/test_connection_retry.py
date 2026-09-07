"""Connection failures remain cancellable and have exactly five backoffs."""

from unittest.mock import MagicMock
import threading
import time

import pytest

from vocal_more.domain.connection_status import ConnectionStatus


def test_connection_retries_five_times_then_fails_without_batch(monkeypatch):
    from vocal_more.core import asr_engine as module
    engine = module.ASREngine()
    jobs, notices, delays = [], [], []
    connect = MagicMock(side_effect=ConnectionError('DNS lookup failed'))
    monkeypatch.setattr(engine, '_start_connect_thread', jobs.append)
    monkeypatch.setattr(engine, '_establish_conversation', connect)
    monkeypatch.setattr(engine, '_transcribe_batch_fallback', MagicMock())
    engine.set_connection_observer(notices.append)
    try:
        engine.start()
        actual_wait = engine._connect_done.wait
        engine._connect_done.wait = lambda timeout: delays.append(timeout) or False
        jobs[0]()
        engine._connect_done.wait = actual_wait
        assert connect.call_count == 6
        assert delays == [1, 2, 4, 8, 16]
        assert [n.retry for n in notices if n.phase == 'retrying'] == [1, 2, 3, 4, 5]
        assert notices[-1].phase == 'failed'
        assert 'DNS lookup failed' in notices[-1].error
        with pytest.raises(ConnectionError, match='DNS lookup failed'):
            engine.stop(pcm_data=b'\0\0' * 4000)
        engine._transcribe_batch_fallback.assert_not_called()
    finally:
        engine.close()


def test_cancel_interrupts_backoff_and_prevents_another_attempt(monkeypatch):
    from vocal_more.core import asr_engine as module
    engine = module.ASREngine()
    entered = threading.Event()
    notices, jobs = [], []
    connect = MagicMock(side_effect=ConnectionError('connection refused'))
    monkeypatch.setattr(engine, '_start_connect_thread', jobs.append)
    monkeypatch.setattr(engine, '_establish_conversation', connect)
    def observe(status):
        notices.append(status)
        if status.phase == 'retrying':
            entered.set()
    engine.set_connection_observer(observe)
    engine.start()
    worker = threading.Thread(target=jobs[0])
    worker.start()
    try:
        assert entered.wait(1)
        before = time.monotonic()
        engine.abort_startup()
        worker.join(.5)
        assert not worker.is_alive()
        assert time.monotonic() - before < .5
        assert connect.call_count == 1
        assert not any(n.phase in {'ready', 'failed'} for n in notices)
    finally:
        engine.close()
        worker.join(1)


def test_server_error_is_preserved_during_session_handshake():
    from vocal_more.core.asr_engine import ASREngine, StreamingASRCallback
    callback = StreamingASRCallback()
    try:
        callback.recognition_error('403: model access denied')
        callback._flush_inbound_events()
        with pytest.raises(ConnectionError, match='403: model access denied'):
            ASREngine._wait_for_session_updated(callback, timeout=.1)
    finally:
        callback.close()


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_connection_message_includes_error_and_retry_limit(language):
    status = ConnectionStatus('retrying', 'TLS handshake failed', retry=3, delay=4)
    title, detail = status.display_text(language)
    assert title
    assert 'TLS handshake failed' in detail
    assert '3/5' in detail
    assert '4' in detail
    assert '×' in detail


def test_success_after_backoff_restores_ready_state(monkeypatch):
    from vocal_more.core import asr_engine as module
    engine = module.ASREngine()
    jobs, notices, delays = [], [], []
    candidate = MagicMock()
    connect = MagicMock(side_effect=[ConnectionError('timeout'), candidate])
    monkeypatch.setattr(engine, '_start_connect_thread', jobs.append)
    monkeypatch.setattr(engine, '_establish_conversation', connect)
    engine.set_connection_observer(notices.append)
    try:
        engine.start()
        actual_wait = engine._connect_done.wait
        engine._connect_done.wait = lambda timeout: delays.append(timeout) or False
        jobs[0]()
        engine._connect_done.wait = actual_wait
        assert connect.call_count == 2
        assert delays == [1]
        assert notices[-1].phase == 'ready'
        assert engine._session_ready
        assert not engine._connect_failed
    finally:
        engine.close()


def test_late_connection_cannot_restore_capsule_after_cancel(monkeypatch):
    from vocal_more.core import asr_engine as module
    engine = module.ASREngine()
    jobs, notices = [], []
    entered, release = threading.Event(), threading.Event()
    candidate = MagicMock()
    def connect(*args, **kwargs):
        entered.set()
        release.wait(2)
        return candidate
    monkeypatch.setattr(engine, '_start_connect_thread', jobs.append)
    monkeypatch.setattr(engine, '_establish_conversation', connect)
    engine.set_connection_observer(notices.append)
    engine.start()
    worker = threading.Thread(target=jobs[0])
    worker.start()
    try:
        assert entered.wait(1)
        engine.abort_startup()
        release.set()
        worker.join(1)
        assert not worker.is_alive()
        assert not any(n.phase == 'ready' for n in notices)
        assert engine._conversation is None
    finally:
        release.set()
        engine.close()
        worker.join(1)


@pytest.mark.parametrize('mode_name', ['walkie_talkie', 'realtime_long'])
def test_mode_cancel_during_retry_aborts_before_waiting_for_asr_stop(mode_name, monkeypatch):
    import importlib
    from vocal_more.modes.base_mode import ModeState
    module = importlib.import_module(f'vocal_more.modes.{mode_name}')
    mode_class = module.WalkieTalkieMode if mode_name == 'walkie_talkie' else module.RealtimeLongMode
    mode = mode_class()
    events = []
    asr = MagicMock()
    asr.abort_startup.side_effect = lambda: events.append('abort')
    asr.stop.side_effect = lambda: events.append('stop')
    mode._asr = asr
    mode._recorder = MagicMock()
    mode._active_session_token = mode._begin_session()
    mode._state = ModeState.RECORDING
    mode._connection_status = ConnectionStatus('retrying', 'DNS failed', retry=1, delay=1)
    try:
        mode.cancel()
        assert events == ['abort', 'stop']
        assert mode.state == ModeState.IDLE
        assert mode.prewarm_asr() is False
        asr.prepare_idle_session.assert_not_called()
        stale = ConnectionStatus('ready')
        mode._on_connection_update(mode._active_session_token - 1, stale)
        assert mode.connection_status is not stale
    finally:
        mode.close()


def test_cancel_wakes_pending_finish_without_batch_or_result(monkeypatch):
    from vocal_more.core import asr_engine as module
    engine = module.ASREngine()
    jobs, results = [], []
    monkeypatch.setattr(engine, '_start_connect_thread', jobs.append)
    batch = MagicMock()
    monkeypatch.setattr(engine, '_transcribe_batch_fallback', batch)
    engine.start()
    entered = threading.Event()
    real_wait = engine._connect_done.wait
    def wait(timeout):
        entered.set()
        return real_wait(timeout)
    engine._connect_done.wait = wait
    worker = threading.Thread(target=lambda: results.append(engine.stop(pcm_data=b'\0\0' * 4000)))
    worker.start()
    try:
        assert entered.wait(1)
        engine.abort_startup()
        worker.join(.5)
        assert not worker.is_alive()
        assert results == ['']
        batch.assert_not_called()
    finally:
        engine.close()
        worker.join(1)
