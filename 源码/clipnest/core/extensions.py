"""可选的未来本地/AI 扩展接口，当前应用不发起任何网络请求。"""

from typing import Protocol


class ContentEnricher(Protocol):
    """扩展需由用户明确启用；实现负责声明自己的数据处理方式。"""

    def suggest_title(self, text: str) -> str: ...

    def suggest_tags(self, text: str) -> list[str]: ...


class SemanticSearch(Protocol):
    def search_ids(self, query: str, limit: int = 50) -> list[str]: ...
