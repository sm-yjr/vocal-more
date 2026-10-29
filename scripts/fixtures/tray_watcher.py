# SPDX-License-Identifier: GPL-3.0-only
"""Private DBus fixture; never run on the user's session bus."""

import os
from pathlib import Path

from gi.repository import Gio, GLib

assert os.environ.get("VOCAL_MORE_PRIVATE_TRAY_TEST") == "1"
report = Path(os.environ["VOCAL_MORE_TRAY_REGISTRATION"])
xml = """<node><interface name="org.kde.StatusNotifierWatcher">
<method name="RegisterStatusNotifierItem"><arg type="s" direction="in"/></method>
<property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
</interface></node>"""
connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)


def call(conn, sender, path, interface, method, parameters, invocation):
    report.write_text(parameters.unpack()[0])
    invocation.return_value(GLib.Variant("()", ()))


connection.register_object(
    "/StatusNotifierWatcher",
    Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0],
    call,
    lambda *args: GLib.Variant("b", True),
    None,
)
Gio.bus_own_name_on_connection(
    connection, "org.kde.StatusNotifierWatcher", Gio.BusNameOwnerFlags.NONE, None, None
)
GLib.MainLoop().run()
