"""保守的本地内容识别；识别结果可在界面中手动调整。"""

import json
import re

from pygments.lexers import guess_lexer
from pygments.util import ClassNotFound


_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_URL = re.compile(r"^(?:https?|ftp)://[^\s]+$", re.IGNORECASE)
_COLOR = re.compile(r"^(?:#[0-9a-f]{3,4}|#[0-9a-f]{6}|#[0-9a-f]{8}|rgba?\(\s*[\d.,%\s]+\)|hsla?\(\s*[\d.,%\s]+\))$", re.IGNORECASE)


def detect(text: str) -> tuple[str, str]:
    """保持原始文本不变，返回 (内容类型, 语言名称)。"""
    full_sample = text.strip()
    # 识别只查看有限前缀，防止超大剪贴板内容拖慢数据库工作线程。
    sample = full_sample[:32768]
    if len(full_sample) <= 32768 and _URL.fullmatch(sample):
        return "url", ""
    if len(full_sample) <= 32768 and _EMAIL.fullmatch(sample):
        return "email", ""
    if len(full_sample) <= 32768 and _COLOR.fullmatch(sample):
        return "color", ""
    if sample.startswith(("{", "[")):
        try:
            value = json.loads(full_sample) if len(full_sample) <= 131072 else None
            if isinstance(value, (dict, list)):
                return "code", "JSON"
        except (ValueError, RecursionError):
            pass
    if re.search(r"^\s*SELECT\b", sample, re.MULTILINE | re.IGNORECASE) and re.search(r"\bFROM\b", sample, re.IGNORECASE):
        return "code", "SQL"
    if re.search(r"\bpublic\s+(?:static\s+)?(?:class|void)\b|\bSystem\.out\.(?:print|println)\s*\(|\bimport\s+java\.", sample):
        return "code", "Java"
    rules = (
        ("C++", r"(?:#\s*include\s*<|\bstd::|\bcout\s*<<|\bclass\s+\w+\s*(?:\{|:\s*(?:public|private|protected)\b))"),
        ("C", r"(?:#\s*include\s*[\"<]|\b(?:int|void)\s+main\s*\(|\bprintf\s*\(|\bscanf\s*\()"),
        ("Python", r"(?:^\s*(?:def|class)\s+\w+.*:\s*$|^\s*(?:from\s+[\w.]+\s+import|import\s+[\w.]+)|^\s*if\s+__name__\s*==|\bprint\s*\([^\n]*\))"),
        ("SQL", r"\b(?:INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|CREATE\s+(?:TABLE|DATABASE|VIEW)|ALTER\s+TABLE|DROP\s+TABLE)\b"),
        ("Java", r"(?:\bpublic\s+(?:static\s+)?(?:class|void)|\bSystem\.out\.(?:print|println)\s*\(|\bimport\s+java\.)"),
        ("JavaScript", r"(?:\b(?:const|let|var)\s+[\w$]+\s*=|\bfunction\s*[\w$]*\s*\(|\bconsole\.log\s*\(|=>)"),
        ("HTML", r"(?:<!doctype\s+html|</?(?:html|head|body|div|span|p|a|script|style)\b[^>]*>)"),
        ("CSS", r"(?:[.#]?[\w-]+\s*\{\s*[\w-]+\s*:\s*[^}]+;?\s*\})"),
        ("C", r"\b(?:int|char|float|double|void|size_t)\s+\w+\s*(?:\([^)]*\)\s*\{|=.+;)"),
    )
    for language, pattern in rules:
        if re.search(pattern, sample, re.MULTILINE | re.IGNORECASE):
            # 普通 C 的标准头文件不应归到 C++。
            if language == "C++" and not re.search(r"\bstd::|\bcout|\bclass\s|<(?:iostream|vector|string|map|algorithm)>", sample):
                language = "C"
            return "code", language

    # Pygments 仅用于已有代码特征的片段，避免把普通中文误认成代码。
    if len(sample) >= 12 and re.search(r"[{};]|(?:^\s*\w+\s*=)", sample, re.MULTILINE):
        try:
            lexer = guess_lexer(sample[:32768])
            aliases = set(lexer.aliases)
            mapping = (("cpp", "C++"), ("c", "C"), ("python", "Python"), ("sql", "SQL"), ("javascript", "JavaScript"), ("java", "Java"), ("json", "JSON"), ("html", "HTML"), ("css", "CSS"))
            for alias, language in mapping:
                if alias in aliases:
                    return "code", language
        except (ClassNotFound, ValueError):
            pass
    return "text", ""
