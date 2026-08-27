"""Thread-owned Windows capsule and settings UI built with tkinter/ttk.

The notification-area host keeps the Win32 message loop on the main thread.
Tk owns a separate thread and receives immutable snapshots through a queue, so
microphone, ASR, and global-hook callbacks never call widget APIs directly.
"""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field
import math
import queue
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class CapsuleSnapshot:
    """Small, privacy-safe view model for the floating capsule."""

    state: str = "idle"
    mode: str = "realtime_long"
    language: str = "zh"
    stage: str = ""
    audio_level: float = 0.0
    trigger_label: str = "F8"
    can_cancel: bool = False


@dataclass(frozen=True)
class SettingsSnapshot:
    """Settings data copied at one idle runtime boundary."""

    version: str
    config: Mapping[str, Any]
    asr_models: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    llm_models: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    devices: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    trigger_browser_code: str = "F8"
    trigger_options: Sequence[tuple[str, str]] = field(default_factory=tuple)
    data_dir: str = ""
    config_path: str = ""
    log_path: str = ""


_MODE_LABELS = {
    "zh": {
        "walkie_talkie": "按住说话",
        "realtime_long": "长语音听写",
        "meeting": "会议记录",
        "prompt": "提示词",
    },
    "en": {
        "walkie_talkie": "Push to Talk",
        "realtime_long": "Long Dictation",
        "meeting": "Meeting",
        "prompt": "Prompt",
    },
}

_STATE_LABELS = {
    "zh": {
        "starting": "正在启动麦克风",
        "recording": "正在聆听",
        "stopping": "正在结束录音",
        "processing": "正在处理",
        "cancelling": "正在取消",
        "failed": "任务失败",
        "success": "听写完成",
    },
    "en": {
        "starting": "Starting microphone",
        "recording": "Listening",
        "stopping": "Stopping recording",
        "processing": "Processing",
        "cancelling": "Cancelling",
        "failed": "Task failed",
        "success": "Dictation complete",
    },
}


TRIGGER_DISPLAY_DEFAULTS: tuple[tuple[str, str], ...] = (
    ("F8", "F8"),
    ("F9", "F9"),
    ("F10", "F10"),
    ("F11", "F11"),
    ("F12", "F12"),
    ("CapsLock", "Caps Lock"),
    ("ControlRight", "Right Ctrl"),
    ("AltRight", "Right Alt"),
)


def _section(config: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = config.get(name, {})
    return value if isinstance(value, Mapping) else {}


def catalog_options(catalog: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """Return stable ``(id, display)`` options, omitting visual separators."""
    options: list[tuple[str, str]] = []
    for item in catalog:
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        display = item.get("display_name")
        label = str(display or model_id)
        options.append((model_id, label))
    return options


def device_options(devices: Iterable[Mapping[str, Any]]) -> list[tuple[str | None, str]]:
    """Normalize PortAudio device rows for a settings combobox."""
    options: list[tuple[str | None, str]] = [(None, "System default")]
    seen: set[str] = set()
    for item in devices:
        raw_name = item.get("name")
        if not isinstance(raw_name, str):
            continue
        name = raw_name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        label = f"{name} (Default)" if item.get("is_default") else name
        options.append((name, label))
    return options


def capsule_primary_text(snapshot: CapsuleSnapshot) -> str:
    """Resolve the capsule's primary line without exposing transcript text."""
    language = snapshot.language if snapshot.language in _STATE_LABELS else "en"
    if snapshot.state == "processing" and snapshot.stage:
        return snapshot.stage
    return _STATE_LABELS[language].get(snapshot.state, snapshot.state)


def capsule_secondary_text(snapshot: CapsuleSnapshot) -> str:
    """Resolve the mode/trigger hint displayed below the primary line."""
    language = snapshot.language if snapshot.language in _MODE_LABELS else "en"
    mode = _MODE_LABELS[language].get(snapshot.mode, snapshot.mode)
    if (
        snapshot.mode == "prompt"
        and snapshot.state in {"starting", "recording"}
        and snapshot.stage
    ):
        if language == "zh":
            return f"{snapshot.stage} · Esc 取消"
        return f"{snapshot.stage} · Esc to cancel"
    if language == "zh":
        return f"{mode} · {snapshot.trigger_label} · Esc 取消"
    return f"{mode} · {snapshot.trigger_label} · Esc to cancel"


def _parse_float(raw: Any, *, name: str, minimum: float, maximum: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return value


def _parse_int(raw: Any, *, name: str, minimum: int, maximum: int) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def build_settings_payload(values: Mapping[str, Any]) -> dict[str, Any]:
    """Validate GUI values and return runtime config keys plus trigger choice."""
    api_key = str(values.get("api_key") or "").strip()
    language = str(values.get("ui.language") or "zh")
    default_mode = str(values.get("default_mode") or "realtime_long")
    asr_language = str(values.get("asr.language") or "auto")
    gain_mode = str(values.get("audio.gain_mode") or "automatic")
    level = str(values.get("llm.level") or "minimal")
    persona = str(values.get("llm.persona") or "default")
    tone = str(values.get("llm.tone") or "neutral")
    polish_mode = str(values.get("llm.polish_mode") or "dictation")
    output_language = str(values.get("llm.output_language") or "auto")

    if language not in {"zh", "en"}:
        raise ValueError("Interface language is invalid")
    if default_mode not in {"walkie_talkie", "realtime_long", "meeting"}:
        raise ValueError("Default mode is invalid")
    if asr_language not in {"auto", "zh", "en"}:
        raise ValueError("Recognition language is invalid")
    if gain_mode not in {"automatic", "manual"}:
        raise ValueError("Gain mode is invalid")
    if level not in {"minimal", "balanced", "strong"}:
        raise ValueError("Polish strength is invalid")
    if persona not in {"default", "technical", "bilingual", "professional", "chat"}:
        raise ValueError("Polish persona is invalid")
    if tone not in {"neutral", "gentle", "direct"}:
        raise ValueError("Polish tone is invalid")
    if polish_mode not in {"dictation", "prompt"}:
        raise ValueError("Polish mode is invalid")
    if output_language not in {"auto", "zh", "en"}:
        raise ValueError("Polish output language is invalid")

    asr_model = str(values.get("asr.model") or "").strip()
    llm_model = str(values.get("llm.model") or "").strip()
    trigger = str(values.get("trigger_browser_code") or "F8").strip() or "F8"
    input_device = values.get("audio.input_device")
    if input_device is not None:
        input_device = str(input_device).strip() or None

    updates: dict[str, Any] = {
        "api_key": api_key,
        "ui.language": language,
        "default_mode": default_mode,
        "auto_paste": bool(values.get("auto_paste")),
        "restore_clipboard": bool(values.get("restore_clipboard")),
        "streaming_paste": bool(values.get("streaming_paste")),
        "enable_polish": bool(values.get("enable_polish")),
        "asr.model": asr_model,
        "asr.language": asr_language,
        "asr.use_dictionary_corpus": bool(values.get("asr.use_dictionary_corpus")),
        "llm.model": llm_model,
        "llm.enable_thinking": bool(values.get("llm.enable_thinking")),
        "llm.level": level,
        "llm.persona": persona,
        "llm.tone": tone,
        "llm.polish_mode": polish_mode,
        "llm.output_language": output_language,
        "llm.temperature": _parse_float(
            values.get("llm.temperature", 0.0),
            name="Temperature",
            minimum=0.0,
            maximum=2.0,
        ),
        "llm.max_tokens": _parse_int(
            values.get("llm.max_tokens", 1024),
            name="Max tokens",
            minimum=64,
            maximum=8192,
        ),
        "audio.input_device": input_device,
        "audio.gain_mode": gain_mode,
        "audio.gain": _parse_float(
            values.get("audio.gain", 8.0),
            name="Software gain",
            minimum=0.1,
            maximum=20.0,
        ),
        "audio.highpass_filter": bool(values.get("audio.highpass_filter")),
        "audio.highpass_freq": _parse_int(
            values.get("audio.highpass_freq", 200),
            name="High-pass frequency",
            minimum=20,
            maximum=1000,
        ),
        "audio.soft_limiter": bool(values.get("audio.soft_limiter")),
        "audio.waveform_ceiling_dbfs": _parse_float(
            values.get("audio.waveform_ceiling_dbfs", -6.0),
            name="Waveform ceiling",
            minimum=-40.0,
            maximum=-1.0,
        ),
        "context_personalization.enabled": bool(
            values.get("context_personalization.enabled")
        ),
    }
    return {"updates": updates, "trigger_browser_code": trigger}


class WindowsDesktopUI:
    """Own tkinter on one dedicated thread and expose thread-safe snapshots."""

    def __init__(
        self,
        *,
        on_capsule_cancel: Callable[[], Any],
        on_save_settings: Callable[[dict[str, Any]], Future | Any],
        on_refresh_devices: Callable[[], Future | Any],
        on_open_path: Callable[[str], Any],
    ) -> None:
        self._on_capsule_cancel = on_capsule_cancel
        self._on_save_settings = on_save_settings
        self._on_refresh_devices = on_refresh_devices
        self._on_open_path = on_open_path
        self._events: queue.Queue[tuple] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._startup_error: BaseException | None = None
        self._capsule_lock = threading.Lock()
        self._latest_capsule = CapsuleSnapshot()
        self._capsule_event_pending = False

    @property
    def startup_error(self) -> BaseException | None:
        return self._startup_error

    def start(self, *, timeout: float = 3.0) -> bool:
        thread = self._thread
        if thread is not None and thread.is_alive():
            return True
        self._ready.clear()
        self._stopped.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._run,
            name="vocal-more-windows-desktop-ui",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=max(0.1, float(timeout))):
            return False
        return self._startup_error is None

    def stop(self, *, timeout: float = 2.0) -> None:
        thread = self._thread
        if thread is None:
            return
        self._events.put(("shutdown",))
        if thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
        self._thread = None

    def update_capsule(self, snapshot: CapsuleSnapshot) -> None:
        with self._capsule_lock:
            self._latest_capsule = snapshot
            if self._capsule_event_pending:
                return
            self._capsule_event_pending = True
        self._events.put(("capsule",))

    def flash_success(self, *, language: str = "zh") -> None:
        self._events.put(("flash_success", language))

    def flash_error(self, message: str, *, language: str = "zh") -> None:
        self._events.put(("flash_error", str(message), language))

    def show_settings(self, snapshot: SettingsSnapshot) -> None:
        self._events.put(("show_settings", snapshot))

    def _consume_capsule(self) -> CapsuleSnapshot:
        with self._capsule_lock:
            snapshot = self._latest_capsule
            self._capsule_event_pending = False
        return snapshot

    def _run(self) -> None:
        root = None
        try:
            import tkinter as tk
            from tkinter import messagebox, ttk

            root = tk.Tk()
            root.withdraw()
            root.title("Vocal More Desktop UI")
            try:
                from .paths import bundled_resource_path

                icon_path = bundled_resource_path(
                    "resources", "windows", "VocalMore.ico"
                )
                if icon_path.exists():
                    root.iconbitmap(default=str(icon_path))
            except Exception:
                pass
            root.option_add("*Font", "{Segoe UI} 10")
            style = ttk.Style(root)
            for theme in ("vista", "xpnative", "clam"):
                if theme in style.theme_names():
                    style.theme_use(theme)
                    break
            style.configure("Accent.TButton", padding=(18, 7))
            style.configure("Settings.TCheckbutton", padding=(0, 3))

            capsule = _CapsuleWindow(
                root,
                on_cancel=self._on_capsule_cancel,
            )
            settings = _SettingsWindow(
                root,
                ttk=ttk,
                messagebox=messagebox,
                on_save=self._on_save_settings,
                on_refresh_devices=self._on_refresh_devices,
                on_open_path=self._on_open_path,
            )

            def drain() -> None:
                while True:
                    try:
                        event = self._events.get_nowait()
                    except queue.Empty:
                        break
                    kind = event[0]
                    if kind == "shutdown":
                        settings.close()
                        capsule.close()
                        root.after_idle(root.destroy)
                        return
                    if kind == "capsule":
                        capsule.apply(self._consume_capsule())
                    elif kind == "flash_success":
                        capsule.flash_success(language=event[1])
                    elif kind == "flash_error":
                        capsule.flash_error(event[1], language=event[2])
                    elif kind == "show_settings":
                        settings.show(event[1])
                root.after(20, drain)

            self._ready.set()
            root.after(0, drain)
            root.mainloop()
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
        finally:
            if root is not None:
                try:
                    root.destroy()
                except Exception:
                    pass
            self._stopped.set()


class _CapsuleWindow:
    WIDTH = 390
    HEIGHT = 76
    TRANSPARENT = "#010203"
    BACKGROUND = "#17191d"
    BORDER = "#343842"
    TEXT = "#f7f7f8"
    MUTED = "#aeb3be"
    GREEN = "#53d88b"
    BLUE = "#6aa8ff"
    ORANGE = "#f5b55f"
    RED = "#ff6b75"

    def __init__(self, root, *, on_cancel: Callable[[], Any]) -> None:
        import tkinter as tk

        self._tk = tk
        self._root = root
        self._on_cancel = on_cancel
        self._window = tk.Toplevel(root)
        self._window.withdraw()
        self._window.overrideredirect(True)
        self._window.attributes("-topmost", True)
        try:
            self._window.attributes("-toolwindow", True)
        except Exception:
            pass
        try:
            self._window.attributes("-transparentcolor", self.TRANSPARENT)
        except Exception:
            pass
        self._window.configure(bg=self.TRANSPARENT)
        self._canvas = tk.Canvas(
            self._window,
            width=self.WIDTH,
            height=self.HEIGHT,
            bg=self.TRANSPARENT,
            highlightthickness=0,
            bd=0,
        )
        self._canvas.pack()
        self._snapshot = CapsuleSnapshot()
        self._target_level = 0.0
        self._display_level = 0.0
        self._phase = 0.0
        self._visible = False
        self._transient_until = 0.0
        self._deferred_idle = False
        self._drag_origin: tuple[int, int, int, int] | None = None
        self._draw_shell()
        self._canvas.tag_bind("cancel", "<Button-1>", self._cancel)
        self._canvas.bind("<ButtonPress-1>", self._begin_drag)
        self._canvas.bind("<B1-Motion>", self._drag)
        self._window.after(33, self._tick)

    def _draw_shell(self) -> None:
        self._rounded_rect(2, 2, self.WIDTH - 2, self.HEIGHT - 2, 30, fill=self.BACKGROUND, outline=self.BORDER, width=1)
        self._canvas.create_oval(20, 26, 34, 40, fill=self.BLUE, outline="", tags=("status_dot",))
        self._canvas.create_text(
            48,
            27,
            anchor="w",
            fill=self.TEXT,
            font=("Segoe UI Semibold", 11),
            text="",
            tags=("primary",),
        )
        self._canvas.create_text(
            48,
            51,
            anchor="w",
            fill=self.MUTED,
            font=("Segoe UI", 8),
            text="",
            tags=("secondary",),
        )
        for index in range(11):
            x = 255 + index * 7
            self._canvas.create_line(
                x,
                31,
                x,
                45,
                fill=self.BLUE,
                width=3,
                capstyle="round",
                tags=("meter", f"meter_{index}"),
            )
        self._canvas.create_oval(
            self.WIDTH - 42,
            21,
            self.WIDTH - 14,
            49,
            fill="#252830",
            outline="#3b404b",
            tags=("cancel", "cancel_bg"),
        )
        self._canvas.create_text(
            self.WIDTH - 28,
            35,
            text="×",
            fill=self.MUTED,
            font=("Segoe UI", 13),
            tags=("cancel", "cancel_text"),
        )

    def _rounded_rect(self, x1, y1, x2, y2, radius, **kwargs):
        points = [
            x1 + radius,
            y1,
            x2 - radius,
            y1,
            x2,
            y1,
            x2,
            y1 + radius,
            x2,
            y2 - radius,
            x2,
            y2,
            x2 - radius,
            y2,
            x1 + radius,
            y2,
            x1,
            y2,
            x1,
            y2 - radius,
            x1,
            y1 + radius,
            x1,
            y1,
        ]
        return self._canvas.create_polygon(
            points,
            smooth=True,
            splinesteps=24,
            **kwargs,
        )

    def apply(self, snapshot: CapsuleSnapshot) -> None:
        self._snapshot = snapshot
        self._target_level = max(0.0, min(1.0, float(snapshot.audio_level)))
        if snapshot.state == "idle":
            if time.monotonic() < self._transient_until:
                self._deferred_idle = True
                return
            self.hide()
            return
        self._deferred_idle = False
        self._render_text()
        self.show()

    def flash_success(self, *, language: str) -> None:
        self._snapshot = CapsuleSnapshot(
            state="success",
            mode=self._snapshot.mode,
            language=language,
            trigger_label=self._snapshot.trigger_label,
        )
        self._transient_until = time.monotonic() + 1.35
        self._deferred_idle = False
        self._render_text()
        self.show()

    def flash_error(self, message: str, *, language: str) -> None:
        self._snapshot = CapsuleSnapshot(
            state="failed",
            mode=self._snapshot.mode,
            language=language,
            stage=message[:80],
            trigger_label=self._snapshot.trigger_label,
        )
        self._transient_until = time.monotonic() + 2.6
        self._deferred_idle = False
        self._render_text()
        self.show()

    def show(self) -> None:
        if not self._visible:
            width = self._window.winfo_screenwidth()
            x = max(8, (width - self.WIDTH) // 2)
            self._window.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+24")
            self._window.deiconify()
            self._window.lift()
            self._visible = True

    def hide(self) -> None:
        self._window.withdraw()
        self._visible = False
        self._display_level = 0.0
        self._target_level = 0.0

    def close(self) -> None:
        try:
            self._window.destroy()
        except Exception:
            pass

    def _render_text(self) -> None:
        primary = capsule_primary_text(self._snapshot)
        if self._snapshot.state == "failed" and self._snapshot.stage:
            primary = self._snapshot.stage
        self._canvas.itemconfigure("primary", text=primary)
        self._canvas.itemconfigure(
            "secondary",
            text=capsule_secondary_text(self._snapshot),
        )
        color = {
            "recording": self.GREEN,
            "starting": self.ORANGE,
            "stopping": self.ORANGE,
            "processing": self.BLUE,
            "cancelling": self.ORANGE,
            "success": self.GREEN,
            "failed": self.RED,
        }.get(self._snapshot.state, self.BLUE)
        self._canvas.itemconfigure("status_dot", fill=color)
        self._canvas.itemconfigure("meter", fill=color)
        state = "normal" if self._snapshot.can_cancel else "hidden"
        self._canvas.itemconfigure("cancel_bg", state=state)
        self._canvas.itemconfigure("cancel_text", state=state)

    def _tick(self) -> None:
        now = time.monotonic()
        if self._transient_until and now >= self._transient_until:
            self._transient_until = 0.0
            if self._deferred_idle or self._snapshot.state in {"success", "failed"}:
                self.hide()
                self._deferred_idle = False

        if self._visible:
            self._phase = (self._phase + 0.16) % (math.tau)
            if self._snapshot.state == "recording":
                self._display_level += (self._target_level - self._display_level) * 0.32
                base = max(0.05, self._display_level ** 0.58)
                heights = [
                    4 + 18 * base * (0.42 + 0.58 * abs(math.sin(self._phase + i * 0.78)))
                    for i in range(11)
                ]
            elif self._snapshot.state in {"processing", "stopping", "cancelling"}:
                heights = [
                    4 + 14 * (0.25 + 0.75 * max(0.0, math.sin(self._phase + i * 0.52)))
                    for i in range(11)
                ]
            else:
                heights = [6 + 4 * abs(math.sin(self._phase + i * 0.4)) for i in range(11)]
            for index, height in enumerate(heights):
                x = 255 + index * 7
                center = 38
                self._canvas.coords(
                    f"meter_{index}",
                    x,
                    center - height / 2,
                    x,
                    center + height / 2,
                )
        try:
            self._window.after(33, self._tick)
        except Exception:
            return

    def _cancel(self, _event=None) -> None:
        try:
            self._on_cancel()
        except Exception as exc:
            print(f"[WindowsDesktopUI] Capsule cancel failed: {exc}")

    def _begin_drag(self, event) -> None:
        tags = self._canvas.gettags("current")
        if "cancel" in tags:
            self._drag_origin = None
            return
        self._drag_origin = (
            event.x_root,
            event.y_root,
            self._window.winfo_x(),
            self._window.winfo_y(),
        )

    def _drag(self, event) -> None:
        if self._drag_origin is None:
            return
        start_x, start_y, window_x, window_y = self._drag_origin
        x = window_x + event.x_root - start_x
        y = window_y + event.y_root - start_y
        self._window.geometry(f"+{x}+{y}")


class _SettingsWindow:
    def __init__(
        self,
        root,
        *,
        ttk,
        messagebox,
        on_save: Callable[[dict[str, Any]], Future | Any],
        on_refresh_devices: Callable[[], Future | Any],
        on_open_path: Callable[[str], Any],
    ) -> None:
        import tkinter as tk

        self._tk = tk
        self._root = root
        self._ttk = ttk
        self._messagebox = messagebox
        self._on_save = on_save
        self._on_refresh_devices = on_refresh_devices
        self._on_open_path = on_open_path
        self._window = None
        self._snapshot: SettingsSnapshot | None = None
        self._vars: dict[str, Any] = {}
        self._device_display_to_value: dict[str, str | None] = {}
        self._asr_display_to_id: dict[str, str] = {}
        self._llm_display_to_id: dict[str, str] = {}
        self._trigger_display_to_code: dict[str, str] = {}
        self._save_button = None
        self._status_var = None
        self._device_combo = None
        self._api_entry = None

    def show(self, snapshot: SettingsSnapshot) -> None:
        self._snapshot = snapshot
        if self._window is None or not self._window.winfo_exists():
            self._build()
        self._populate(snapshot)
        self._window.deiconify()
        self._window.lift()
        self._window.focus_force()
        if self._api_entry is not None and not str(snapshot.config.get("api_key") or ""):
            self._api_entry.focus_set()

    def close(self) -> None:
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass
        self._window = None

    def _build(self) -> None:
        tk = self._tk
        ttk = self._ttk
        window = tk.Toplevel(self._root)
        window.withdraw()
        window.title("Vocal More Settings")
        window.geometry("760x650")
        window.minsize(700, 590)
        window.protocol("WM_DELETE_WINDOW", window.withdraw)
        self._window = window

        outer = ttk.Frame(window, padding=(18, 14, 18, 12))
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(
            header,
            text="Vocal More",
            font=("Segoe UI Semibold", 18),
        ).pack(side="left")
        self._version_var = tk.StringVar(value="")
        ttk.Label(header, textvariable=self._version_var).pack(side="right", pady=(7, 0))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        general = ttk.Frame(notebook, padding=18)
        recognition = ttk.Frame(notebook, padding=18)
        polish = ttk.Frame(notebook, padding=18)
        audio = ttk.Frame(notebook, padding=18)
        about = ttk.Frame(notebook, padding=18)
        notebook.add(general, text="General / 常规")
        notebook.add(recognition, text="Recognition / 识别")
        notebook.add(polish, text="Polishing / 润色")
        notebook.add(audio, text="Audio / 音频")
        notebook.add(about, text="About / 关于")

        for frame in (general, recognition, polish, audio, about):
            frame.columnconfigure(1, weight=1)

        self._vars = {
            "api_key": tk.StringVar(),
            "show_api_key": tk.BooleanVar(value=False),
            "ui.language": tk.StringVar(),
            "default_mode": tk.StringVar(),
            "trigger_browser_code": tk.StringVar(),
            "auto_paste": tk.BooleanVar(),
            "restore_clipboard": tk.BooleanVar(),
            "streaming_paste": tk.BooleanVar(),
            "enable_polish": tk.BooleanVar(),
            "asr.model": tk.StringVar(),
            "asr.language": tk.StringVar(),
            "asr.use_dictionary_corpus": tk.BooleanVar(),
            "audio.input_device": tk.StringVar(),
            "llm.model": tk.StringVar(),
            "llm.enable_thinking": tk.BooleanVar(),
            "llm.level": tk.StringVar(),
            "llm.persona": tk.StringVar(),
            "llm.tone": tk.StringVar(),
            "llm.polish_mode": tk.StringVar(),
            "llm.output_language": tk.StringVar(),
            "llm.temperature": tk.StringVar(),
            "llm.max_tokens": tk.StringVar(),
            "audio.gain_mode": tk.StringVar(),
            "audio.gain": tk.StringVar(),
            "audio.highpass_filter": tk.BooleanVar(),
            "audio.highpass_freq": tk.StringVar(),
            "audio.soft_limiter": tk.BooleanVar(),
            "audio.waveform_ceiling_dbfs": tk.StringVar(),
            "context_personalization.enabled": tk.BooleanVar(),
        }

        row = 0
        ttk.Label(general, text="DashScope API Key").grid(row=row, column=0, sticky="w", pady=6)
        api_frame = ttk.Frame(general)
        api_frame.grid(row=row, column=1, sticky="ew", pady=6)
        api_frame.columnconfigure(0, weight=1)
        self._api_entry = ttk.Entry(api_frame, textvariable=self._vars["api_key"], show="•")
        self._api_entry.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(
            api_frame,
            text="Show",
            variable=self._vars["show_api_key"],
            command=self._toggle_api_visibility,
        ).grid(row=0, column=1, padx=(10, 0))
        row += 1
        self._combo_row(general, row, "Interface language", "ui.language", ("zh", "en"))
        row += 1
        self._combo_row(
            general,
            row,
            "Default mode",
            "default_mode",
            ("realtime_long", "walkie_talkie", "meeting"),
        )
        row += 1
        self._combo_row(general, row, "Global trigger", "trigger_browser_code", ())
        self._trigger_combo = self._last_combo
        row += 1
        ttk.Checkbutton(
            general,
            text="Auto-paste recognized text into the active application",
            variable=self._vars["auto_paste"],
            style="Settings.TCheckbutton",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        ttk.Checkbutton(
            general,
            text="Restore the clipboard about 1 second after pasting",
            variable=self._vars["restore_clipboard"],
            style="Settings.TCheckbutton",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        ttk.Checkbutton(
            general,
            text="Paste finalized segments live during long dictation (skips polishing)",
            variable=self._vars["streaming_paste"],
            style="Settings.TCheckbutton",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        ttk.Checkbutton(
            general,
            text="Enable text polishing",
            variable=self._vars["enable_polish"],
            style="Settings.TCheckbutton",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        ttk.Checkbutton(
            general,
            text="Adapt output to the foreground application category",
            variable=self._vars["context_personalization.enabled"],
            style="Settings.TCheckbutton",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=5)

        row = 0
        self._combo_row(recognition, row, "ASR model", "asr.model", ())
        self._asr_combo = self._last_combo
        row += 1
        self._combo_row(recognition, row, "Recognition language", "asr.language", ("auto", "zh", "en"))
        row += 1
        ttk.Checkbutton(
            recognition,
            text="Include approved dictionary terms in the recognition corpus",
            variable=self._vars["asr.use_dictionary_corpus"],
            style="Settings.TCheckbutton",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        ttk.Label(recognition, text="Microphone").grid(row=row, column=0, sticky="w", pady=6)
        device_frame = ttk.Frame(recognition)
        device_frame.grid(row=row, column=1, sticky="ew", pady=6)
        device_frame.columnconfigure(0, weight=1)
        self._device_combo = ttk.Combobox(
            device_frame,
            textvariable=self._vars["audio.input_device"],
            state="readonly",
        )
        self._device_combo.grid(row=0, column=0, sticky="ew")
        ttk.Button(
            device_frame,
            text="Refresh",
            command=self._refresh_devices,
        ).grid(row=0, column=1, padx=(10, 0))

        row = 0
        self._combo_row(polish, row, "LLM model", "llm.model", ())
        self._llm_combo = self._last_combo
        row += 1
        self._combo_row(
            polish,
            row,
            "Input purpose",
            "llm.polish_mode",
            ("dictation", "prompt"),
        )
        row += 1
        self._combo_row(
            polish,
            row,
            "Output language",
            "llm.output_language",
            ("auto", "zh", "en"),
        )
        row += 1
        self._combo_row(polish, row, "Strength", "llm.level", ("minimal", "balanced", "strong"))
        row += 1
        self._combo_row(
            polish,
            row,
            "Persona",
            "llm.persona",
            ("default", "technical", "bilingual", "professional", "chat"),
        )
        row += 1
        self._combo_row(polish, row, "Tone", "llm.tone", ("neutral", "gentle", "direct"))
        row += 1
        self._entry_row(polish, row, "Temperature (0–2)", "llm.temperature")
        row += 1
        self._entry_row(polish, row, "Maximum output tokens", "llm.max_tokens")
        row += 1
        ttk.Checkbutton(
            polish,
            text="Enable model reasoning when supported",
            variable=self._vars["llm.enable_thinking"],
            style="Settings.TCheckbutton",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=5)

        row = 0
        self._combo_row(audio, row, "Gain control", "audio.gain_mode", ("automatic", "manual"))
        row += 1
        self._entry_row(audio, row, "Software gain (0.1–20)", "audio.gain")
        row += 1
        ttk.Checkbutton(
            audio,
            text="Enable high-pass filter",
            variable=self._vars["audio.highpass_filter"],
            style="Settings.TCheckbutton",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        self._entry_row(audio, row, "High-pass frequency (Hz)", "audio.highpass_freq")
        row += 1
        ttk.Checkbutton(
            audio,
            text="Enable soft limiter",
            variable=self._vars["audio.soft_limiter"],
            style="Settings.TCheckbutton",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        self._entry_row(audio, row, "Capsule waveform ceiling (dBFS)", "audio.waveform_ceiling_dbfs")
        row += 1
        ttk.Label(
            audio,
            text=(
                "On Windows, automatic gain currently uses the shared software fallback. "
                "Changes apply at the next recording boundary."
            ),
            wraplength=580,
            foreground="#555555",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(14, 0))

        self._data_var = tk.StringVar()
        self._config_var = tk.StringVar()
        self._log_var = tk.StringVar()
        ttk.Label(about, text="Version", font=("Segoe UI Semibold", 10)).grid(row=0, column=0, sticky="nw", pady=5)
        ttk.Label(about, textvariable=self._version_var).grid(row=0, column=1, sticky="w", pady=5)
        ttk.Label(about, text="Data folder", font=("Segoe UI Semibold", 10)).grid(row=1, column=0, sticky="nw", pady=5)
        ttk.Label(about, textvariable=self._data_var, wraplength=510).grid(row=1, column=1, sticky="w", pady=5)
        ttk.Label(about, text="Config file", font=("Segoe UI Semibold", 10)).grid(row=2, column=0, sticky="nw", pady=5)
        ttk.Label(about, textvariable=self._config_var, wraplength=510).grid(row=2, column=1, sticky="w", pady=5)
        ttk.Label(about, text="Log file", font=("Segoe UI Semibold", 10)).grid(row=3, column=0, sticky="nw", pady=5)
        ttk.Label(about, textvariable=self._log_var, wraplength=510).grid(row=3, column=1, sticky="w", pady=5)
        about_buttons = ttk.Frame(about)
        about_buttons.grid(row=4, column=0, columnspan=2, sticky="w", pady=(16, 0))
        ttk.Button(about_buttons, text="Open data folder", command=lambda: self._open("data")).pack(side="left")
        ttk.Button(about_buttons, text="Open config", command=lambda: self._open("config")).pack(side="left", padx=8)
        ttk.Button(about_buttons, text="Open log", command=lambda: self._open("log")).pack(side="left")
        ttk.Label(
            about,
            text=(
                "The installer and executable are currently unsigned. User data is kept outside "
                "the installation directory and is preserved during uninstall."
            ),
            wraplength=600,
            foreground="#555555",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(18, 0))

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(12, 0))
        self._status_var = tk.StringVar(value="")
        ttk.Label(footer, textvariable=self._status_var).pack(side="left")
        ttk.Button(footer, text="Close", command=window.withdraw).pack(side="right")
        self._save_button = ttk.Button(
            footer,
            text="Save",
            style="Accent.TButton",
            command=self._save,
        )
        self._save_button.pack(side="right", padx=(0, 8))

    def _combo_row(self, parent, row: int, label: str, key: str, values: Sequence[str]) -> None:
        self._ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=6)
        combo = self._ttk.Combobox(
            parent,
            textvariable=self._vars[key],
            values=tuple(values),
            state="readonly",
        )
        combo.grid(row=row, column=1, sticky="ew", pady=6)
        self._last_combo = combo

    def _entry_row(self, parent, row: int, label: str, key: str) -> None:
        self._ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=6)
        self._ttk.Entry(parent, textvariable=self._vars[key]).grid(
            row=row,
            column=1,
            sticky="ew",
            pady=6,
        )

    def _populate(self, snapshot: SettingsSnapshot) -> None:
        config = snapshot.config
        audio = _section(config, "audio")
        asr = _section(config, "asr")
        llm = _section(config, "llm")
        context = _section(config, "context_personalization")

        self._version_var.set(snapshot.version)
        self._data_var.set(snapshot.data_dir)
        self._config_var.set(snapshot.config_path)
        self._log_var.set(snapshot.log_path)
        self._vars["api_key"].set(str(config.get("api_key") or ""))
        self._vars["ui.language"].set(str(_section(config, "ui").get("language") or "zh"))
        self._vars["default_mode"].set(str(config.get("default_mode") or "realtime_long"))
        self._vars["auto_paste"].set(bool(config.get("auto_paste", True)))
        self._vars["restore_clipboard"].set(bool(config.get("restore_clipboard", True)))
        self._vars["streaming_paste"].set(bool(config.get("streaming_paste", False)))
        self._vars["enable_polish"].set(bool(config.get("enable_polish", True)))
        self._vars["asr.language"].set(str(asr.get("language") or "auto"))
        self._vars["asr.use_dictionary_corpus"].set(bool(asr.get("use_dictionary_corpus", True)))
        self._vars["llm.enable_thinking"].set(bool(llm.get("enable_thinking", False)))
        self._vars["llm.level"].set(str(llm.get("level") or "minimal"))
        self._vars["llm.persona"].set(str(llm.get("persona") or "default"))
        self._vars["llm.tone"].set(str(llm.get("tone") or "neutral"))
        self._vars["llm.polish_mode"].set(str(llm.get("polish_mode") or "dictation"))
        self._vars["llm.output_language"].set(str(llm.get("output_language") or "auto"))
        self._vars["llm.temperature"].set(str(llm.get("temperature", 0.0)))
        self._vars["llm.max_tokens"].set(str(llm.get("max_tokens", 1024)))
        self._vars["audio.gain_mode"].set(str(audio.get("gain_mode") or "automatic"))
        self._vars["audio.gain"].set(str(audio.get("gain", 8.0)))
        self._vars["audio.highpass_filter"].set(bool(audio.get("highpass_filter", True)))
        self._vars["audio.highpass_freq"].set(str(audio.get("highpass_freq", 200)))
        self._vars["audio.soft_limiter"].set(bool(audio.get("soft_limiter", True)))
        self._vars["audio.waveform_ceiling_dbfs"].set(str(audio.get("waveform_ceiling_dbfs", -6.0)))
        self._vars["context_personalization.enabled"].set(bool(context.get("enabled", True)))

        self._set_catalog_combo(
            self._asr_combo,
            self._vars["asr.model"],
            catalog_options(snapshot.asr_models),
            str(asr.get("model") or ""),
            target_map="asr",
        )
        self._set_catalog_combo(
            self._llm_combo,
            self._vars["llm.model"],
            catalog_options(snapshot.llm_models),
            str(llm.get("model") or ""),
            target_map="llm",
        )
        trigger_options = tuple(snapshot.trigger_options) or TRIGGER_DISPLAY_DEFAULTS
        self._trigger_display_to_code = {label: code for code, label in trigger_options}
        self._trigger_combo.configure(values=tuple(label for _, label in trigger_options))
        trigger_label = next(
            (label for code, label in trigger_options if code == snapshot.trigger_browser_code),
            trigger_options[0][1],
        )
        self._vars["trigger_browser_code"].set(trigger_label)
        self._set_devices(snapshot.devices, selected=audio.get("input_device"))
        self._status_var.set("")
        self._save_button.configure(state="normal")
        self._toggle_api_visibility()

    def _set_catalog_combo(self, combo, variable, options, selected_id: str, *, target_map: str) -> None:
        display_to_id = {label: model_id for model_id, label in options}
        if target_map == "asr":
            self._asr_display_to_id = display_to_id
        else:
            self._llm_display_to_id = display_to_id
        combo.configure(values=tuple(display_to_id))
        selected = next((label for model_id, label in options if model_id == selected_id), "")
        if not selected and options:
            selected = options[0][1]
        variable.set(selected)

    def _set_devices(self, devices, *, selected=None) -> None:
        options = device_options(devices)
        self._device_display_to_value = {label: value for value, label in options}
        labels = tuple(label for _, label in options)
        self._device_combo.configure(values=labels)
        selected_label = next((label for value, label in options if value == selected), labels[0])
        self._vars["audio.input_device"].set(selected_label)

    def _toggle_api_visibility(self) -> None:
        if self._api_entry is not None:
            self._api_entry.configure(show="" if self._vars["show_api_key"].get() else "•")

    def _collect(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for key, variable in self._vars.items():
            if key == "show_api_key":
                continue
            values[key] = variable.get()
        values["asr.model"] = self._asr_display_to_id.get(str(values["asr.model"]), "")
        values["llm.model"] = self._llm_display_to_id.get(str(values["llm.model"]), "")
        values["audio.input_device"] = self._device_display_to_value.get(
            str(values["audio.input_device"])
        )
        values["trigger_browser_code"] = self._trigger_display_to_code.get(
            str(values["trigger_browser_code"]),
            "F8",
        )
        return build_settings_payload(values)

    def _save(self) -> None:
        try:
            payload = self._collect()
        except ValueError as exc:
            self._messagebox.showerror("Vocal More", str(exc), parent=self._window)
            return
        self._save_button.configure(state="disabled")
        self._status_var.set("Saving…")
        try:
            result = self._on_save(payload)
        except Exception as exc:
            self._finish_operation(error=exc)
            return
        self._await(result, self._finish_save)

    def _finish_save(self, result: Any) -> None:
        if isinstance(result, Mapping) and not result.get("ok", True):
            self._finish_operation(error=RuntimeError(str(result.get("message") or "Save failed")))
            return
        message = "Saved"
        if isinstance(result, Mapping) and result.get("message"):
            message = str(result["message"])
        self._status_var.set(message)
        self._save_button.configure(state="normal")

    def _refresh_devices(self) -> None:
        self._status_var.set("Refreshing microphones…")
        try:
            result = self._on_refresh_devices()
        except Exception as exc:
            self._finish_operation(error=exc)
            return

        def finished(devices: Any) -> None:
            selected = self._device_display_to_value.get(
                str(self._vars["audio.input_device"].get())
            )
            self._set_devices(devices or (), selected=selected)
            self._status_var.set("Microphone list refreshed")

        self._await(result, finished)

    def _await(self, result: Any, callback: Callable[[Any], None]) -> None:
        if not isinstance(result, Future) and not (
            hasattr(result, "done") and hasattr(result, "result")
        ):
            callback(result)
            return

        def poll() -> None:
            try:
                done = result.done()
            except Exception as exc:
                self._finish_operation(error=exc)
                return
            if not done:
                self._window.after(50, poll)
                return
            try:
                value = result.result()
            except Exception as exc:
                self._finish_operation(error=exc)
                return
            callback(value)

        self._window.after(50, poll)

    def _finish_operation(self, *, error: BaseException) -> None:
        self._save_button.configure(state="normal")
        self._status_var.set("")
        self._messagebox.showerror("Vocal More", str(error), parent=self._window)

    def _open(self, target: str) -> None:
        try:
            self._on_open_path(target)
        except Exception as exc:
            self._messagebox.showerror("Vocal More", str(exc), parent=self._window)


__all__ = [
    "CapsuleSnapshot",
    "SettingsSnapshot",
    "TRIGGER_DISPLAY_DEFAULTS",
    "WindowsDesktopUI",
    "build_settings_payload",
    "capsule_primary_text",
    "capsule_secondary_text",
    "catalog_options",
    "device_options",
]
