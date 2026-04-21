"""Composition-root helpers for menu app and RPC runtime wiring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .application.dictation_command_coordinator import DictationCommandCoordinator
from .application.runtime_facade import RuntimeFacade
from .config import get_config


@dataclass
class MenuAppDependencies:
    config: object
    hotkey_listener_ready: Optional[bool]
    environment_checks: list
    text_polisher: object | None
    capsule: object
    recording_store: object
    walkie_talkie: object
    realtime_long: object
    current_mode: object
    command_coordinator: object
    hotkey_manager: object
    runtime: RuntimeFacade
    settings_window: object


@dataclass
class RPCHandlerDependencies:
    config: object
    recording_store: object
    text_polisher: object | None
    walkie_talkie: object
    realtime_long: object
    current_mode: object
    command_coordinator: object
    runtime: RuntimeFacade


@dataclass
class AppRuntime:
    runtime: RuntimeFacade
    menu_bar: object
    rpc_handler: object


def _select_mode(default_mode: str, walkie_talkie: object, realtime_long: object) -> object:
    if default_mode == "realtime_long":
        return realtime_long
    return walkie_talkie


def build_menu_app_dependencies(
    app,
    *,
    config=None,
    text_polisher_factory,
    capsule_factory,
    recording_store_factory,
    walkie_talkie_factory,
    realtime_long_factory,
    hotkey_manager_factory,
    settings_window_factory,
    command_coordinator_factory=DictationCommandCoordinator,
    runtime_factory=RuntimeFacade,
) -> MenuAppDependencies:
    config = config or get_config()
    text_polisher = text_polisher_factory() if config.api_key else None
    capsule = capsule_factory(
        on_cancel=app._on_capsule_cancel,
        on_finish=app._on_capsule_finish,
    )
    recording_store = recording_store_factory()

    walkie_talkie = walkie_talkie_factory(
        on_state_change=app._on_state_change,
        on_result=app._on_result,
        on_partial_result=app._on_partial_result,
        on_error=app._on_error,
        on_processing_stage=app._on_processing_stage,
        text_polisher=text_polisher,
        on_audio_level=app._on_audio_level,
        recording_store=recording_store,
    )
    realtime_long = realtime_long_factory(
        on_state_change=app._on_state_change,
        on_result=app._on_result,
        on_partial_result=app._on_partial_result,
        on_error=app._on_error,
        on_processing_stage=app._on_processing_stage,
        text_polisher=text_polisher,
        on_audio_level=app._on_audio_level,
        recording_store=recording_store,
    )
    current_mode = _select_mode(config.default_mode, walkie_talkie, realtime_long)
    command_coordinator = command_coordinator_factory(thread_name="vocal-more-menu-commands")

    hotkey_manager = hotkey_manager_factory(
        on_fn_pressed=app._on_fn_pressed,
        on_fn_released=app._on_fn_released,
        on_double_cmd=app._on_double_cmd,
        on_escape_pressed=app._on_escape_pressed,
    )

    runtime = runtime_factory(
        config=config,
        modes={
            "walkie_talkie": walkie_talkie,
            "realtime_long": realtime_long,
        },
        get_current_mode=lambda: getattr(app, "_current_mode", None),
        set_current_mode=lambda mode: app._select_mode(
            "walkie_talkie" if mode is walkie_talkie else "realtime_long"
        ),
        on_refresh_text_polisher=app._refresh_text_polisher,
        on_set_active_hotkeys=getattr(hotkey_manager, "set_active_hotkeys", None),
        on_set_custom_key=getattr(hotkey_manager, "set_custom_key", None),
        on_apply_interface_language=app._apply_interface_language,
        on_refresh_environment_status=app._refresh_environment_status,
    )

    settings_window = settings_window_factory(
        on_set_config=app._on_settings_config_change,
        on_set_asr_model=app._on_settings_set_asr_model,
        on_sync_form_state=app._on_settings_sync_form_state,
        on_set_device=app._on_settings_set_device,
        on_set_active_hotkeys=app._on_settings_set_hotkeys,
        on_add_dict_entry=app._on_settings_add_dict,
        on_remove_dict_entry=app._on_settings_remove_dict,
        on_refresh_devices=app._on_settings_refresh_devices,
        on_open_config_file=app._on_settings_open_config,
        on_open_dict_file=app._on_settings_open_dict,
        on_open_external=app._on_settings_open_external,
        recording_store=recording_store,
    )

    return MenuAppDependencies(
        config=config,
        hotkey_listener_ready=None,
        environment_checks=[],
        text_polisher=text_polisher,
        capsule=capsule,
        recording_store=recording_store,
        walkie_talkie=walkie_talkie,
        realtime_long=realtime_long,
        current_mode=current_mode,
        command_coordinator=command_coordinator,
        hotkey_manager=hotkey_manager,
        runtime=runtime,
        settings_window=settings_window,
    )


def build_rpc_handler_dependencies(
    handler,
    *,
    send_notification: Callable[[str, dict], None],
    config=None,
    text_polisher_factory,
    recording_store_factory,
    walkie_talkie_factory,
    realtime_long_factory,
    command_coordinator_factory=DictationCommandCoordinator,
    runtime_factory=RuntimeFacade,
) -> RPCHandlerDependencies:
    config = config or get_config()
    recording_store = recording_store_factory()
    text_polisher = text_polisher_factory() if config.api_key else None

    walkie_talkie = walkie_talkie_factory(
        on_state_change=handler._on_state_change,
        on_result=handler._on_result,
        on_partial_result=handler._on_partial_result,
        on_error=handler._on_error,
        on_processing_stage=handler._on_processing_stage,
        text_polisher=text_polisher,
        on_audio_level=handler._on_audio_level,
        recording_store=recording_store,
    )
    realtime_long = realtime_long_factory(
        on_state_change=handler._on_state_change,
        on_result=handler._on_result,
        on_partial_result=handler._on_partial_result,
        on_error=handler._on_error,
        on_processing_stage=handler._on_processing_stage,
        text_polisher=text_polisher,
        on_audio_level=handler._on_audio_level,
        recording_store=recording_store,
    )
    current_mode = _select_mode(config.default_mode, walkie_talkie, realtime_long)
    command_coordinator = command_coordinator_factory(thread_name="vocal-more-rpc-commands")

    runtime = runtime_factory(
        config=config,
        modes={
            "walkie_talkie": walkie_talkie,
            "realtime_long": realtime_long,
        },
        get_current_mode=lambda: getattr(handler, "_current_mode", None),
        set_current_mode=lambda mode: setattr(handler, "_current_mode", mode),
        on_refresh_text_polisher=handler._refresh_text_polisher,
    )

    return RPCHandlerDependencies(
        config=config,
        recording_store=recording_store,
        text_polisher=text_polisher,
        walkie_talkie=walkie_talkie,
        realtime_long=realtime_long,
        current_mode=current_mode,
        command_coordinator=command_coordinator,
        runtime=runtime,
    )


def build_menu_app(*, app_factory=None):
    if app_factory is None:
        raise TypeError("build_menu_app() requires an app_factory")
    return app_factory()


def build_rpc_handler(
    *,
    send_notification: Callable[[str, dict], None],
    handler_factory=None,
):
    if handler_factory is None:
        raise TypeError("build_rpc_handler() requires a handler_factory")
    return handler_factory(send_notification=send_notification)


def build_runtime(
    *,
    app_factory,
    handler_factory,
    send_notification: Optional[Callable[[str, dict], None]] = None,
) -> AppRuntime:
    send_notification = send_notification or (lambda _method, _params: None)
    menu_bar = app_factory()
    rpc_handler = handler_factory(send_notification=send_notification)
    rpc_handler._runtime = menu_bar.runtime
    return AppRuntime(
        runtime=menu_bar.runtime,
        menu_bar=menu_bar,
        rpc_handler=rpc_handler,
    )
