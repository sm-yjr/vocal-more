"""Privacy-preserving foreground application context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ContextCategory = Literal["general", "messaging", "development", "writing"]
CONTEXT_CATEGORIES: tuple[ContextCategory, ...] = (
    "development",
    "general",
    "messaging",
    "writing",
)

_DEVELOPMENT_APP_IDS = {
    "com.apple.dt.xcode",
    "com.github.wez.wezterm",
    "com.googlecode.iterm2",
    "com.jetbrains.intellij",
    "com.jetbrains.pycharm",
    "com.microsoft.vscode",
    "com.mitchellh.ghostty",
    "com.sublimetext.4",
    "dev.warp.warp-stable",
    "code.exe",
    "codium.exe",
    "devenv.exe",
    "idea64.exe",
    "pycharm64.exe",
    "rider64.exe",
    "webstorm64.exe",
    "wezterm-gui.exe",
    "windowsterminal.exe",
    "wt.exe",
}
_DEVELOPMENT_PREFIXES = (
    "com.jetbrains.",
    "com.todesktop.230313mzl4w4u92",
)
_MESSAGING_APP_IDS = {
    "com.apple.mobilemail",
    "com.apple.MobileSMS",
    "com.hnc.discord",
    "com.microsoft.teams2",
    "com.tencent.xinwechat",
    "com.tinyspeck.slackmacgap",
    "com.tencent.qq",
    "com.electron.lark",
    "discord.exe",
    "feishu.exe",
    "lark.exe",
    "ms-teams.exe",
    "qq.exe",
    "slack.exe",
    "teams.exe",
    "wechat.exe",
}
_WRITING_APP_IDS = {
    "com.apple.iwork.pages",
    "com.apple.notes",
    "com.apple.textedit",
    "com.microsoft.word",
    "md.obsidian",
    "net.shinyfrog.bear",
    "notion.id",
    "notepad.exe",
    "notion.exe",
    "obsidian.exe",
    "onenote.exe",
    "winword.exe",
}

_CONTEXT_INSTRUCTIONS: dict[ContextCategory, str] = {
    "development": (
        "当前是开发场景。保护代码、命令、API 名、路径和英文标识符，"
        "不要翻译或改写；技术描述保持简洁、准确。"
    ),
    "messaging": (
        "当前是即时沟通场景。保留自然聊天语气和短句，避免公文腔，"
        "不要把普通消息扩写成正式文档。"
    ),
    "writing": (
        "当前是写作场景。优先形成连贯段落和清晰标点，保持原有语气，"
        "不要过度压缩有效信息。"
    ),
    "general": "",
}


@dataclass(frozen=True)
class AppContext:
    """Transient app identity plus the coarse category allowed to persist."""

    category: ContextCategory
    bundle_id: str


def classify_app_context(bundle_id: str) -> AppContext:
    """Map an app identifier to a coarse category without reading app content."""
    normalized = str(bundle_id or "").strip()
    lookup = normalized.lower()
    if lookup in {item.lower() for item in _DEVELOPMENT_APP_IDS} or any(
        lookup.startswith(prefix.lower()) for prefix in _DEVELOPMENT_PREFIXES
    ):
        category: ContextCategory = "development"
    elif lookup in {item.lower() for item in _MESSAGING_APP_IDS}:
        category = "messaging"
    elif lookup in {item.lower() for item in _WRITING_APP_IDS}:
        category = "writing"
    else:
        category = "general"
    return AppContext(category=category, bundle_id=normalized)


def instruction_for_context(context: AppContext | None) -> str:
    """Return an abstract prompt rule that contains no application identity."""
    if context is None:
        return ""
    return _CONTEXT_INSTRUCTIONS.get(context.category, "")


__all__ = [
    "AppContext",
    "CONTEXT_CATEGORIES",
    "ContextCategory",
    "classify_app_context",
    "instruction_for_context",
]
