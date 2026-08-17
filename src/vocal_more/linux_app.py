"""Ubuntu GNOME 50/Wayland desktop host for Vocal More."""

from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import __version__
from .config import ASR_MODEL_CATALOG, LLM_MODEL_CATALOG, get_config
from .linux_desktop_contract import (
    BUS_NAME,
    CONTEXT_INTERFACE_NAME,
    DBUS_INTROSPECTION_XML,
    INTERFACE_NAME,
    OBJECT_PATH,
    DesktopSnapshot,
)
from .linux_desktop_controller import LinuxDesktopController
from .linux_settings_window import LinuxSettingsSnapshot, LinuxSettingsWindow
from .paths import default_app_paths

_EXTENSION_GUIDE_MARKER = ".gnome-extension-guide-v1"


def extension_guide_marker(paths=None) -> Path:
    app_paths = paths or default_app_paths()
    return app_paths.state_dir / _EXTENSION_GUIDE_MARKER


def should_show_extension_guide(extension_status: str, paths=None) -> bool:
    return extension_status != "enabled" and not extension_guide_marker(paths).exists()


def _load_gnome_runtime():
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, Gio, GLib, Gtk
    except Exception as exc:
        raise RuntimeError(
            "The Linux desktop host requires Ubuntu packages python3-gi and "
            "gir1.2-gtk-4.0"
        ) from exc
    return Gtk, Gio, GLib, Gdk


def _application_class(Gtk, Gio, GLib, Gdk):
    class LinuxVocalMoreApplication(Gtk.Application):
        def __init__(self) -> None:
            super().__init__(
                application_id=BUS_NAME,
                flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
            )
            self.config = get_config()
            self._connection = None
            self._registration_ids = []
            self._controller = None
            self._handler = None
            self._text_output = None
            self._settings = None
            self._mic_test = None
            self._workers = ThreadPoolExecutor(
                max_workers=2,
                thread_name_prefix="vocal-more-linux-ui",
            )
            self._latest_snapshot = DesktopSnapshot()
            self._snapshot_lock = threading.Lock()
            self._snapshot_timer = 0
            self._closing = False
            self._guide_window = None

        def do_dbus_register(self, connection, object_path) -> bool:
            if not Gtk.Application.do_dbus_register(self, connection, object_path):
                return False
            node = Gio.DBusNodeInfo.new_for_xml(DBUS_INTROSPECTION_XML)
            self._connection = connection
            try:
                self._registration_ids = [
                    connection.register_object(
                        OBJECT_PATH,
                        interface,
                        self._on_dbus_method_call,
                        None,
                        None,
                    )
                    for interface in node.interfaces
                ]
            except Exception:
                for registration_id in self._registration_ids:
                    connection.unregister_object(registration_id)
                self._registration_ids = []
                raise
            return all(self._registration_ids)

        def do_dbus_unregister(self, connection, object_path) -> None:
            for registration_id in self._registration_ids:
                connection.unregister_object(registration_id)
            self._registration_ids = []
            self._connection = None
            Gtk.Application.do_dbus_unregister(self, connection, object_path)

        def do_startup(self) -> None:
            Gtk.Application.do_startup(self)
            self.hold()
            from .core.audio_recorder import AudioRecorder
            from .linux_rpc_handler import LinuxRPCHandler
            from .linux_text_output import LinuxTextOutputAdapter
            from .ui.mic_test_controller import MicTestController

            self._text_output = LinuxTextOutputAdapter(
                write_clipboard=self._write_clipboard,
                request_paste=self._request_shell_paste,
            )
            self._handler = LinuxRPCHandler(
                send_notification=self._on_runtime_notification,
                text_output=self._text_output,
            )
            self._controller = LinuxDesktopController(
                config=self.config,
                handler=self._handler,
                on_snapshot=self._queue_snapshot,
                on_show_settings=self._schedule_show_settings,
                on_quit=self.quit,
                on_notice=self._notify,
            )
            self._settings = LinuxSettingsWindow(
                Gtk=Gtk,
                Gio=Gio,
                application=self,
                on_save=self._save_settings,
                on_open_path=self._open_path,
                on_action=self._settings_action,
                on_start_mic_test=self._start_mic_test,
                on_stop_mic_test=self._stop_mic_test,
            )
            self._mic_test = MicTestController(
                config_provider=lambda: self.config,
                recorder_factory=AudioRecorder,
                on_level=lambda value: GLib.idle_add(self._settings_mic_level, value),
                on_error=lambda message: GLib.idle_add(self._settings_mic_error, message),
                on_complete=lambda: GLib.idle_add(self._settings_mic_complete),
            )
            self._sync_extension_trigger()

        def do_activate(self) -> None:
            self._show_settings()
            self._show_extension_guide_if_needed()

        def do_command_line(self, command_line) -> int:
            arguments = list(command_line.get_arguments())[1:]
            if "--service" not in arguments:
                self.activate()
            command_line.set_exit_status(0)
            return 0

        def do_shutdown(self) -> None:
            if self._closing:
                return
            self._closing = True
            if self._mic_test is not None:
                self._mic_test.cleanup()
            if self._settings is not None:
                self._settings.close()
            if self._controller is not None:
                self._controller.close()
            if self._text_output is not None:
                self._text_output.close()
            self._workers.shutdown(wait=False, cancel_futures=True)
            try:
                self.release()
            except Exception:
                pass
            Gtk.Application.do_shutdown(self)

        def _on_dbus_method_call(
            self,
            _connection,
            _sender,
            _object_path,
            _interface_name,
            method_name,
            parameters,
            invocation,
        ) -> None:
            try:
                if _interface_name == CONTEXT_INTERFACE_NAME:
                    if method_name != "SetFocusedApp":
                        invocation.return_dbus_error(
                            f"{CONTEXT_INTERFACE_NAME}.UnknownMethod",
                            f"Unknown method: {method_name}",
                        )
                        return
                    self._set_focused_app(str(parameters.unpack()[0] or ""))
                    invocation.return_value(GLib.Variant("()", ()))
                    return
                if method_name == "GetSnapshot":
                    invocation.return_value(GLib.Variant("(s)", (self._snapshot_json(),)))
                    return
                controller = self._require_controller()
                if method_name == "TriggerPressed":
                    controller.submit_trigger_pressed()
                elif method_name == "TriggerReleased":
                    controller.submit_trigger_released()
                elif method_name == "Cancel":
                    controller.submit_cancel()
                elif method_name == "SetMode":
                    controller.submit_set_mode(str(parameters.unpack()[0]))
                elif method_name == "SetAutoPaste":
                    controller.submit_set_auto_paste(bool(parameters.unpack()[0]))
                elif method_name == "ShowSettings":
                    controller.show_settings()
                elif method_name == "Quit":
                    controller.request_quit()
                elif method_name == "CompletePaste":
                    request_id, ok, error = parameters.unpack()
                    self._text_output.complete_paste(
                        int(request_id),
                        bool(ok),
                        str(error or ""),
                    )
                else:
                    invocation.return_dbus_error(
                        f"{INTERFACE_NAME}.UnknownMethod",
                        f"Unknown method: {method_name}",
                    )
                    return
                invocation.return_value(GLib.Variant("()", ()))
            except Exception as exc:
                invocation.return_dbus_error(
                    f"{INTERFACE_NAME}.Failed",
                    str(exc),
                )

        def _require_controller(self) -> LinuxDesktopController:
            if self._controller is None:
                raise RuntimeError("Vocal More backend is still starting")
            return self._controller

        def _queue_snapshot(self, snapshot: DesktopSnapshot) -> None:
            with self._snapshot_lock:
                self._latest_snapshot = snapshot
                if self._snapshot_timer:
                    return
                self._snapshot_timer = GLib.timeout_add(34, self._emit_latest_snapshot)

        def _snapshot_json(self) -> str:
            with self._snapshot_lock:
                return self._latest_snapshot.to_json()

        def _emit_latest_snapshot(self) -> bool:
            with self._snapshot_lock:
                self._snapshot_timer = 0
                payload = self._latest_snapshot.to_json()
            if self._connection is not None:
                self._connection.emit_signal(
                    None,
                    OBJECT_PATH,
                    INTERFACE_NAME,
                    "SnapshotChanged",
                    GLib.Variant("(s)", (payload,)),
                )
            return GLib.SOURCE_REMOVE

        def _request_shell_paste(self, request_id: int) -> None:
            def emit() -> bool:
                if self._connection is None:
                    self._text_output.complete_paste(
                        request_id,
                        False,
                        "GNOME Shell extension is disconnected",
                    )
                    return GLib.SOURCE_REMOVE
                self._connection.emit_signal(
                    None,
                    OBJECT_PATH,
                    INTERFACE_NAME,
                    "PasteRequested",
                    GLib.Variant("(t)", (request_id,)),
                )
                return GLib.SOURCE_REMOVE

            GLib.idle_add(emit)

        def _write_clipboard(self, text: str, timeout: float) -> bool:
            completed = threading.Event()
            result = {"ok": False}

            def write() -> bool:
                display = Gdk.Display.get_default()
                if display is not None:
                    display.get_clipboard().set(str(text))
                    result["ok"] = True
                completed.set()
                return GLib.SOURCE_REMOVE

            GLib.idle_add(write)
            return completed.wait(timeout) and result["ok"]

        def _on_runtime_notification(self, method: str, params: dict) -> None:
            if self._controller is not None:
                self._controller.handle_runtime_notification(method, params)

        def _schedule_show_settings(self) -> None:
            GLib.idle_add(self._show_settings)

        def _show_settings(self) -> bool:
            if self._settings is None or self._handler is None:
                return GLib.SOURCE_REMOVE
            try:
                devices = self._handler.dispatch("list_devices", {})
            except Exception as exc:
                print(f"[LinuxApp] Microphone enumeration failed: {exc}")
                devices = []
            paths = default_app_paths()
            snapshot = LinuxSettingsSnapshot(
                version=__version__,
                config=self.config.to_dict(),
                asr_models=tuple(ASR_MODEL_CATALOG),
                llm_models=tuple(LLM_MODEL_CATALOG),
                devices=tuple(devices),
                config_path=str(paths.config_path),
                data_dir=str(paths.data_dir),
                log_path=str(paths.log_path),
                environment=self._environment_snapshot(),
                recordings=tuple(self._safe_dispatch("list_recordings", [], {})),
                dictionary_entries=tuple(self._safe_dispatch("get_dictionary", [], {})),
                learning_candidates=tuple(
                    self._safe_dispatch("list_dictionary_learning", [], {"limit": 50})
                ),
                context_summary=self._safe_dispatch("get_context_summary", {"counts": {}, "total": 0}, {}),
            )
            self._settings.show(snapshot)
            return GLib.SOURCE_REMOVE

        def _save_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
            if self._controller.snapshot().state != "idle":
                return {
                    "ok": False,
                    "message": "Finish the current dictation before saving settings.",
                }
            input_device = updates.pop("audio.input_device", self.config.audio.input_device)
            default_mode = str(updates.pop("default_mode", self.config.default_mode))
            for key, value in updates.items():
                self._handler.dispatch("set_config", {"key": key, "value": value})
            if input_device != self.config.audio.input_device:
                self._handler.dispatch("set_device", {"device": input_device})
            if default_mode != self.config.default_mode:
                self._handler.dispatch("set_mode", {"mode": default_mode})
            self._sync_extension_trigger()
            self._controller.publish()
            return {"ok": True, "message": "Settings saved and applied"}

        def _safe_dispatch(self, method: str, fallback, params: dict):
            try:
                return self._handler.dispatch(method, params)
            except Exception as exc:
                print(f"[LinuxApp] Settings data '{method}' unavailable: {exc}")
                return fallback

        def _settings_action(self, action: str, payload: dict) -> dict[str, Any]:
            method = {
                "retry_recording": "retry_transcription",
                "meeting_notes": "generate_meeting_notes",
                "delete_recording": "delete_recording",
                "add_dictionary": "add_dict_entry",
                "remove_dictionary": "remove_dict_entry",
                "approve_learning": "approve_dictionary_learning",
                "reject_learning": "reject_dictionary_learning",
                "undo_learning": "undo_dictionary_learning",
                "reset_context": "reset_context",
                "export_support": "export_support_bundle",
            }.get(action)
            if method is None:
                raise ValueError(f"Unknown settings action: {action}")
            result = self._handler.dispatch(method, dict(payload))
            if not bool(result.get("ok", True)):
                return {"ok": False, "message": str(result.get("status") or "Action was not accepted")}
            GLib.idle_add(self._show_settings)
            if action == "export_support":
                return {"ok": True, "message": f"Support bundle: {result.get('path', '')}"}
            return {"ok": True, "message": "Done"}

        def _sync_extension_trigger(self) -> None:
            accelerator = str(
                getattr(self.config.hotkey, "linux_accelerator", "F8")
            )
            try:
                settings = Gio.Settings.new("org.gnome.shell.extensions.vocal-more")
                settings.set_strv("linux-accelerator", [accelerator])
            except Exception as exc:
                print(f"[LinuxApp] Could not sync GNOME trigger: {exc}")

        def _show_extension_guide_if_needed(self) -> None:
            status = self._extension_status()
            if not should_show_extension_guide(status):
                return
            marker = extension_guide_marker()
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("shown\n", encoding="utf-8")

            guide = Gtk.ApplicationWindow(application=self)
            guide.set_title("Enable Vocal More for GNOME")
            guide.set_default_size(480, 220)
            body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
            body.set_margin_top(24)
            body.set_margin_bottom(24)
            body.set_margin_start(24)
            body.set_margin_end(24)
            heading = Gtk.Label(label="One-time GNOME setup", xalign=0)
            heading.add_css_class("title-2")
            instructions = Gtk.Label(
                label=(
                    "1. Open the Extensions app and enable “Vocal More”.\n"
                    "2. Sign out and sign in once.\n"
                    "3. Use F8–F12 to dictate; automatic paste requires the extension."
                ),
                xalign=0,
            )
            instructions.set_wrap(True)
            close = Gtk.Button(label="Got it")
            close.add_css_class("suggested-action")
            close.set_halign(Gtk.Align.END)
            close.connect("clicked", lambda _button: guide.close())
            body.append(heading)
            body.append(instructions)
            body.append(close)
            guide.set_child(body)
            guide.connect("close-request", self._clear_guide_window)
            self._guide_window = guide
            guide.present()

        def _clear_guide_window(self, _window) -> bool:
            self._guide_window = None
            return False

        def _environment_snapshot(self) -> dict[str, str]:
            from .linux_diagnostics import collect_linux_environment

            extension_status = self._extension_status()
            paste_status = (
                self._text_output.diagnostic_status()
                if self._text_output is not None and extension_status == "enabled"
                else "extension unavailable"
            )
            return collect_linux_environment(
                dbus_ready=self._connection is not None,
                extension_status=extension_status,
                paste_status=paste_status,
            )

        def _extension_status(self) -> str:
            try:
                proxy = Gio.DBusProxy.new_for_bus_sync(
                    Gio.BusType.SESSION,
                    Gio.DBusProxyFlags.NONE,
                    None,
                    "org.gnome.Shell",
                    "/org/gnome/Shell",
                    "org.gnome.Shell.Extensions",
                    None,
                )
                result = proxy.call_sync(
                    "GetExtensionInfo",
                    GLib.Variant("(s)", ("vocal-more@sm-yjr.com",)),
                    Gio.DBusCallFlags.NONE,
                    500,
                    None,
                )
                info = result.unpack()[0]
                version = info.get("version", 0)
                if hasattr(version, "unpack"):
                    version = version.unpack()
                if int(version or 0) != 1:
                    return f"version mismatch (installed {version or 'unknown'}, expected 1)"
                state = info.get("state", 0)
                if hasattr(state, "unpack"):
                    state = state.unpack()
                return "enabled" if int(state) == 1 else "disabled"
            except Exception:
                return "not installed"

        def _notify(self, title: str, message: str) -> None:
            def show() -> bool:
                notice = Gio.Notification.new(title)
                notice.set_body(message)
                self.send_notification("runtime", notice)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(show)

        def _open_path(self, target: str) -> None:
            paths = default_app_paths()
            selected = {
                "data": paths.data_dir,
                "config": paths.config_path,
                "log": paths.log_path,
            }.get(target)
            if selected is None:
                raise ValueError(f"Unknown path target: {target}")
            if target == "config" and not selected.exists():
                self.config.save()
            elif target == "log" and not selected.exists():
                selected.parent.mkdir(parents=True, exist_ok=True)
                selected.touch()
            else:
                selected.mkdir(parents=True, exist_ok=True) if target == "data" else None
            Gio.AppInfo.launch_default_for_uri(selected.resolve().as_uri(), None)

        def _start_mic_test(self, on_level, on_error) -> None:
            self._mic_level_callback = on_level
            self._mic_error_callback = on_error
            self._workers.submit(self._mic_test.start)

        def _stop_mic_test(self) -> None:
            if self._mic_test is not None:
                self._workers.submit(self._mic_test.stop)

        def _settings_mic_level(self, value: float) -> bool:
            callback = getattr(self, "_mic_level_callback", None)
            if callback:
                callback(value)
            return GLib.SOURCE_REMOVE

        def _settings_mic_error(self, message: str) -> bool:
            callback = getattr(self, "_mic_error_callback", None)
            if callback:
                callback(message)
            return GLib.SOURCE_REMOVE

        def _settings_mic_complete(self) -> bool:
            if self._settings is not None:
                self._settings_mic_level(0.0)
            return GLib.SOURCE_REMOVE

        @staticmethod
        def _set_focused_app(desktop_app_id: str) -> None:
            try:
                from .infrastructure.linux_app_context import set_focused_app_id

                set_focused_app_id(desktop_app_id)
            except (ImportError, AttributeError):
                return

    return LinuxVocalMoreApplication


def main() -> None:
    if not sys.platform.startswith("linux"):
        raise SystemExit("The Linux host can only run on Linux.")
    if os.environ.get("XDG_SESSION_TYPE", "").casefold() != "wayland":
        raise SystemExit("Vocal More's Linux desktop host requires a Wayland session.")
    Gtk, Gio, GLib, Gdk = _load_gnome_runtime()
    application = _application_class(Gtk, Gio, GLib, Gdk)()
    raise SystemExit(application.run(sys.argv))


if __name__ == "__main__":
    main()


__all__ = [
    "extension_guide_marker",
    "main",
    "should_show_extension_guide",
]
