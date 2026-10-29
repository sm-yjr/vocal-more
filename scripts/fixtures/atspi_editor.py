# SPDX-License-Identifier: GPL-3.0-only
"""Synthetic opt-in text/password fields in the acceptance session only."""

import json
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

app = Gtk.Application(application_id="org.example.VocalMoreAccessibilityFixture")
control = Path(os.environ["VOCAL_MORE_ATSPI_TEST_CONTROL"])


def activate(application):
    window = Gtk.ApplicationWindow(
        application=application, title="Vocal More AT-SPI Fixture"
    )
    entry = Gtk.Entry()
    entry.set_text("前缀：旧值后缀")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(entry)
    other = Gtk.Entry(text="unrelated-field-never-learn")
    box.append(other)
    multiline = Gtk.TextView()
    box.append(multiline)
    window.set_child(box)
    window.present()
    entry.grab_focus()
    entry.select_region(3, 5)

    def poll():
        command = control.read_text() if control.exists() else ""
        if not command:
            return GLib.SOURCE_CONTINUE
        control.unlink()
        if command == "snapshot":
            report = os.environ.get("VOCAL_MORE_EDITOR_REPORT")
            if report:
                Path(report).write_text(
                    json.dumps(
                        {
                            "value": entry.get_text(),
                            "active": window.is_active(),
                            "selection": entry.get_selection_bounds(),
                            "caret": entry.get_position(),
                        }
                    )
                )
        elif command == "raise":
            window.present()
            entry.grab_focus()
            GLib.timeout_add(
                150, lambda: (entry.select_region(3, 5), GLib.SOURCE_REMOVE)[1]
            )
        elif command == "reset":
            entry.set_visibility(True)
            entry.set_text("前缀：旧值后缀")
            entry.grab_focus()
            entry.select_region(3, 5)
        elif command == "long":
            entry.set_text("前🦀" * 6000 + "旧值后缀")
            entry.grab_focus()
            entry.select_region(12000, 12002)
        elif command == "multiline":
            buffer = multiline.get_buffer()
            buffer.set_text("前缀：旧\n值后缀")
            multiline.grab_focus()
            buffer.select_range(
                buffer.get_iter_at_offset(3), buffer.get_iter_at_offset(6)
            )
        elif command == "other":
            other.grab_focus()
        elif command == "secure":
            entry.set_visibility(False)
            entry.set_text("fixture-password-never-read")
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add(50, poll)


app.connect("activate", activate)
app.run([])
