"""Tests for the deterministic bilingual formatting pipeline."""

from __future__ import annotations

import pytest

from vocal_more.domain.bilingual_formatting import format_bilingual_text


class TestCjkLatinSpacing:
    def test_inserts_space_between_cjk_and_latin_letters(self):
        assert format_bilingual_text("使用Python脚本") == "使用 Python 脚本"

    def test_inserts_space_between_cjk_and_digits(self):
        assert format_bilingual_text("共3个文件") == "共 3 个文件"

    def test_inserts_space_in_both_directions(self):
        assert format_bilingual_text("Python脚本3个") == "Python 脚本 3 个"

    def test_does_not_duplicate_existing_spaces(self):
        assert format_bilingual_text("使用 Python 脚本") == "使用 Python 脚本"
        assert format_bilingual_text("共 3 个文件") == "共 3 个文件"

    def test_does_not_touch_interior_of_ascii_runs(self):
        assert format_bilingual_text("我使用Python3写代码") == (
            "我使用 Python3 写代码"
        )

    def test_punctuation_adjacent_to_cjk_gets_no_space(self):
        assert format_bilingual_text("运行Python。") == "运行 Python。"


class TestPureTextUnchanged:
    def test_pure_chinese_unchanged(self):
        text = "今天天气很好，我们出去走走吧。"
        assert format_bilingual_text(text) == text

    def test_pure_english_unchanged(self):
        text = "The quick brown fox jumps over 13 lazy dogs."
        assert format_bilingual_text(text) == text

    def test_empty_string(self):
        assert format_bilingual_text("") == ""


class TestFullwidthConversion:
    def test_converts_fullwidth_letters_and_digits(self):
        assert format_bilingual_text("ＡＢＣ１２３") == "ABC123"

    def test_converts_lowercase_fullwidth(self):
        assert format_bilingual_text("ａｂｃ") == "abc"

    def test_conversion_enables_spacing_insertion(self):
        assert format_bilingual_text("中文ＡＢＣ结尾") == "中文 ABC 结尾"

    def test_does_not_convert_fullwidth_punctuation(self):
        # Full/half-width punctuation conversion is explicitly out of scope.
        text = "好的！真的吗？谢谢，再见。"
        assert format_bilingual_text(text) == text


class TestRepeatedPunctuation:
    def test_compresses_repeated_chinese_punctuation(self):
        assert format_bilingual_text("好的。。谢谢！！！真的吗？？") == (
            "好的。谢谢！真的吗？"
        )

    def test_compresses_repeated_commas_and_enumeration(self):
        assert format_bilingual_text("苹果，，香蕉、、橘子") == "苹果，香蕉、橘子"

    def test_keeps_single_punctuation(self):
        text = "好的。谢谢！真的吗？苹果，香蕉、橘子"
        assert format_bilingual_text(text) == text


class TestProtectedSegments:
    def test_url_with_scheme_untouched(self):
        text = "访问 https://example.com/path?a=1&b=2 即可"
        assert format_bilingual_text(text) == text

    def test_url_glued_to_cjk_untouched(self):
        # The whole whitespace-free run is protected, nothing changes.
        text = "访问https://a.io看看"
        assert format_bilingual_text(text) == text

    def test_www_url_untouched(self):
        text = "打开 www.example.com 看看"
        assert format_bilingual_text(text) == text

    def test_unix_path_untouched(self):
        text = "配置在 /usr/local/bin 里面"
        assert format_bilingual_text(text) == text

    def test_path_glued_to_cjk_untouched(self):
        text = "打开/usr/local/bin目录"
        assert format_bilingual_text(text) == text

    def test_windows_path_untouched(self):
        text = r"文件在 C:\Users\test\docs 里"
        assert format_bilingual_text(text) == text

    def test_inline_code_untouched(self):
        text = "使用`fmt.Println()`输出"
        assert format_bilingual_text(text) == text

    def test_inline_code_with_cjk_inside_untouched(self):
        text = "运行`中文命令`即可"
        assert format_bilingual_text(text) == text

    def test_text_around_protected_segment_still_formatted(self):
        assert format_bilingual_text("使用Python和`git status`命令") == (
            "使用 Python 和`git status`命令"
        )


class TestIdempotency:
    @pytest.mark.parametrize(
        "text",
        [
            "使用Python脚本共3个",
            "访问 https://example.com/path 和 /usr/local/bin",
            "好的。。谢谢！！！",
            "ＡＢＣ１２３中文",
            "使用`fmt.Println()`输出",
        ],
    )
    def test_second_pass_is_a_noop(self, text):
        once = format_bilingual_text(text)
        assert format_bilingual_text(once) == once
