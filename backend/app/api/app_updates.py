from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

import httpx
from fastapi import APIRouter
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel

from app.version import app_version, update_check_enabled

router = APIRouter(prefix="/api/app", tags=["app"])

RELEASES_FEED_URL = "https://github.com/OpenBMB/StaffDeck/releases.atom"
RELEASES_PAGE_URL = "https://github.com/OpenBMB/StaffDeck/releases"
RELEASE_TAG_PATH = "/OpenBMB/StaffDeck/releases/tag/"
SUCCESS_CACHE_SECONDS = 6 * 60 * 60
FAILURE_CACHE_SECONDS = 15 * 60

_cache_lock = threading.Lock()


class AppVersionRead(BaseModel):
    """版本检查接口的响应体:当前版本、最新版本、是否可更新及检查状态。"""

    current_version: str
    latest_version: str | None = None
    update_available: bool = False
    release_url: str = RELEASES_PAGE_URL
    release_name: str | None = None
    published_at: str | None = None
    check_enabled: bool = True
    check_succeeded: bool = True


_cached_result: tuple[float, AppVersionRead] | None = None


@dataclass(frozen=True)
class ReleaseEntry:
    """从 releases atom feed 解析出的一条已发布版本记录。"""

    version: str
    parsed_version: Version
    name: str
    url: str
    published_at: str | None


def _parse_version(value: str) -> Version | None:
    """解析版本串:去掉前导 v/V 与 +build 后缀,无法解析时返回 None。"""
    normalized = value.strip()
    if normalized[:1].lower() == "v":
        normalized = normalized[1:]
    normalized = normalized.partition("+")[0]
    try:
        return Version(normalized)
    except InvalidVersion:
        return None


def _is_newer(latest: str, current: str) -> bool:
    """判断 latest 是否严格高于 current;任一侧无法解析时返回 False。"""
    latest_version = _parse_version(latest)
    current_version = _parse_version(current)
    return bool(
        latest_version is not None
        and current_version is not None
        and latest_version > current_version
    )


def _release_tag(entry: ElementTree.Element, namespace: dict[str, str]) -> tuple[str, str]:
    """从 feed 条目提取 (版本 tag, 发布链接)。

    优先取 alternate 链接里 /releases/tag/ 之后的段,并校验 https 与 github.com;
    取不到时回退到 atom:id 末段,链接一律用通用发布页。无法识别版本时返回空 tag。
    """
    link = entry.find("atom:link[@rel='alternate']", namespace)
    release_url = link.get("href", "").strip() if link is not None else ""
    if release_url:
        parsed_url = urlsplit(release_url)
        path = unquote(parsed_url.path)
        if (
            parsed_url.scheme == "https"
            and parsed_url.netloc == "github.com"
            and path.startswith(RELEASE_TAG_PATH)
        ):
            tag = path.removeprefix(RELEASE_TAG_PATH).strip("/")
            if _parse_version(tag) is not None:
                return tag, release_url

    entry_id = (entry.findtext("atom:id", default="", namespaces=namespace) or "").strip()
    tag = unquote(entry_id.rsplit("/", 1)[-1]) if "/" in entry_id else ""
    if _parse_version(tag) is not None:
        return tag, RELEASES_PAGE_URL
    return "", RELEASES_PAGE_URL


def _parse_release_feed(content: bytes, current: str) -> ReleaseEntry | None:
    """解析 releases atom feed,返回版本最高的候选条目;无可用候选时返回 None。

    当前版本为稳定版时会排除预发布 tag,避免提示降级到历史 beta 版本线。
    """
    root = ElementTree.fromstring(content)
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    current_version = _parse_version(current)
    if current_version is None:
        return None
    releases: list[ReleaseEntry] = []
    for entry in root.findall("atom:entry", namespace):
        tag, release_url = _release_tag(entry, namespace)
        parsed = _parse_version(tag)
        if parsed is None:
            continue
        # Stable update guarantees start at 0.2.0. Stable builds never consider
        # older prerelease tags, including the historical 0.12-beta release line.
        if not current_version.is_prerelease and parsed.is_prerelease:
            continue
        title = (entry.findtext("atom:title", default="", namespaces=namespace) or "").strip()
        releases.append(
            ReleaseEntry(
                version=tag.removeprefix("v").removeprefix("V"),
                parsed_version=parsed,
                name=title or tag,
                url=release_url,
                published_at=entry.findtext("atom:updated", default=None, namespaces=namespace),
            )
        )
    return max(releases, key=lambda item: item.parsed_version, default=None)


def _fetch_version() -> AppVersionRead:
    """拉取 releases feed 并组装版本信息;检查被关闭或请求失败时返回降级结果。

    不向外抛异常:网络、XML 解析与版本格式问题统一折算为 check_succeeded=False。
    """
    current = app_version()
    if not update_check_enabled():
        return AppVersionRead(
            current_version=current,
            check_enabled=False,
            check_succeeded=False,
        )
    try:
        response = httpx.get(
            RELEASES_FEED_URL,
            headers={"Accept": "application/atom+xml", "User-Agent": "StaffDeck"},
            timeout=4.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        release = _parse_release_feed(response.content, current)
        if release is None:
            return AppVersionRead(current_version=current, check_succeeded=False)
        return AppVersionRead(
            current_version=current,
            latest_version=release.version,
            update_available=_is_newer(release.version, current),
            release_url=release.url,
            release_name=release.name,
            published_at=release.published_at,
        )
    except (httpx.HTTPError, ElementTree.ParseError, ValueError, TypeError):
        return AppVersionRead(current_version=current, check_succeeded=False)


@router.get("/version", response_model=AppVersionRead)
def get_app_version() -> AppVersionRead:
    """返回当前版本与可用更新,用进程内 TTL 缓存限制对外请求频率。

    命中未过期缓存直接复用;成功结果缓存 6 小时,失败结果缓存 15 分钟。
    """
    global _cached_result
    now = time.monotonic()
    with _cache_lock:
        if _cached_result is not None and _cached_result[0] > now:
            return _cached_result[1]
        result = _fetch_version()
        ttl = SUCCESS_CACHE_SECONDS if result.check_succeeded else FAILURE_CACHE_SECONDS
        _cached_result = (now + ttl, result)
        return result
