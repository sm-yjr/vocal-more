"""Windows executable classification tests."""

from vocal_more.domain.app_context import classify_app_context


def test_windows_executables_map_to_coarse_categories():
    assert classify_app_context("Code.exe").category == "development"
    assert classify_app_context("WeChat.exe").category == "messaging"
    assert classify_app_context("WINWORD.EXE").category == "writing"
    assert classify_app_context("explorer.exe").category == "general"
