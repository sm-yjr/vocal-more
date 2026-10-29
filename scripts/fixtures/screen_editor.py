# SPDX-License-Identifier: GPL-3.0-only
"""Public, synthetic Wayland window for real ScreenCast portal acceptance."""

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

control = Path(os.environ["VOCAL_MORE_SCREEN_TEST_OUTPUT"])
app = Gtk.Application(application_id="org.example.VocalMoreScreenFixture")


def activate(application):
    window = Gtk.ApplicationWindow(
        application=application, title="Vocal More Screen Fixture"
    )
    window.set_default_size(1024, 640)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
    box.set_valign(Gtk.Align.FILL)
    box.set_halign(Gtk.Align.FILL)
    box.add_css_class("fixture")
    label = Gtk.Label(
        label="Vocal More · Screen Context Acceptance\n\nProject: LumaHarbor\nVerification: VM-8617\n\nSynthetic content only · No microphone · No cloud"
    )
    label.set_hexpand(True)
    label.set_vexpand(True)
    box.append(label)
    provider = Gtk.CssProvider()
    provider.load_from_data(
        b".fixture {background:#143a80;color:white;padding:32px;} .fixture.flip {background:#aa3c14;} label {font-size:28px;}"
    )
    Gtk.StyleContext.add_provider_for_display(
        window.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    window.set_child(box)
    window.present()
    phase = 0

    def tick():
        nonlocal phase
        phase += 1
        if (control / "flip").exists():
            box.add_css_class("flip")
            window.set_default_size(800, 520)
        label.set_label(
            f"Vocal More · Screen Context Acceptance\n\nProject: LumaHarbor\nVerification: VM-8617\n\nSynthetic content only · frame {phase}"
        )
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add(300, tick)


app.connect("activate", activate)
app.run([])
