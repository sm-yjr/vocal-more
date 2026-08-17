from vocal_more.linux_diagnostics import collect_linux_environment


def test_linux_diagnostics_reports_required_boundaries(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")

    result = collect_linux_environment(
        dbus_ready=True,
        extension_status="enabled",
        paste_status="ready",
    )

    assert result["Wayland"] == "wayland"
    assert result["D-Bus"] == "ready"
    assert result["extension"] == "enabled"
    assert result["auto-paste"] == "ready"
    assert "PipeWire" in result
    assert "PipeWire default source" in result
    assert "PortAudio" in result
    assert "Shell compatibility" in result
    assert result["session recovery"] == "D-Bus name-owner reconnect enabled"
