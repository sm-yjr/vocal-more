from __future__ import annotations

from unittest.mock import Mock, patch


def test_linux_rpc_handler_injects_shared_text_output():
    from vocal_more.bootstrap import RPCHandlerDependencies
    from vocal_more.linux_rpc_handler import LinuxRPCHandler

    output = Mock()
    dependencies = RPCHandlerDependencies(
        config=Mock(),
        recording_store=Mock(),
        recording_retry=Mock(),
        text_polisher=None,
        walkie_talkie=Mock(),
        realtime_long=Mock(),
        meeting=Mock(),
        current_mode=Mock(),
        command_coordinator=Mock(),
        runtime=Mock(),
    )

    with patch(
        "vocal_more.linux_rpc_handler.build_rpc_handler_dependencies",
        return_value=dependencies,
    ) as build:
        handler = LinuxRPCHandler(lambda _method, _params: None, text_output=output)

    assert handler._walkie_talkie is dependencies.walkie_talkie
    assert build.call_args.kwargs["text_output_factory"]() is output
