"""GTK4 settings surface for the Ubuntu GNOME host."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .linux_desktop_contract import LINUX_ACCELERATORS


@dataclass(frozen=True)
class LinuxSettingsSnapshot:
    version: str
    config: Mapping[str, Any]
    asr_models: Sequence[Mapping[str, Any]]
    llm_models: Sequence[Mapping[str, Any]]
    devices: Sequence[Mapping[str, Any]]
    config_path: str
    data_dir: str
    log_path: str
    environment: Mapping[str, str]
    recordings: Sequence[Mapping[str, Any]] = ()
    dictionary_entries: Sequence[Mapping[str, Any]] = ()
    learning_candidates: Sequence[Mapping[str, Any]] = ()
    context_summary: Mapping[str, Any] | None = None


def build_linux_settings_updates(values: Mapping[str, Any]) -> dict[str, Any]:
    """Validate user-editable GTK values and return normalized config updates."""
    language = str(values.get("ui.language") or "zh")
    mode = str(values.get("default_mode") or "realtime_long")
    accelerator = str(values.get("hotkey.linux_accelerator") or "F8")
    if language not in {"zh", "en"}:
        raise ValueError("Unsupported interface language")
    if mode not in {"walkie_talkie", "realtime_long", "meeting"}:
        raise ValueError("Unsupported recording mode")
    if accelerator not in LINUX_ACCELERATORS:
        raise ValueError("Linux trigger must be F8-F12")

    gain = _bounded_float(values.get("audio.gain"), 0.5, 50.0, "gain")
    highpass = _bounded_int(
        values.get("audio.highpass_freq"),
        50,
        500,
        "high-pass frequency",
    )
    input_device = str(values.get("audio.input_device") or "").strip() or None
    temperature = _bounded_float(values.get("llm.temperature", 0.0), 0.0, 2.0, "temperature")
    max_tokens = _bounded_int(values.get("llm.max_tokens", 1024), 64, 8192, "max tokens")
    dictionary_exclusions = _split_app_ids(values.get("dictionary_learning.excluded_bundle_ids"))
    context_exclusions = _split_app_ids(values.get("context_personalization.excluded_bundle_ids"))
    return {
        "api_key": str(values.get("api_key") or "").strip(),
        "ui.language": language,
        "default_mode": mode,
        "hotkey.linux_accelerator": accelerator,
        "auto_paste": bool(values.get("auto_paste")),
        "enable_polish": bool(values.get("enable_polish")),
        "asr.model": str(values.get("asr.model") or "").strip(),
        "llm.model": str(values.get("llm.model") or "").strip(),
        "llm.level": str(values.get("llm.level") or "minimal"),
        "llm.temperature": temperature,
        "llm.max_tokens": max_tokens,
        "llm.enable_thinking": bool(values.get("llm.enable_thinking")),
        "llm.structured": bool(values.get("llm.structured")),
        "llm.tone": str(values.get("llm.tone") or "neutral"),
        "llm.persona": str(values.get("llm.persona") or "default"),
        "dictionary_learning.enabled": bool(values.get("dictionary_learning.enabled")),
        "dictionary_learning.excluded_bundle_ids": dictionary_exclusions,
        "context_personalization.enabled": bool(values.get("context_personalization.enabled")),
        "context_personalization.excluded_bundle_ids": context_exclusions,
        "audio.input_device": input_device,
        "audio.gain_mode": "manual",
        "audio.gain": gain,
        "audio.highpass_filter": bool(values.get("audio.highpass_filter")),
        "audio.highpass_freq": highpass,
        "audio.soft_limiter": bool(values.get("audio.soft_limiter")),
    }


def _split_app_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = value.replace("\n", ",").split(",")
    elif isinstance(value, Sequence):
        raw = value
    else:
        raw = []
    result: list[str] = []
    for item in raw:
        app_id = str(item).strip()
        if app_id and app_id not in result:
            result.append(app_id)
    return result


def _bounded_float(value: Any, minimum: float, maximum: float, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return result


def _bounded_int(value: Any, minimum: int, maximum: int, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


class LinuxSettingsWindow:
    """Lazy GTK window; all widget access stays on the GLib main thread."""

    def __init__(
        self,
        *,
        Gtk,
        Gio,
        application,
        on_save: Callable[[dict[str, Any]], Mapping[str, Any]],
        on_open_path: Callable[[str], None],
        on_action: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
        on_start_mic_test: Callable[[Callable[[float], None], Callable[[str], None]], None],
        on_stop_mic_test: Callable[[], None],
    ) -> None:
        self._Gtk = Gtk
        self._Gio = Gio
        self._application = application
        self._on_save = on_save
        self._on_open_path = on_open_path
        self._on_action = on_action
        self._on_start_mic_test = on_start_mic_test
        self._on_stop_mic_test = on_stop_mic_test
        self._window = None
        self._widgets: dict[str, Any] = {}
        self._option_ids: dict[str, list[Any]] = {}
        self._status = None
        self._mic_level = None
        self._dynamic_sections: dict[str, Any] = {}

    def show(self, snapshot: LinuxSettingsSnapshot) -> None:
        if self._window is None:
            self._build()
        self._populate(snapshot)
        self._window.present()

    def close(self) -> None:
        self._on_stop_mic_test()
        if self._window is not None:
            self._window.close()
            self._window = None

    def _build(self) -> None:
        Gtk = self._Gtk
        window = Gtk.ApplicationWindow(application=self._application)
        window.set_title("Vocal More Settings")
        window.set_default_size(720, 760)
        window.connect("close-request", self._on_close_request)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        header = Gtk.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Vocal More"))
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save)
        header.pack_end(save)
        root.append(header)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.set_margin_top(20)
        content.set_margin_bottom(24)
        content.set_margin_start(28)
        content.set_margin_end(28)
        scroll.set_child(content)
        root.append(scroll)

        general = self._section(content, "General")
        self._entry(general, "API Key", "api_key", password=True)
        self._choice(general, "Language", "ui.language", [("zh", "中文"), ("en", "English")])
        self._choice(general, "Mode", "default_mode", [
            ("walkie_talkie", "Push to Talk"),
            ("realtime_long", "Long Dictation"),
            ("meeting", "Meeting"),
        ])
        self._choice(general, "Trigger", "hotkey.linux_accelerator", [(x, x) for x in LINUX_ACCELERATORS])
        self._switch(general, "Paste automatically", "auto_paste")

        recognition = self._section(content, "Recognition and polish")
        self._choice(recognition, "ASR model", "asr.model", [])
        self._switch(recognition, "Polish transcription", "enable_polish")
        self._choice(recognition, "LLM model", "llm.model", [])
        self._choice(recognition, "Polish strength", "llm.level", [
            ("minimal", "Minimal"), ("balanced", "Balanced"), ("strong", "Strong")
        ])
        self._spin(recognition, "Temperature", "llm.temperature", 0.0, 2.0, 0.1)
        self._spin(recognition, "Maximum output tokens", "llm.max_tokens", 64, 8192, 64)
        self._switch(recognition, "Reasoning mode", "llm.enable_thinking")
        self._switch(recognition, "Structured output", "llm.structured")
        self._choice(recognition, "Tone", "llm.tone", [
            ("neutral", "Neutral"), ("gentle", "Gentle"), ("direct", "Direct")
        ])
        self._choice(recognition, "Persona", "llm.persona", [
            ("default", "Default"), ("technical", "Technical"),
            ("bilingual", "Bilingual"), ("professional", "Professional"),
            ("chat", "Chat"),
        ])

        audio = self._section(content, "Low-voice audio")
        self._choice(audio, "Microphone", "audio.input_device", [(None, "System default")])
        self._spin(audio, "Software gain", "audio.gain", 0.5, 50.0, 0.5)
        self._switch(audio, "High-pass filter", "audio.highpass_filter")
        self._spin(audio, "High-pass frequency", "audio.highpass_freq", 50, 500, 10)
        self._switch(audio, "Soft limiter", "audio.soft_limiter")
        test_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        test_button = Gtk.Button(label="Test microphone (5 seconds)")
        test_button.connect("clicked", self._toggle_mic_test)
        self._mic_level = Gtk.LevelBar.new_for_interval(0.0, 1.0)
        self._mic_level.set_hexpand(True)
        test_row.append(test_button)
        test_row.append(self._mic_level)
        audio.append(test_row)
        self._widgets["mic_test_button"] = test_button

        privacy = self._section(content, "Context and dictionary learning")
        self._switch(privacy, "Context personalization", "context_personalization.enabled")
        self._entry(privacy, "Excluded desktop app IDs", "context_personalization.excluded_bundle_ids")
        self._switch(privacy, "Automatic dictionary learning", "dictionary_learning.enabled")
        self._entry(privacy, "Learning exclusions", "dictionary_learning.excluded_bundle_ids")
        reset_context = Gtk.Button(label="Reset context profile")
        reset_context.set_halign(Gtk.Align.START)
        reset_context.connect("clicked", lambda _button: self._run_action("reset_context", {}))
        privacy.append(reset_context)

        history = self._section(content, "Recording history")
        self._dynamic_sections["history"] = history

        dictionary = self._section(content, "Dictionary")
        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        term = Gtk.Entry(placeholder_text="Term")
        aliases = Gtk.Entry(placeholder_text="Aliases, comma separated")
        add = Gtk.Button(label="Add")
        add.connect("clicked", lambda _button: self._add_dictionary_entry(term, aliases))
        term.set_hexpand(True)
        aliases.set_hexpand(True)
        add_row.append(term)
        add_row.append(aliases)
        add_row.append(add)
        dictionary.append(add_row)
        self._dynamic_sections["dictionary"] = dictionary

        candidates = self._section(content, "Dictionary learning review")
        self._dynamic_sections["candidates"] = candidates

        system = self._section(content, "System")
        for target, label in (("data", "Open data folder"), ("config", "Open config"), ("log", "Open log")):
            button = Gtk.Button(label=label)
            button.set_halign(Gtk.Align.START)
            button.connect("clicked", lambda _b, selected=target: self._on_open_path(selected))
            system.append(button)
        export = Gtk.Button(label="Export support bundle")
        export.set_halign(Gtk.Align.START)
        export.connect("clicked", lambda _button: self._run_action("export_support", {}))
        system.append(export)
        self._status = Gtk.Label(xalign=0)
        self._status.set_wrap(True)
        content.append(self._status)

        window.set_child(root)
        self._window = window

    def _section(self, parent, title: str):
        Gtk = self._Gtk
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class("title-3")
        box.append(heading)
        box.add_css_class("card")
        parent.append(box)
        return box

    def _row(self, parent, label: str, widget) -> None:
        Gtk = self._Gtk
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        text = Gtk.Label(label=label, xalign=0)
        text.set_hexpand(True)
        row.append(text)
        row.append(widget)
        parent.append(row)

    def _entry(self, parent, label: str, key: str, *, password: bool = False) -> None:
        widget = self._Gtk.Entry()
        widget.set_hexpand(True)
        if password:
            widget.set_visibility(False)
            widget.set_input_purpose(self._Gtk.InputPurpose.PASSWORD)
        self._widgets[key] = widget
        self._row(parent, label, widget)

    def _switch(self, parent, label: str, key: str) -> None:
        widget = self._Gtk.Switch()
        self._widgets[key] = widget
        self._row(parent, label, widget)

    def _spin(self, parent, label: str, key: str, minimum, maximum, step) -> None:
        widget = self._Gtk.SpinButton.new_with_range(minimum, maximum, step)
        self._widgets[key] = widget
        self._row(parent, label, widget)

    def _choice(self, parent, label: str, key: str, options) -> None:
        widget = self._Gtk.DropDown.new_from_strings([str(name) for _value, name in options])
        self._widgets[key] = widget
        self._option_ids[key] = [value for value, _name in options]
        self._row(parent, label, widget)

    def _replace_choice(self, key: str, options, selected) -> None:
        widget = self._widgets[key]
        ids = [value for value, _label in options]
        model = self._Gtk.StringList.new([str(label) for _value, label in options])
        widget.set_model(model)
        self._option_ids[key] = ids
        try:
            widget.set_selected(ids.index(selected))
        except ValueError:
            widget.set_selected(0)

    def _populate(self, snapshot: LinuxSettingsSnapshot) -> None:
        config = snapshot.config
        audio = _section(config, "audio")
        asr = _section(config, "asr")
        llm = _section(config, "llm")
        hotkey = _section(config, "hotkey")
        ui = _section(config, "ui")
        learning = _section(config, "dictionary_learning")
        context = _section(config, "context_personalization")
        self._widgets["api_key"].set_text(str(config.get("api_key") or ""))
        self._set_choice("ui.language", ui.get("language", "zh"))
        self._set_choice("default_mode", config.get("default_mode", "realtime_long"))
        self._set_choice("hotkey.linux_accelerator", hotkey.get("linux_accelerator", "F8"))
        self._widgets["auto_paste"].set_active(bool(config.get("auto_paste")))
        self._widgets["enable_polish"].set_active(bool(config.get("enable_polish")))
        self._replace_choice("asr.model", _catalog(snapshot.asr_models), asr.get("model"))
        self._replace_choice("llm.model", _catalog(snapshot.llm_models), llm.get("model"))
        self._set_choice("llm.level", llm.get("level", "minimal"))
        self._widgets["llm.temperature"].set_value(float(llm.get("temperature", 0.0)))
        self._widgets["llm.max_tokens"].set_value(float(llm.get("max_tokens", 1024)))
        self._widgets["llm.enable_thinking"].set_active(bool(llm.get("enable_thinking", False)))
        self._widgets["llm.structured"].set_active(bool(llm.get("structured", False)))
        self._set_choice("llm.tone", llm.get("tone", "neutral"))
        self._set_choice("llm.persona", llm.get("persona", "default"))
        self._widgets["dictionary_learning.enabled"].set_active(bool(learning.get("enabled", False)))
        self._widgets["dictionary_learning.excluded_bundle_ids"].set_text(
            ", ".join(str(value) for value in learning.get("excluded_bundle_ids", []))
        )
        self._widgets["context_personalization.enabled"].set_active(bool(context.get("enabled", True)))
        self._widgets["context_personalization.excluded_bundle_ids"].set_text(
            ", ".join(str(value) for value in context.get("excluded_bundle_ids", []))
        )
        devices = [(None, "System default")]
        devices.extend((item.get("name"), str(item.get("name"))) for item in snapshot.devices if item.get("name"))
        self._replace_choice("audio.input_device", devices, audio.get("input_device"))
        self._widgets["audio.gain"].set_value(float(audio.get("gain", 2.0)))
        self._widgets["audio.highpass_filter"].set_active(bool(audio.get("highpass_filter", True)))
        self._widgets["audio.highpass_freq"].set_value(float(audio.get("highpass_freq", 200)))
        self._widgets["audio.soft_limiter"].set_active(bool(audio.get("soft_limiter", True)))
        env = " · ".join(f"{key}: {value}" for key, value in snapshot.environment.items())
        context_total = int((snapshot.context_summary or {}).get("total", 0))
        self._status.set_text(f"Version {snapshot.version}\n{env}\nContext samples: {context_total}")
        self._populate_history(snapshot.recordings)
        self._populate_dictionary(snapshot.dictionary_entries)
        self._populate_candidates(snapshot.learning_candidates)

    def _set_choice(self, key: str, value: Any) -> None:
        ids = self._option_ids[key]
        self._widgets[key].set_selected(ids.index(value) if value in ids else 0)

    def _choice_value(self, key: str) -> Any:
        selected = int(self._widgets[key].get_selected())
        ids = self._option_ids[key]
        return ids[selected] if 0 <= selected < len(ids) else None

    def _collect(self) -> dict[str, Any]:
        return {
            "api_key": self._widgets["api_key"].get_text(),
            "ui.language": self._choice_value("ui.language"),
            "default_mode": self._choice_value("default_mode"),
            "hotkey.linux_accelerator": self._choice_value("hotkey.linux_accelerator"),
            "auto_paste": self._widgets["auto_paste"].get_active(),
            "enable_polish": self._widgets["enable_polish"].get_active(),
            "asr.model": self._choice_value("asr.model"),
            "llm.model": self._choice_value("llm.model"),
            "llm.level": self._choice_value("llm.level"),
            "llm.temperature": self._widgets["llm.temperature"].get_value(),
            "llm.max_tokens": self._widgets["llm.max_tokens"].get_value_as_int(),
            "llm.enable_thinking": self._widgets["llm.enable_thinking"].get_active(),
            "llm.structured": self._widgets["llm.structured"].get_active(),
            "llm.tone": self._choice_value("llm.tone"),
            "llm.persona": self._choice_value("llm.persona"),
            "dictionary_learning.enabled": self._widgets["dictionary_learning.enabled"].get_active(),
            "dictionary_learning.excluded_bundle_ids": self._widgets["dictionary_learning.excluded_bundle_ids"].get_text(),
            "context_personalization.enabled": self._widgets["context_personalization.enabled"].get_active(),
            "context_personalization.excluded_bundle_ids": self._widgets["context_personalization.excluded_bundle_ids"].get_text(),
            "audio.input_device": self._choice_value("audio.input_device"),
            "audio.gain": self._widgets["audio.gain"].get_value(),
            "audio.highpass_filter": self._widgets["audio.highpass_filter"].get_active(),
            "audio.highpass_freq": self._widgets["audio.highpass_freq"].get_value_as_int(),
            "audio.soft_limiter": self._widgets["audio.soft_limiter"].get_active(),
        }

    def _save(self, _button) -> None:
        try:
            result = self._on_save(build_linux_settings_updates(self._collect()))
            self._status.set_text(str(result.get("message") or "Settings saved"))
        except Exception as exc:
            self._status.set_text(str(exc))

    def _toggle_mic_test(self, button) -> None:
        if button.get_label().startswith("Stop"):
            self._on_stop_mic_test()
            button.set_label("Test microphone (5 seconds)")
            return
        button.set_label("Stop microphone test")
        self._on_start_mic_test(self._update_mic_level, self._mic_test_error)

    def _update_mic_level(self, level: float) -> None:
        self._mic_level.set_value(max(0.0, min(1.0, float(level))))

    def _mic_test_error(self, error: str) -> None:
        self._status.set_text(error)
        self._widgets["mic_test_button"].set_label("Test microphone (5 seconds)")

    def _clear_dynamic_rows(self, section, keep: int = 1) -> None:
        child = section.get_first_child()
        index = 0
        while child is not None:
            next_child = child.get_next_sibling()
            if index >= keep:
                section.remove(child)
            index += 1
            child = next_child

    def _populate_history(self, recordings: Sequence[Mapping[str, Any]]) -> None:
        section = self._dynamic_sections["history"]
        self._clear_dynamic_rows(section)
        for recording in recordings[:20]:
            recording_id = str(recording.get("id") or "")
            label = " · ".join(filter(None, (
                str(recording.get("created_at") or recording.get("timestamp") or ""),
                str(recording.get("mode") or ""),
                str(recording.get("status") or ""),
            ))) or recording_id
            row = self._Gtk.Box(orientation=self._Gtk.Orientation.HORIZONTAL, spacing=8)
            text = self._Gtk.Label(label=label, xalign=0)
            text.set_hexpand(True)
            row.append(text)
            for title, action in (("Retry", "retry_recording"), ("Notes", "meeting_notes"), ("Delete", "delete_recording")):
                button = self._Gtk.Button(label=title)
                button.connect("clicked", lambda _b, selected=action, rid=recording_id: self._run_action(selected, {"id": rid}))
                row.append(button)
            section.append(row)

    def _populate_dictionary(self, entries: Sequence[Mapping[str, Any]]) -> None:
        section = self._dynamic_sections["dictionary"]
        self._clear_dynamic_rows(section, keep=2)
        for entry in entries[:100]:
            term = str(entry.get("term") or "")
            aliases = ", ".join(str(value) for value in entry.get("aliases", []))
            row = self._Gtk.Box(orientation=self._Gtk.Orientation.HORIZONTAL, spacing=8)
            label = self._Gtk.Label(label=f"{term}  {aliases}".strip(), xalign=0)
            label.set_hexpand(True)
            remove = self._Gtk.Button(label="Remove")
            remove.connect("clicked", lambda _b, value=term: self._run_action("remove_dictionary", {"term": value}))
            row.append(label)
            row.append(remove)
            section.append(row)

    def _populate_candidates(self, candidates: Sequence[Mapping[str, Any]]) -> None:
        section = self._dynamic_sections["candidates"]
        self._clear_dynamic_rows(section)
        for candidate in candidates[:50]:
            job_id = str(candidate.get("id") or candidate.get("job_id") or "")
            label_text = str(candidate.get("term") or candidate.get("candidate") or job_id)
            row = self._Gtk.Box(orientation=self._Gtk.Orientation.HORIZONTAL, spacing=8)
            label = self._Gtk.Label(label=label_text, xalign=0)
            label.set_hexpand(True)
            row.append(label)
            for title, action in (("Approve", "approve_learning"), ("Reject", "reject_learning"), ("Undo", "undo_learning")):
                button = self._Gtk.Button(label=title)
                button.connect("clicked", lambda _b, selected=action, cid=job_id: self._run_action(selected, {"id": cid}))
                row.append(button)
            section.append(row)

    def _add_dictionary_entry(self, term, aliases) -> None:
        value = term.get_text().strip()
        if not value:
            self._status.set_text("Dictionary term is required")
            return
        self._run_action("add_dictionary", {
            "term": value,
            "aliases": _split_app_ids(aliases.get_text()),
        })
        term.set_text("")
        aliases.set_text("")

    def _run_action(self, action: str, payload: Mapping[str, Any]) -> None:
        try:
            result = self._on_action(action, payload)
            self._status.set_text(str(result.get("message") or "Done"))
        except Exception as exc:
            self._status.set_text(str(exc))

    def _on_close_request(self, _window) -> bool:
        self._on_stop_mic_test()
        return False


def _section(config: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = config.get(key, {})
    return value if isinstance(value, Mapping) else {}


def _catalog(items: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    return [
        (str(item["id"]), str(item.get("display_name") or item["id"]))
        for item in items
        if item.get("id")
    ]


__all__ = [
    "LinuxSettingsSnapshot",
    "LinuxSettingsWindow",
    "build_linux_settings_updates",
]
