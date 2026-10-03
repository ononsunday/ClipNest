"""纯函数文本工具。处理结果由调用方预览，不改变历史原文。"""

import json
import re


TOOLS = {
    "去除多余空格": "trim_spaces",
    "清理换行符": "clean_newlines",
    "转换为大写": "upper",
    "转换为小写": "lower",
    "去除重复行": "dedupe_lines",
    "JSON 格式化": "json",
    "复制为纯文本": "plain",
}


def transform(text: str, operation: str) -> str:
    """显式执行一项变换；JSON 失败时抛出便于界面提示的 ValueError。"""
    operation = TOOLS.get(operation, operation)
    if operation == "trim_spaces":
        compressed = re.sub(r"[ \t]+", " ", text)
        return re.sub(r"(?m)^ +| +(?=\r?$)", "", compressed).strip(" ")
    if operation == "clean_newlines":
        return re.sub(r"[\r\n]+", " ", text).strip()
    if operation == "upper":
        return text.upper()
    if operation == "lower":
        return text.lower()
    if operation == "dedupe_lines":
        newline = "\r\n" if "\r\n" in text else "\n"
        result = newline.join(dict.fromkeys(text.splitlines()))
        return result + newline if text.endswith(("\r", "\n")) else result
    if operation == "json":
        try:
            value = json.loads(text)
            return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        except (ValueError, RecursionError) as exc:
            raise ValueError("无法格式化 JSON，请检查 JSON 语法") from exc
    if operation == "plain":
        # 历史的 text 字段已经是纯文本；只需使用纯文本 MIME 重新复制。
        return text
    raise ValueError(f"未知文本工具：{operation}")
