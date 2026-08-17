#!/usr/bin/python3
"""Exercise the installed Desktop1 contract on an isolated session bus."""

from __future__ import annotations

import json

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from vocal_more.linux_desktop_contract import (
    BUS_NAME,
    CONTEXT_INTERFACE_NAME,
    DBUS_INTROSPECTION_XML,
    INTERFACE_NAME,
    OBJECT_PATH,
    DesktopSnapshot,
)


def main() -> int:
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    interfaces = Gio.DBusNodeInfo.new_for_xml(DBUS_INTROSPECTION_XML).interfaces
    loop = GLib.MainLoop()
    result = {"ok": False, "error": "timeout"}
    focused = {"app_id": ""}

    def method_call(_conn, _sender, _path, iface, method, params, invocation):
        if iface == CONTEXT_INTERFACE_NAME and method == "SetFocusedApp":
            focused["app_id"] = params.unpack()[0]
            invocation.return_value(GLib.Variant("()", ()))
            return
        if method != "GetSnapshot":
            invocation.return_dbus_error(f"{INTERFACE_NAME}.Unexpected", method)
            return
        invocation.return_value(GLib.Variant("(s)", (DesktopSnapshot().to_json(),)))

    registrations = [
        connection.register_object(OBJECT_PATH, interface, method_call, None, None)
        for interface in interfaces
    ]

    def call_snapshot(_connection=None, _name=None) -> None:
        def finished(source, call_result) -> None:
            try:
                reply = source.call_finish(call_result)
                snapshot = json.loads(reply.unpack()[0])
                result["ok"] = (
                    snapshot["schema_version"] == 1
                    and focused["app_id"] == "org.gnome.Terminal"
                )
                result["error"] = ""
            except Exception as exc:
                result["error"] = str(exc)
            loop.quit()

        connection.call(
            BUS_NAME,
            OBJECT_PATH,
            INTERFACE_NAME,
            "GetSnapshot",
            None,
            GLib.VariantType.new("(s)"),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
            finished,
        )

    def call_context(_connection=None, _name=None) -> None:
        def finished(source, call_result) -> None:
            try:
                source.call_finish(call_result)
            except Exception as exc:
                result["error"] = str(exc)
                loop.quit()
                return
            call_snapshot()

        connection.call(
            BUS_NAME,
            OBJECT_PATH,
            CONTEXT_INTERFACE_NAME,
            "SetFocusedApp",
            GLib.Variant("(s)", ("org.gnome.Terminal",)),
            GLib.VariantType.new("()"),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
            finished,
        )

    owner = Gio.bus_own_name_on_connection(
        connection,
        BUS_NAME,
        Gio.BusNameOwnerFlags.NONE,
        call_context,
        lambda _connection, _name: loop.quit(),
    )
    GLib.timeout_add_seconds(3, lambda: loop.quit() or GLib.SOURCE_REMOVE)
    loop.run()
    Gio.bus_unown_name(owner)
    for registration in registrations:
        connection.unregister_object(registration)
    if not result["ok"]:
        raise SystemExit(f"Desktop1 D-Bus smoke failed: {result['error']}")
    print("Desktop1 D-Bus smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
