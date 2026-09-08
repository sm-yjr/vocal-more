"""Persistent microphone failure and safe, user-initiated retry feedback."""

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MicrophoneStatus:
    error: str
    recovery_probe: Callable[[], dict] | None = field(default=None, repr=False)
    phase: str = field(default="microphone_failed", init=False)

    def display_text(self, language: str) -> tuple[str, str]:
        chinese = language == "zh"
        recovery = self.recovery_probe() if self.recovery_probe else None
        if recovery and recovery["busy"]:
            title = "麦克风正在恢复" if chinese else "Microphone recovery pending"
            hint = ("上次音频任务尚未结束，结束后可再次按听写键。若持续无响应，请重启应用。点击 × 关闭。"
                    if chinese else "Waiting for the previous audio task to finish before retrying. If it stays unresponsive, restart the app. Click × to close.")
        elif recovery:
            title = "麦克风可以重试" if chinese else "Microphone retry available"
            hint = ("上次音频任务已结束，请再次按听写键重试。点击 × 关闭。"
                    if chinese else "The previous audio task has finished. Press your dictation key to retry. Click × to close.")
        else:
            title = "麦克风启动失败" if chinese else "Microphone startup failed"
            hint = "处理后请再次按听写键，点击 × 关闭。" if chinese else "Press your dictation key again when ready. Click × to close."
        return title, f"{self.error}\n{hint}".strip()
