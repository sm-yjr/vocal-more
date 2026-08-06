"""Native Windows notification-area host implemented with the Win32 API."""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import queue
import sys
from typing import Callable

from .paths import bundled_resource_path


# Window and shell messages.
_WM_NULL = 0x0000
_WM_DESTROY = 0x0002
_WM_CLOSE = 0x0010
_WM_COMMAND = 0x0111
_WM_CONTEXTMENU = 0x007B
_WM_LBUTTONDBLCLK = 0x0203
_WM_RBUTTONUP = 0x0205
_WM_APP = 0x8000
_WM_TRAY = _WM_APP + 1
_WM_UI_EVENT = _WM_APP + 2

# Shell_NotifyIcon operations and fields.
_NIM_ADD = 0x00000000
_NIM_MODIFY = 0x00000001
_NIM_DELETE = 0x00000002
_NIM_SETVERSION = 0x00000004
_NIF_MESSAGE = 0x00000001
_NIF_ICON = 0x00000002
_NIF_TIP = 0x00000004
_NIF_INFO = 0x00000010
_NOTIFYICON_VERSION_4 = 4
_NIIF_INFO = 0x00000001
_NIIF_WARNING = 0x00000002
_NIIF_ERROR = 0x00000003

# Menu flags.
_MF_STRING = 0x00000000
_MF_GRAYED = 0x00000001
_MF_CHECKED = 0x00000008
_MF_POPUP = 0x00000010
_MF_SEPARATOR = 0x00000800
_TPM_RIGHTBUTTON = 0x0002
_TPM_RETURNCMD = 0x0100

_IMAGE_ICON = 1
_LR_LOADFROMFILE = 0x0010
_LR_DEFAULTSIZE = 0x0040
_IDI_APPLICATION = 32512

_CMD_TOGGLE = 1001
_CMD_MODE_WALKIE = 1002
_CMD_MODE_REALTIME = 1003
_CMD_MODE_MEETING = 1004
_CMD_AUTO_PASTE = 1005
_CMD_SETTINGS = 1006
_CMD_OPEN_CONFIG = 1007
_CMD_OPEN_DATA = 1008
_CMD_QUIT = 1009

_ACTIONS = {
    _CMD_TOGGLE: "toggle",
    _CMD_MODE_WALKIE: "mode:walkie_talkie",
    _CMD_MODE_REALTIME: "mode:realtime_long",
    _CMD_MODE_MEETING: "mode:meeting",
    _CMD_AUTO_PASTE: "toggle_auto_paste",
    _CMD_SETTINGS: "settings",
    _CMD_OPEN_CONFIG: "open_config",
    _CMD_OPEN_DATA: "open_data",
    _CMD_QUIT: "quit",
}


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", _GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


_LRESULT = ctypes.c_ssize_t
_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_WNDPROC = _WINFUNCTYPE(
    _LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


@dataclass(frozen=True)
class TraySnapshot:
    """State needed to render the tray tooltip and menu."""

    state: str = "idle"
    mode: str = "realtime_long"
    auto_paste: bool = True
    trigger_label: str = "F8"
    api_configured: bool = False
    language: str = "zh"
    processing_stage: str = ""


_TEXT = {
    "zh": {
        "status": "状态",
        "idle": "空闲",
        "starting": "正在启动录音",
        "recording": "正在录音",
        "stopping": "正在结束录音",
        "processing": "正在处理",
        "cancelling": "正在取消",
        "failed": "失败",
        "start": "开始听写 ({trigger})",
        "stop": "停止听写 ({trigger})",
        "cancel": "取消当前任务",
        "settings": "设置…",
        "mode": "录音模式",
        "walkie_talkie": "按住说话",
        "realtime_long": "长语音听写",
        "meeting": "会议记录",
        "auto_paste": "自动粘贴",
        "open_config": "高级：打开配置文件",
        "open_data": "打开数据目录",
        "quit": "退出",
        "api_missing": "未配置 DashScope API Key",
    },
    "en": {
        "status": "Status",
        "idle": "Idle",
        "starting": "Starting microphone",
        "recording": "Recording",
        "stopping": "Stopping",
        "processing": "Processing",
        "cancelling": "Cancelling",
        "failed": "Failed",
        "start": "Start dictation ({trigger})",
        "stop": "Stop dictation ({trigger})",
        "cancel": "Cancel current task",
        "settings": "Settings…",
        "mode": "Recording mode",
        "walkie_talkie": "Push to Talk",
        "realtime_long": "Long Dictation",
        "meeting": "Meeting",
        "auto_paste": "Auto paste",
        "open_config": "Advanced: Open config file",
        "open_data": "Open data folder",
        "quit": "Quit",
        "api_missing": "DashScope API key is not configured",
    },
}


class WindowsTray:
    """Own a hidden Win32 window and one notification-area icon."""

    def __init__(
        self,
        *,
        on_action: Callable[[str], None],
        get_snapshot: Callable[[], TraySnapshot],
    ) -> None:
        if sys.platform != "win32":
            raise RuntimeError("WindowsTray can only run on Windows")

        self._on_action = on_action
        self._get_snapshot = get_snapshot
        self._events: queue.Queue[tuple] = queue.Queue()
        self._hwnd = None
        self._hinstance = None
        self._icon_added = False
        self._closing = False
        self._class_name = "VocalMoreWindowsTrayWindow"
        self._owned_icon = None

        self._user32 = ctypes.windll.user32
        self._shell32 = ctypes.windll.shell32
        self._kernel32 = ctypes.windll.kernel32
        self._configure_api()

        self._wndproc = _WNDPROC(self._window_proc)
        self._nid = _NOTIFYICONDATAW()
        taskbar_created = self._user32.RegisterWindowMessageW("TaskbarCreated")
        self._taskbar_created = int(taskbar_created) or None

    def _configure_api(self) -> None:
        user32 = self._user32
        shell32 = self._shell32
        kernel32 = self._kernel32

        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        kernel32.GetLastError.argtypes = []
        kernel32.GetLastError.restype = wintypes.DWORD
        user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = _LRESULT
        user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
        user32.LoadIconW.restype = wintypes.HICON
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.DestroyIcon.argtypes = [wintypes.HICON]
        user32.DestroyIcon.restype = wintypes.BOOL
        user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        user32.RegisterWindowMessageW.restype = wintypes.UINT
        user32.CreatePopupMenu.argtypes = []
        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.DestroyMenu.argtypes = [wintypes.HMENU]
        user32.DestroyMenu.restype = wintypes.BOOL
        user32.AppendMenuW.argtypes = [
            wintypes.HMENU,
            wintypes.UINT,
            ctypes.c_size_t,
            wintypes.LPCWSTR,
        ]
        user32.AppendMenuW.restype = wintypes.BOOL
        user32.TrackPopupMenu.argtypes = [
            wintypes.HMENU,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        ]
        user32.TrackPopupMenu.restype = wintypes.UINT
        user32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.PostQuitMessage.restype = None
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.GetMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = _LRESULT
        shell32.Shell_NotifyIconW.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(_NOTIFYICONDATAW),
        ]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL

    def run(self) -> None:
        """Create the icon and run the Win32 message loop until Quit."""
        self._create_window()
        self._add_icon()
        if not self._icon_added:
            raise RuntimeError("Could not add the Vocal More notification-area icon")
        self._drain_events()

        message = wintypes.MSG()
        while True:
            result = self._user32.GetMessageW(
                ctypes.byref(message),
                None,
                0,
                0,
            )
            if result == -1:
                raise ctypes.WinError(self._kernel32.GetLastError())
            if result == 0:
                return
            self._user32.TranslateMessage(ctypes.byref(message))
            self._user32.DispatchMessageW(ctypes.byref(message))

    def refresh(self) -> None:
        self._events.put(("refresh",))
        self._wake()

    def notify(self, title: str, message: str, *, level: str = "info") -> None:
        self._events.put(("notify", str(title), str(message), str(level)))
        self._wake()

    def request_quit(self) -> None:
        hwnd = self._hwnd
        if hwnd:
            self._user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)

    def _wake(self) -> None:
        hwnd = self._hwnd
        if hwnd:
            self._user32.PostMessageW(hwnd, _WM_UI_EVENT, 0, 0)

    def _create_window(self) -> None:
        self._hinstance = self._kernel32.GetModuleHandleW(None)
        icon = self._load_application_icon()
        window_class = _WNDCLASSW(
            style=0,
            lpfnWndProc=self._wndproc,
            cbClsExtra=0,
            cbWndExtra=0,
            hInstance=self._hinstance,
            hIcon=icon,
            hCursor=None,
            hbrBackground=None,
            lpszMenuName=None,
            lpszClassName=self._class_name,
        )
        atom = self._user32.RegisterClassW(ctypes.byref(window_class))
        if not atom:
            error = self._kernel32.GetLastError()
            # ERROR_CLASS_ALREADY_EXISTS is safe for a restarted host process.
            if error != 1410:
                raise ctypes.WinError(error)

        self._hwnd = self._user32.CreateWindowExW(
            0,
            self._class_name,
            "Vocal More",
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            self._hinstance,
            None,
        )
        if not self._hwnd:
            raise ctypes.WinError(self._kernel32.GetLastError())

    def _window_proc(self, hwnd, message, wparam, lparam):
        try:
            if message == _WM_TRAY:
                # NOTIFYICON_VERSION_4 stores the event code in LOWORD(lParam).
                mouse_message = int(lparam) & 0xFFFF
                if mouse_message in {_WM_RBUTTONUP, _WM_CONTEXTMENU}:
                    self._show_menu()
                    return 0
                if mouse_message == _WM_LBUTTONDBLCLK:
                    self._dispatch_action("settings")
                    return 0
            elif message == _WM_COMMAND:
                command_id = int(wparam) & 0xFFFF
                action = _ACTIONS.get(command_id)
                if action:
                    if action == "quit":
                        self._close_window()
                    else:
                        self._dispatch_action(action)
                    return 0
            elif message == _WM_UI_EVENT:
                self._drain_events()
                return 0
            elif message == _WM_CLOSE:
                self._close_window()
                return 0
            elif message == _WM_DESTROY:
                self._remove_icon()
                self._destroy_owned_icon()
                self._user32.PostQuitMessage(0)
                return 0
            elif self._taskbar_created and message == self._taskbar_created:
                self._icon_added = False
                self._add_icon()
                return 0
        except Exception as exc:
            print(f"[WindowsTray] Window procedure failed: {exc}")
        return self._user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _close_window(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self._on_action("quit")
        except Exception as exc:
            print(f"[WindowsTray] Quit callback failed: {exc}")
        self._remove_icon()
        hwnd = self._hwnd
        self._hwnd = None
        if hwnd:
            self._user32.DestroyWindow(hwnd)

    def _dispatch_action(self, action: str) -> None:
        try:
            self._on_action(action)
        except Exception as exc:
            print(f"[WindowsTray] Action '{action}' failed: {exc}")
            self.notify("Vocal More", str(exc), level="error")

    def _add_icon(self) -> None:
        if not self._hwnd:
            return
        snapshot = self._get_snapshot()
        self._nid = _NOTIFYICONDATAW()
        self._nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        self._nid.hWnd = self._hwnd
        self._nid.uID = 1
        self._nid.uFlags = _NIF_MESSAGE | _NIF_ICON | _NIF_TIP
        self._nid.uCallbackMessage = _WM_TRAY
        self._nid.hIcon = self._load_application_icon()
        self._nid.szTip = self._tooltip(snapshot)[:127]
        self._icon_added = bool(
            self._shell32.Shell_NotifyIconW(_NIM_ADD, ctypes.byref(self._nid))
        )
        if self._icon_added:
            self._nid.uTimeoutOrVersion = _NOTIFYICON_VERSION_4
            self._shell32.Shell_NotifyIconW(
                _NIM_SETVERSION,
                ctypes.byref(self._nid),
            )

    def _remove_icon(self) -> None:
        if self._icon_added:
            self._shell32.Shell_NotifyIconW(_NIM_DELETE, ctypes.byref(self._nid))
            self._icon_added = False

    def _apply_snapshot(self) -> None:
        if not self._icon_added:
            return
        snapshot = self._get_snapshot()
        self._nid.uFlags = _NIF_TIP | _NIF_MESSAGE | _NIF_ICON
        self._nid.szTip = self._tooltip(snapshot)[:127]
        self._shell32.Shell_NotifyIconW(_NIM_MODIFY, ctypes.byref(self._nid))

    def _show_balloon(self, title: str, message: str, level: str) -> None:
        if not self._icon_added:
            return
        self._nid.uFlags = _NIF_INFO
        self._nid.szInfoTitle = title[:63]
        self._nid.szInfo = message[:255]
        self._nid.dwInfoFlags = {
            "error": _NIIF_ERROR,
            "warning": _NIIF_WARNING,
        }.get(level, _NIIF_INFO)
        self._shell32.Shell_NotifyIconW(_NIM_MODIFY, ctypes.byref(self._nid))

    def _drain_events(self) -> None:
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return
            if event[0] == "refresh":
                self._apply_snapshot()
            elif event[0] == "notify":
                _, title, message, level = event
                self._show_balloon(title, message, level)

    def _show_menu(self) -> None:
        snapshot = self._get_snapshot()
        text = _TEXT.get(snapshot.language, _TEXT["en"])
        menu = self._user32.CreatePopupMenu()
        if not menu:
            return
        mode_menu = self._user32.CreatePopupMenu()
        if not mode_menu:
            self._user32.DestroyMenu(menu)
            return

        state_label = self._state_label(snapshot, text)
        self._append(menu, _MF_STRING | _MF_GRAYED, 0, f"{text['status']}: {state_label}")
        if not snapshot.api_configured:
            self._append(menu, _MF_STRING | _MF_GRAYED, 0, text["api_missing"])
        self._append(menu, _MF_SEPARATOR, 0, None)

        toggle_label = self._toggle_label(snapshot, text)
        self._append(menu, _MF_STRING, _CMD_TOGGLE, toggle_label)
        self._append(menu, _MF_STRING, _CMD_SETTINGS, text["settings"])
        self._append(menu, _MF_SEPARATOR, 0, None)

        mode_commands = (
            ("walkie_talkie", _CMD_MODE_WALKIE),
            ("realtime_long", _CMD_MODE_REALTIME),
            ("meeting", _CMD_MODE_MEETING),
        )
        for mode, command in mode_commands:
            flags = _MF_STRING | (_MF_CHECKED if snapshot.mode == mode else 0)
            self._append(mode_menu, flags, command, text[mode])
        self._append(menu, _MF_POPUP, int(mode_menu), text["mode"])

        auto_flags = _MF_STRING | (_MF_CHECKED if snapshot.auto_paste else 0)
        self._append(menu, auto_flags, _CMD_AUTO_PASTE, text["auto_paste"])
        self._append(menu, _MF_SEPARATOR, 0, None)
        self._append(menu, _MF_STRING, _CMD_OPEN_CONFIG, text["open_config"])
        self._append(menu, _MF_STRING, _CMD_OPEN_DATA, text["open_data"])
        self._append(menu, _MF_SEPARATOR, 0, None)
        self._append(menu, _MF_STRING, _CMD_QUIT, text["quit"])

        point = _POINT()
        self._user32.GetCursorPos(ctypes.byref(point))
        self._user32.SetForegroundWindow(self._hwnd)
        command = self._user32.TrackPopupMenu(
            menu,
            _TPM_RIGHTBUTTON | _TPM_RETURNCMD,
            point.x,
            point.y,
            0,
            self._hwnd,
            None,
        )
        self._user32.DestroyMenu(menu)
        # Required by the notification-area context-menu contract so the menu
        # reliably dismisses when the user clicks elsewhere.
        self._user32.PostMessageW(self._hwnd, _WM_NULL, 0, 0)
        if command:
            self._user32.PostMessageW(self._hwnd, _WM_COMMAND, command, 0)

    def _load_application_icon(self):
        if self._owned_icon:
            return self._owned_icon
        candidates = (
            bundled_resource_path("resources", "windows", "VocalMore.ico"),
            bundled_resource_path("packaging", "windows", "VocalMore.ico"),
        )
        for path in candidates:
            if not path.exists():
                continue
            handle = self._user32.LoadImageW(
                None,
                str(path),
                _IMAGE_ICON,
                0,
                0,
                _LR_LOADFROMFILE | _LR_DEFAULTSIZE,
            )
            if handle:
                self._owned_icon = handle
                return handle
        return self._user32.LoadIconW(None, ctypes.c_void_p(_IDI_APPLICATION))

    def _destroy_owned_icon(self) -> None:
        if self._owned_icon:
            self._user32.DestroyIcon(self._owned_icon)
            self._owned_icon = None

    def _append(self, menu, flags: int, identifier: int, label: str | None) -> None:
        self._user32.AppendMenuW(
            menu,
            flags,
            ctypes.c_size_t(identifier),
            label,
        )

    @staticmethod
    def _toggle_label(snapshot: TraySnapshot, text: dict[str, str]) -> str:
        state = snapshot.state
        if state == "idle" or state == "failed":
            return text["start"].format(trigger=snapshot.trigger_label)
        if state in {"starting", "recording"}:
            return text["stop"].format(trigger=snapshot.trigger_label)
        return text["cancel"]

    @staticmethod
    def _state_label(snapshot: TraySnapshot, text: dict[str, str]) -> str:
        if snapshot.state == "processing" and snapshot.processing_stage:
            return snapshot.processing_stage
        return text.get(snapshot.state, snapshot.state)

    @classmethod
    def _tooltip(cls, snapshot: TraySnapshot) -> str:
        text = _TEXT.get(snapshot.language, _TEXT["en"])
        state = cls._state_label(snapshot, text)
        return f"Vocal More — {state} — {snapshot.trigger_label}"


__all__ = ["TraySnapshot", "WindowsTray"]
