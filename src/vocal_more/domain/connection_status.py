"""User-visible connection attempts, shared by engine and UI adapters."""

from dataclasses import dataclass

CONNECTION_RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0, 16.0)


@dataclass(frozen=True)
class ConnectionStatus:
    phase: str
    error: str = ""
    retry: int = 0
    delay: float = 0.0
    max_retries: int = 5

    def display_text(self, language: str) -> tuple[str, str]:
        chinese = language == "zh"
        if self.phase == "failed":
            title = "连接失败" if chinese else "Connection failed"
            hint = (f"已重试 {self.retry} 次，点击 × 关闭。" if chinese else
                    f"Stopped after {self.retry} retries. Click × to close.")
        elif self.phase == "retrying":
            title = "连接失败，等待重试" if chinese else "Connection failed · retrying"
            hint = (f"{self.delay:g} 秒后重试（{self.retry}/{self.max_retries}），点击 × 取消。" if chinese else
                    f"Retry {self.retry}/{self.max_retries} in {self.delay:g}s. Click × to cancel.")
        elif self.retry == 0:
            title = "正在连接" if chinese else "Connecting"
            hint = "正在建立听写连接，点击 × 取消。" if chinese else "Connecting to dictation. Click × to cancel."
        else:
            title = (f"正在重连（{self.retry}/{self.max_retries}）" if chinese else
                     f"Reconnecting ({self.retry}/{self.max_retries})")
            hint = "点击 × 取消。" if chinese else "Click × to cancel."
        return title, f"{self.error}\n{hint}".strip()
