import pytest

from clipnest.core.classify import detect
from clipnest.core.text_tools import TOOLS, transform


@pytest.mark.parametrize("text, expected", [
    ("你好，今天学习数据库。", ("text", "")),
    ("https://example.com/path?q=中文", ("url", "")),
    ("student@example.edu", ("email", "")),
    ("#aabbcc", ("color", "")),
    ("rgba(10, 20, 30, 0.5)", ("color", "")),
    ('{"名字": "小明", "age": 20}', ("code", "JSON")),
    ("#include <stdio.h>\nint main() { printf(\"你好\"); }", ("code", "C")),
    ("#include <iostream>\nstd::cout << 3;", ("code", "C++")),
    ("def answer():\n    return 42", ("code", "Python")),
    ("class Answer:\n    value = 42", ("code", "Python")),
    ("SELECT * FROM users WHERE id=1;", ("code", "SQL")),
    ("const answer = 42;", ("code", "JavaScript")),
    ("public class Main { public static void main(String[] args) {} }", ("code", "Java")),
    ('<div class="card">你好</div>', ("code", "HTML")),
    (".card { color: #fff; }", ("code", "CSS")),
])
def test_local_classification(text, expected):
    assert detect(text) == expected


@pytest.mark.parametrize("text, operation, expected", [
    ("  你好   世界 \n  a\t b  ", "trim_spaces", "你好 世界\na b"),
    ("  a  \r\n  b\t\r\n", "trim_spaces", "a\r\nb\r\n"),
    ("a\r\n\r\nb\nc", "clean_newlines", "a b c"),
    ("Hello 中文", "upper", "HELLO 中文"),
    ("Hello 中文", "lower", "hello 中文"),
    ("a\nb\na\n c\nb", "dedupe_lines", "a\nb\n c"),
    ("a\r\nb\r\na", "dedupe_lines", "a\r\nb"),
    ("a\nb\na\n", "dedupe_lines", "a\nb\n"),
    ('{"a":1,"名字":"小明"}', "json", '{\n  "a": 1,\n  "名字": "小明"\n}'),
    ("\t  原始\r\n文本  ", "plain", "\t  原始\r\n文本  "),
])
def test_text_transform_preview_keeps_original(text, operation, expected):
    before = text
    assert transform(text, operation) == expected
    assert text == before


def test_text_tools_accept_ui_labels():
    assert transform("a", "转换为大写") == "A"
    assert set(TOOLS.values()) == {"trim_spaces", "clean_newlines", "upper", "lower", "dedupe_lines", "json", "plain"}


@pytest.mark.parametrize("text", ['{"a":}', "not json", '{"a": NaN}'])
def test_json_error_is_actionable(text):
    with pytest.raises(ValueError, match="JSON"):
        transform(text, "json")


def test_unknown_transform_rejected():
    with pytest.raises(ValueError):
        transform("abc", "execute")
