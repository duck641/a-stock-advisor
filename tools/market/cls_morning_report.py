"""获取财联社有声早报。"""

from __future__ import annotations

import re
import time
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

from langchain_core.tools import tool


CLS_SUBJECT_URL = "https://www.cls.cn/subject/1151"
FETCH_BUDGET_SECONDS = 10
REQUEST_TIMEOUT_SECONDS = 3


class _MorningLinkParser(HTMLParser):
    """从财联社专题页提取有声早报文章链接。"""

    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        if re.fullmatch(r"/detail/\d+", href):
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            title = "".join(self._text).strip()
            if "早报" in title:
                self.links.append((self._href, title))
            self._href = None
            self._text = []


class _ArticleBodyParser(HTMLParser):
    """提取早报正文，并保留段落边界。"""

    def __init__(self):
        super().__init__()
        self._active = False
        self._div_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        if tag == "div" and "detail-content" in classes and not self._active:
            self._active = True
            self._div_depth = 1
            return
        if self._active and tag == "div":
            self._div_depth += 1
        if self._active and tag in ("p", "h2", "h3", "li", "br"):
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._active and data.strip():
            self._parts.append(data.strip() + " ")

    def handle_endtag(self, tag: str) -> None:
        if not self._active:
            return
        if tag in ("p", "h2", "h3", "li"):
            self._parts.append("\n")
        if tag == "div":
            self._div_depth -= 1
            if self._div_depth == 0:
                self._active = False

    def text(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"[ \t]+", " ", text)
        return re.sub(r"\n\s*\n+", "\n", text).strip()


def _get_html(url: str, timeout: float, session=None) -> str:
    import requests

    client = session or requests
    response = client.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": CLS_SUBJECT_URL,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def _parse_target(target_date: str) -> date:
    try:
        return datetime.strptime(target_date, "%Y-%m-%d").date() if target_date else date.today()
    except ValueError as exc:
        raise ValueError("target_date 必须是 YYYY-MM-DD 格式") from exc


def _fetch_morning_report(target: date) -> dict:
    """读取指定日期或之前最近一期早报，并限制联网总耗时。"""
    import requests

    # Windows 子进程还需要导入工具包，因此联网部分只占用10秒预算。
    deadline = time.monotonic() + FETCH_BUDGET_SECONDS

    def request_timeout() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("财联社早报查询超过10秒时限")
        return min(REQUEST_TIMEOUT_SECONDS, remaining)

    errors = []
    with requests.Session() as session:
        parser = _MorningLinkParser()
        parser.feed(_get_html(CLS_SUBJECT_URL, timeout=request_timeout(), session=session))
        candidates = list(dict.fromkeys(parser.links))

        # 专题页按新到旧排列。限制候选数量，避免旧文章逐篇请求拖过总预算。
        for href, title in candidates[:6]:
            if time.monotonic() >= deadline:
                break
            url = urljoin(CLS_SUBJECT_URL, href)
            try:
                detail_html = _get_html(url, timeout=request_timeout(), session=session)
            except requests.RequestException as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                continue
            times = re.findall(r"20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}", detail_html)
            if not times:
                continue
            published_at = datetime.strptime(times[0], "%Y-%m-%d %H:%M")
            if published_at.date() > target:
                continue
            body_parser = _ArticleBodyParser()
            body_parser.feed(detail_html)
            content = body_parser.text()
            if content:
                return {
                    "status": "可用",
                    "source": "财联社有声早报",
                    "title": title,
                    "published_at": published_at.strftime("%Y-%m-%d %H:%M"),
                    "url": url,
                    "content": content,
                }

    reason = (
        "财联社早报查询达到10秒时限，未能确认目标日期内容"
        if time.monotonic() >= deadline
        else (
            f"财联社文章请求失败：{errors[-1]}" if errors
            else f"没有找到 {target.isoformat()} 或之前的有效早报"
        )
    )
    return {"status": "不可用", "source": "财联社有声早报", "reason": reason}


@tool
def get_cls_morning_report(target_date: str = "") -> dict:
    """获取财联社有声早报正文。

    用户分析当天可能异动、轮动或值得观察的板块时调用，以早报中的事件和判断
    作为线索。此工具只访问财联社，不读取项目本地日报或板块状态；正文会直接
    返回给模型，由模型结合上下文提炼相关线索。联网查询最多等待10秒。
    """
    target = _parse_target(target_date)
    try:
        return _fetch_morning_report(target)
    except Exception as exc:
        return {
            "status": "不可用",
            "source": "财联社有声早报",
            "reason": f"读取失败: {type(exc).__name__}: {exc}",
        }
