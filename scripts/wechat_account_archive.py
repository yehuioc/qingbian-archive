from __future__ import annotations

import argparse
import asyncio
import csv
import html
import json
import os
import re
import shutil
import sys
import urllib.parse
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from camoufox.async_api import AsyncCamoufox
from miku_ai import get_wexin_article
from miku_ai.spider import MikuSpider


LIB_ROOT = Path(__file__).resolve().parent.parent / "lib"
if str(LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(LIB_ROOT))

from wechat_to_md.converter import build_markdown, convert_html_to_markdown, replace_image_urls  # noqa: E402
from wechat_to_md.downloader import download_all_images  # noqa: E402
from wechat_to_md.parser import extract_metadata, process_content  # noqa: E402
from wechat_to_md.scraper import fetch_page_html  # noqa: E402
from wechat_to_md.utils import format_timestamp, sanitize_filename  # noqa: E402


NEXT_RE = re.compile(r"next_article_link:\s*JsDecode\('([^']*)'\)")
PRE_RE = re.compile(r"pre_article_link:\s*JsDecode\('([^']*)'\)")
BIZ_RE = re.compile(r'__biz=([A-Za-z0-9+/=]+)')
MID_RE = re.compile(r'(?:(?:\?|&)|(?:\\x26amp;))mid=(\d+)')
IDX_RE = re.compile(r'(?:(?:\?|&)|(?:\\x26amp;))idx=(\d+)')
SN_RE = re.compile(r'(?:(?:\?|&)|(?:\\x26amp;))sn=([0-9a-fA-F]+)')
CHKSM_RE = re.compile(r'(?:(?:\?|&)|(?:\\x26amp;))chksm=([0-9a-zA-Z]+)')
AUTHOR_ALT_RE = re.compile(r"nick_name:\s*JsDecode\('([^']*)'\)")
ACCOUNT_ID_RE = re.compile(r"user_name:\s*JsDecode\('([^']*)'\)")
WINDOW_BIZ_RE = re.compile(r'var\s+biz\s*=\s*"([^"]+)"')
WINDOW_MID_RE = re.compile(r'var\s+mid\s*=\s*"([^"]+)"')
WINDOW_IDX_RE = re.compile(r'var\s+idx\s*=\s*"([^"]+)"')
WINDOW_SN_RE = re.compile(r'var\s+sn\s*=\s*"([^"]+)"')
WINDOW_CHKSM_RE = re.compile(r'var\s+chksm\s*=\s*"([^"]+)"')
HTML_BIZ_RE = re.compile(r"bizuin:\s*JsDecode\('([^']+)'\)")
HTML_MID_RE = re.compile(r"mid:\s*'(\d+)'\s*\*\s*1")
HTML_IDX_RE = re.compile(r"idx:\s*'(\d+)'\s*\*\s*1")
HTML_SN_RE = re.compile(r"sn:\s*JsDecode\('([^']+)'\)")
HTML_CHKSM_RE = re.compile(r"chksm:\s*JsDecode\('([^']+)'\)")
WINDOW_TITLE_RE = re.compile(r"""window\.msg_title\s*=\s*window\.title\s*=\s*['"]([^'"]+)['"]""")
WINDOW_CT_RE = re.compile(r"""window\.ct\s*=\s*['"]?(\d{10})""")
JSDECODE_TITLE_RE = re.compile(r"""title:\s*JsDecode\('([^']+)'\)""")
ALBUM_RE = re.compile(
    r"title:\s*JsDecode\('([^']*)'\).*?"
    r"link:\s*JsDecode\('https://mp\.weixin\.qq\.com/mp/appmsgalbum\?__biz=([^']*?)\\x26amp;action=getalbum\\x26amp;album_id=([^']*?)#wechat_redirect'\).*?"
    r"content_size:\s*'([^']*)'",
    re.S,
)
ARTICLE_LINK_RE = re.compile(r"https://mp\.weixin\.qq\.com/s(?:/[A-Za-z0-9_-]+|\?[^\"'<>\s]+)")
SOGOU_RESULT_SELECTOR = 'li[id^="sogou_vr_11002601_box_"]'


@dataclass
class ArticleRecord:
    canonical_url: str
    title: str
    author: str
    account_name: str
    account_id: str
    date: str
    biz: str
    mid: str
    idx: str
    sn: str
    chksm: str
    next_url: str
    pre_url: str
    article_dir: str
    markdown_path: str
    image_count: int
    direction: str
    series_title: str = ""
    series_id: str = ""
    series_item_index: str = ""
    series_item_order: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive a WeChat public account into ingestion.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--runtime-home", required=True)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--author-hint", default="")
    parser.add_argument("--seed-url", action="append", default=[])
    parser.add_argument("--bootstrap-archive-root", action="append", default=[])
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--search-pages", type=int, default=3)
    parser.add_argument("--search-limit", type=int, default=20)
    parser.add_argument("--max-articles", type=int, default=0)
    parser.add_argument("--no-search", action="store_true",
                        help="Disable search-based discovery; rely on seed URLs, album queues, and bootstrap archives.")
    parser.add_argument("--download-images", action="store_true")
    parser.add_argument("--allow-headful-fallback", action="store_true")
    parser.add_argument("--no-headless", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def clean_js_url(url: str) -> str:
    if not url:
        return ""
    cleaned = (
        url.replace("\\x26amp;", "&")
        .replace("\\x26", "&")
        .replace("&#x26;", "&")
        .replace("&#38;", "&")
        .replace("&amp;", "&")
    )
    cleaned = cleaned.replace("http://", "https://")
    return cleaned


def decode_js_text(value: str) -> str:
    if not value:
        return ""
    decoded = value.replace("\\x26amp;", "&").replace("\\x26", "&")
    return html.unescape(decoded)


def canonical_from_parts(biz: str, mid: str, idx: str, sn: str = "", chksm: str = "") -> str:
    if not (biz and mid and idx):
        return ""
    sn_part = f"&sn={sn}" if sn else ""
    extra = f"&chksm={chksm}" if chksm else ""
    return f"https://mp.weixin.qq.com/s?__biz={biz}&mid={mid}&idx={idx}{sn_part}{extra}#wechat_redirect"


def normalize_article_url(url: str) -> str:
    cleaned = clean_js_url(url)
    biz = BIZ_RE.search(cleaned)
    mid = MID_RE.search(cleaned)
    idx = IDX_RE.search(cleaned)
    sn = SN_RE.search(cleaned)
    chksm = CHKSM_RE.search(cleaned)
    if biz and mid and idx:
        return canonical_from_parts(
            biz.group(1),
            mid.group(1),
            idx.group(1),
            sn.group(1) if sn else "",
            chksm.group(1) if chksm else "",
        )
    return cleaned


def article_identity(url: str) -> str:
    normalized = normalize_article_url(url)
    biz = BIZ_RE.search(normalized)
    mid = MID_RE.search(normalized)
    idx = IDX_RE.search(normalized)
    if biz and mid and idx:
        return canonical_from_parts(
            biz.group(1),
            mid.group(1),
            idx.group(1),
            "",
            "",
        )
    return normalized


def extract_biz_from_url(url: str) -> str:
    normalized = normalize_article_url(url)
    biz = BIZ_RE.search(normalized)
    return biz.group(1) if biz else ""


def canonical_from_html(html_text: str, fallback_url: str) -> str:
    biz = WINDOW_BIZ_RE.search(html_text) or HTML_BIZ_RE.search(html_text)
    mid = WINDOW_MID_RE.search(html_text) or HTML_MID_RE.search(html_text)
    idx = WINDOW_IDX_RE.search(html_text) or HTML_IDX_RE.search(html_text)
    sn = WINDOW_SN_RE.search(html_text) or HTML_SN_RE.search(html_text)
    chksm = WINDOW_CHKSM_RE.search(html_text) or HTML_CHKSM_RE.search(html_text)
    if biz and mid and idx:
        return canonical_from_parts(
            clean_js_url(biz.group(1)),
            mid.group(1),
            idx.group(1),
            clean_js_url(sn.group(1)) if sn else "",
            clean_js_url(chksm.group(1)) if chksm else "",
        )
    return normalize_article_url(fallback_url)


def extract_account_name(html_text: str, soup: BeautifulSoup, meta_author: str) -> str:
    if meta_author:
        return meta_author.strip()
    alt = AUTHOR_ALT_RE.search(html_text)
    if alt:
        return decode_js_text(alt.group(1))
    author_el = soup.select_one("#js_name, .nickNameSpan, #js_wx_follow_nickname")
    return author_el.get_text(strip=True) if author_el else ""


def extract_account_id(html_text: str) -> str:
    match = ACCOUNT_ID_RE.search(html_text)
    return clean_js_url(match.group(1)) if match else ""


def extract_title_fallback(soup: BeautifulSoup, html_text: str = "") -> str:
    for selector, attr in (
        ('meta[property="og:title"]', "content"),
        ('meta[property="twitter:title"]', "content"),
        ('meta[name="twitter:title"]', "content"),
    ):
        node = soup.select_one(selector)
        if node and node.get(attr):
            return str(node.get(attr)).strip()

    title_el = soup.find("title")
    title_text = title_el.get_text(strip=True) if title_el else ""
    if title_text and title_text != "Weixin Official Accounts Platform":
        return title_text

    for pattern in (WINDOW_TITLE_RE, JSDECODE_TITLE_RE):
        match = pattern.search(html_text)
        if match:
            return decode_js_text(match.group(1)).strip()

    return title_text


def extract_publish_time_fallback(html_text: str) -> str:
    match = WINDOW_CT_RE.search(html_text)
    if not match:
        return ""
    return format_timestamp(match.group(1))


def extract_album_hints(html_text: str) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    rows: list[dict[str, str]] = []
    for match in ALBUM_RE.finditer(html_text):
        title = decode_js_text(match.group(1))
        biz = clean_js_url(match.group(2))
        album_id = clean_js_url(match.group(3))
        content_size = decode_js_text(match.group(4))
        key = (biz, album_id)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "title": title,
                "biz": biz,
                "album_id": album_id,
                "content_size": content_size,
            }
        )
    return rows


def dedupe_urls(urls: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        normalized = normalize_article_url(raw)
        identity = article_identity(normalized)
        if not normalized or identity in seen:
            continue
        seen.add(identity)
        ordered.append(normalized)
    return ordered


def extract_validation_authority(candidate: dict[str, Any], details: dict[str, Any]) -> dict[str, Any]:
    actual_account = str(details.get("actual_account") or "").strip()
    account_id = str(details.get("account_id") or "").strip()
    bizs: set[str] = set()

    normalized = str(candidate.get("normalized_url") or candidate.get("url") or "").strip()
    normalized_biz = extract_biz_from_url(normalized)
    if normalized_biz:
        bizs.add(normalized_biz)

    for row in details.get("album_hints") or []:
        biz = str(row.get("biz") or "").strip()
        if biz:
            bizs.add(biz)

    return {
        "actual_account": actual_account,
        "account_id": account_id,
        "bizs": bizs,
    }


def merge_locked_authority(
    locked_account_ids: set[str],
    locked_bizs: set[str],
    locked_account_names: set[str],
    authority: dict[str, Any],
) -> None:
    account_id = str(authority.get("account_id") or "").strip()
    actual_account = str(authority.get("actual_account") or "").strip()
    bizs = authority.get("bizs") or set()

    if account_id:
        locked_account_ids.add(account_id)
    if actual_account:
        locked_account_names.add(actual_account)
    for biz in bizs:
        if biz:
            locked_bizs.add(str(biz).strip())


def authority_allows_candidate(
    candidate: dict[str, Any],
    details: dict[str, Any],
    locked_account_ids: set[str],
    locked_bizs: set[str],
    locked_account_names: set[str],
) -> bool:
    if not (locked_account_ids or locked_bizs or locked_account_names):
        return True

    authority = extract_validation_authority(candidate, details)
    account_id = str(authority.get("account_id") or "").strip()
    actual_account = str(authority.get("actual_account") or "").strip()
    bizs = {str(biz).strip() for biz in authority.get("bizs") or set() if str(biz).strip()}

    if locked_account_ids and account_id and account_id in locked_account_ids:
        return True
    if locked_bizs and bizs.intersection(locked_bizs):
        return True
    if locked_account_names and actual_account and actual_account in locked_account_names and not (locked_account_ids or locked_bizs):
        return True
    return False


def record_matches_authority(
    record: ArticleRecord,
    allowed_account_ids: set[str] | None = None,
    allowed_bizs: set[str] | None = None,
    allowed_account_names: set[str] | None = None,
) -> bool:
    account_id = str(record.account_id or "").strip()
    biz = str(record.biz or "").strip()
    account_name = str(record.account_name or "").strip()
    allowed_account_ids = allowed_account_ids or set()
    allowed_bizs = allowed_bizs or set()
    allowed_account_names = allowed_account_names or set()

    if not (allowed_account_ids or allowed_bizs or allowed_account_names):
        return True
    if allowed_account_ids and account_id and account_id in allowed_account_ids:
        return True
    if allowed_bizs and biz and biz in allowed_bizs:
        return True
    if allowed_account_names and account_name and account_name in allowed_account_names and not (allowed_account_ids or allowed_bizs):
        return True
    return False


def filter_records_by_authority(
    records_by_url: dict[str, ArticleRecord],
    allowed_account_ids: set[str] | None = None,
    allowed_bizs: set[str] | None = None,
    allowed_account_names: set[str] | None = None,
) -> tuple[dict[str, ArticleRecord], list[ArticleRecord]]:
    kept: dict[str, ArticleRecord] = {}
    removed: list[ArticleRecord] = []
    for identity, record in records_by_url.items():
        if record_matches_authority(
            record,
            allowed_account_ids=allowed_account_ids,
            allowed_bizs=allowed_bizs,
            allowed_account_names=allowed_account_names,
        ):
            kept[identity] = record
        else:
            removed.append(record)
    return kept, removed


def filter_album_rows_by_biz(rows: list[dict[str, Any]], allowed_bizs: set[str] | None = None) -> list[dict[str, Any]]:
    if not allowed_bizs:
        return list(rows)
    return [row for row in rows if str(row.get("biz") or "").strip() in allowed_bizs]


def url_allowed_by_biz(url: str, allowed_bizs: set[str] | None = None) -> bool:
    if not allowed_bizs:
        return True
    biz = extract_biz_from_url(url)
    if not biz:
        return True
    return biz in allowed_bizs


def extract_same_account_links(html_text: str, biz_hint: str = "") -> list[str]:
    rows: list[str] = []
    for match in ARTICLE_LINK_RE.finditer(html_text):
        raw_link = match.group(0)
        if "${" in raw_link or "{window." in raw_link:
            continue
        normalized = normalize_article_url(raw_link)
        if not normalized:
            continue
        biz_match = BIZ_RE.search(normalized)
        if biz_hint and biz_match and biz_match.group(1) != biz_hint:
            continue
        rows.append(normalized)
    return dedupe_urls(rows)


def html_is_placeholder(html_text: str, soup: BeautifulSoup | None = None) -> bool:
    soup = soup or BeautifulSoup(html_text, "html.parser")
    title = extract_title_fallback(soup, html_text)
    has_content_container = 'id="js_content"' in html_text
    if title == "Weixin Official Accounts Platform":
        return True
    if (
        ("${window.biz}" in html_text or "${window.mid}" in html_text or "${window.idx}" in html_text or "${window.sn}" in html_text)
        and not has_content_container
        and not title
    ):
        return True
    if not has_content_container and not title:
        return True
    return False


def discovery_from_html(html_text: str, current_url: str, record: ArticleRecord | None = None) -> dict[str, Any]:
    canonical = canonical_from_html(html_text, current_url)
    biz_match = BIZ_RE.search(canonical)
    biz = biz_match.group(1) if biz_match else (record.biz if record else "")
    related = dedupe_urls(
        [
            record.pre_url if record else "",
            record.next_url if record else "",
            *extract_same_account_links(html_text, biz),
        ]
    )
    related = [url for url in related if url != canonical]
    return {
        "canonical_url": canonical,
        "biz": biz,
        "album_hints": extract_album_hints(html_text),
        "related_urls": related,
    }


def normalize_series_title(account_name: str, raw_title: str) -> str:
    title = (raw_title or "").strip()
    if not title:
        return ""
    prefixes = [
        f"{account_name}:",
        f"{account_name}：",
        f"{account_name} ",
    ]
    for prefix in prefixes:
        if prefix and title.startswith(prefix):
            title = title[len(prefix) :].strip()
            break
    return title


def extract_album_title_from_soup(soup: BeautifulSoup) -> str:
    candidates: list[str] = []
    for selector in (".album__label-title", ".video-album__label-title"):
        node = soup.select_one(selector)
        if node:
            text = node.get_text(strip=True)
            if text:
                candidates.append(text)
    for selector, attr in (
        ('meta[property="og:title"]', "content"),
        ('meta[name="twitter:title"]', "content"),
    ):
        node = soup.select_one(selector)
        if node and node.get(attr):
            candidates.append(str(node.get(attr)).strip())
    title_node = soup.find("title")
    if title_node:
        candidates.append(title_node.get_text(strip=True))

    for candidate in candidates:
        normalized = candidate.strip()
        if not normalized:
            continue
        if normalized == "Weixin Official Accounts Platform":
            continue
        return normalized
    return ""


def article_storage_paths(
    output_root: Path,
    title: str,
    publish_time: str,
    mid: str,
    account_name: str = "",
    author: str = "",
    series_title: str = "",
    series_id: str = "",
    series_item_order: int = 0,
) -> tuple[Path, Path]:
    safe_title = sanitize_filename(title) or "untitled"
    safe_mid = mid or "unknown-mid"
    safe_date = publish_time[:10].replace("-", "") if publish_time else "unknown-date"
    safe_account = sanitize_filename(account_name or "unknown-account")
    safe_author = sanitize_filename(author or "unknown-author")
    safe_series_title = sanitize_filename(series_title or "未分系列")
    safe_series = f"{safe_series_title}__{series_id}" if series_id else safe_series_title
    order_prefix = f"{int(series_item_order):04d}-" if series_item_order else "0000-"
    article_dir = output_root / "articles" / safe_account / safe_author / safe_series / f"{order_prefix}{safe_date}-{safe_mid}-{safe_title}"
    markdown_path = article_dir / f"{safe_title}.md"
    return article_dir, markdown_path


def max_album_size(album_hints: list[dict[str, str]]) -> int:
    sizes: list[int] = []
    for row in album_hints:
        try:
            sizes.append(int(row.get("content_size") or 0))
        except Exception:
            continue
    return max(sizes) if sizes else 0


def score_candidate(candidate: dict[str, str], account_name: str, author_hint: str) -> int:
    score = 0
    title = (candidate.get("title") or "").strip()
    source = (candidate.get("source") or "").strip()
    if title.startswith(f"{account_name}:") or title.startswith(f"{account_name}："):
        score += 100
    if account_name in title:
        score += 30
    if source == account_name or source.startswith(account_name):
        score += 80
    if author_hint and author_hint in title:
        score += 20
    return score


def candidate_priority(candidate: dict[str, str], account_name: str, author_hint: str) -> tuple[int, int, int, int]:
    title = (candidate.get("title") or "").strip()
    source = (candidate.get("source") or "").strip()
    return (
        1 if (source == account_name or source.startswith(account_name)) else 0,
        1 if title.startswith(f"{account_name}:") or title.startswith(f"{account_name}：") else 0,
        1 if account_name in title else 0,
        score_candidate(candidate, account_name, author_hint),
    )


def strip_account_prefix(title: str, account_name: str) -> str:
    normalized = decode_js_text(title).strip()
    if not normalized or not account_name:
        return normalized
    pattern = rf"^\s*{re.escape(account_name)}\s*[:：—\-|｜]\s*"
    return re.sub(pattern, "", normalized, count=1).strip()


def title_fragments(title: str) -> list[str]:
    working = decode_js_text(title).strip()
    if not working:
        return []
    working = re.sub(r"^【[^】]+】", "", working).strip()
    pieces = [working]
    splitters = ["，", "。", "？", "！", "：", ":", "、", "|", "｜", "——", "-", "与", "和"]
    for splitter in splitters:
        next_parts: list[str] = []
        for piece in pieces:
            next_parts.extend(part.strip() for part in piece.split(splitter) if part.strip())
        pieces.extend(next_parts)

    deduped: list[str] = []
    seen: set[str] = set()
    for piece in pieces:
        normalized = piece.strip("【】“”\"' \t")
        if len(normalized) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def extract_content_query_hints(html_text: str) -> list[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    content = soup.select_one("#js_content")
    if not content:
        return []

    text = content.get_text("\n", strip=True)
    text = re.sub(r"\s+", "", text)
    lines = [line.strip() for line in re.split(r"[\n。！？；]", text) if line.strip()]

    candidates: list[str] = []
    for line in lines[:10]:
        working = re.sub(r"^【[^】]+】", "", line).strip()
        if not working:
            continue
        if 4 <= len(working) <= 16:
            candidates.append(working)
        for splitter in ("：", ":", "——", "，", "、", "；"):
            parts = [part.strip() for part in working.split(splitter) if part.strip()]
            for part in parts:
                if 4 <= len(part) <= 16:
                    candidates.append(part)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.strip("【】“”\"' \t")
        if len(normalized) < 4 or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
        if len(deduped) >= 8:
            break
    return deduped


def query_variants_from_title(title: str, account_name: str) -> list[str]:
    raw = decode_js_text(title).strip()
    if not raw:
        return []

    stripped = strip_account_prefix(raw, account_name)
    queries = [raw]
    if stripped and stripped != raw:
        queries.extend([f"{account_name} {stripped}", stripped])
    elif account_name and account_name not in raw:
        queries.append(f"{account_name} {raw}")

    base_for_fragments = stripped or raw
    for fragment in title_fragments(base_for_fragments)[:4]:
        if account_name and account_name not in fragment:
            queries.append(f"{account_name} {fragment}")

    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        normalized = query.strip()
        if not normalized or len(normalized) > 80 or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def candidate_queries(account_name: str, author_hint: str, title_hints: list[str] | None = None) -> list[str]:
    queries: list[str] = []
    for title_hint in title_hints or []:
        queries.extend(query_variants_from_title(title_hint, account_name))
    queries.extend(
        [
            account_name,
            f"{account_name}:",
            f"{account_name}：",
            f"“{account_name}”",
        ]
    )
    if author_hint and author_hint != account_name:
        queries.extend(
            [
                f"{account_name} {author_hint}",
                f"{author_hint} {account_name}",
            ]
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        normalized = query.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


async def collect_search_candidates(
    account_name: str,
    author_hint: str,
    search_pages: int,
    search_limit: int,
    title_hints: list[str] | None = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    queries = candidate_queries(account_name, author_hint, title_hints=title_hints)
    total_limit = max(search_limit, 10) * max(len(queries), 1)

    spider = MikuSpider()

    for query in queries:
        try:
            results = await get_wexin_article(query, search_limit)
        except Exception:
            results = []

        for item in results:
            raw_url = (item.get("url") or "").strip()
            if not raw_url:
                continue
            normalized = normalize_article_url(raw_url)
            identity = article_identity(normalized)
            if identity in seen:
                continue
            seen.add(identity)
            rows.append(
                {
                    "title": (item.get("title") or "").strip(),
                    "source": (item.get("source") or item.get("name") or item.get("account") or item.get("author") or "").strip(),
                    "date": (item.get("date") or "").strip(),
                    "url": raw_url,
                    "normalized_url": normalized,
                    "query": query,
                }
            )
            if len(rows) >= total_limit:
                return rows

        for page in range(1, search_pages + 1):
            try:
                html_text = await spider.weixin_spider(query, page=page)
            except Exception:
                continue
            soup = BeautifulSoup(html_text, "html.parser")
            items = soup.find_all("li", {"id": lambda x: x and x.startswith("sogou_vr_11002601_box_")})
            parsed = await asyncio.gather(*[spider.parse_item(item) for item in items])
            for item in parsed:
                raw_url = (item.get("url") or "").strip()
                if not raw_url:
                    continue
                normalized = normalize_article_url(raw_url)
                identity = article_identity(normalized)
                if identity in seen:
                    continue
                seen.add(identity)
                rows.append(
                    {
                        "title": (item.get("title") or "").strip(),
                        "source": (item.get("source") or "").strip(),
                        "date": (item.get("date") or "").strip(),
                        "url": raw_url,
                        "normalized_url": normalized,
                        "query": query,
                    }
                )
                if len(rows) >= total_limit:
                    return rows
    return rows


def validate_candidate_html(
    candidate: dict[str, Any],
    html_text: str,
    trial_url: str,
    account_name: str,
    author_hint: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    soup = BeautifulSoup(html_text, "html.parser")
    placeholder = html_is_placeholder(html_text, soup)
    album_hints = extract_album_hints(html_text)
    meta = extract_metadata(soup, html_text, url=trial_url)
    actual_account = extract_account_name(html_text, soup, meta.author)
    source_name = (candidate.get("source") or "").strip()

    matches_account = actual_account == account_name or (author_hint and actual_account == author_hint)
    if (
        not matches_account
        and not actual_account
        and album_hints
        and (candidate.get("query") == "explicit-seed-url" or candidate.get("query") == "browser-search-explicit-seed")
    ):
        matches_account = True

    if not matches_account:
        return None

    details = {
        "html": html_text,
        "meta": meta,
        "actual_account": actual_account,
        "account_id": extract_account_id(html_text),
        "album_hints": album_hints,
        "placeholder": placeholder,
        "source_name": source_name,
        "query_hints": extract_content_query_hints(html_text),
    }
    candidate["normalized_url"] = canonical_from_html(html_text, trial_url)
    return candidate, details


async def validate_candidate(
    candidate: dict[str, Any],
    account_name: str,
    author_hint: str,
    headless: bool,
    allow_headful_fallback: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    trial_url = candidate["url"]
    source_name = (candidate.get("source") or "").strip()
    attempts = 4 if source_name == account_name else 2
    for attempt in range(attempts):
        try:
            html_text = await fetch_page_html(trial_url, headless=headless)
        except Exception:
            if attempt + 1 < attempts:
                await asyncio.sleep(2)
                continue
            return None
        soup = BeautifulSoup(html_text, "html.parser")
        placeholder = html_is_placeholder(html_text, soup)
        album_hints = extract_album_hints(html_text)
        if placeholder and not album_hints:
            if headless and allow_headful_fallback:
                try:
                    html_text = await fetch_page_html(trial_url, headless=False)
                    soup = BeautifulSoup(html_text, "html.parser")
                    placeholder = html_is_placeholder(html_text, soup)
                    album_hints = extract_album_hints(html_text)
                except Exception:
                    pass
            if placeholder and not album_hints:
                if attempt + 1 < attempts:
                    await asyncio.sleep(2)
                    continue
                return None
        validated = validate_candidate_html(candidate, html_text, trial_url, account_name, author_hint)
        if not validated:
            if attempt + 1 < attempts:
                await asyncio.sleep(2)
                continue
            return None
        return validated
    return None


def sogou_search_url(query: str, page: int = 1) -> str:
    encoded = urllib.parse.quote(query)
    return f"https://weixin.sogou.com/weixin?type=2&query={encoded}&page={page}"


async def extract_search_row_source(box: Any) -> str:
    for selector in (".account", ".s-p", ".txt-info", ".txt-box p"):
        locator = box.locator(selector)
        if await locator.count():
            try:
                text = (await locator.first.inner_text()).strip()
            except Exception:
                text = ""
            if text:
                return text
    return ""


async def extract_sogou_result_rows(page: Any, row_limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    count = await page.locator(SOGOU_RESULT_SELECTOR).count()
    for index in range(min(count, row_limit)):
        box = page.locator(SOGOU_RESULT_SELECTOR).nth(index)
        title_locator = box.locator("h3")
        title = ""
        if await title_locator.count():
            try:
                title = (await title_locator.first.inner_text()).strip()
            except Exception:
                title = ""
        rows.append(
            {
                "index": index,
                "title": title,
                "source": await extract_search_row_source(box),
                "query": "",
            }
        )
    return rows


async def collect_broad_search_title_hints(
    browser: AsyncCamoufox,
    account_name: str,
    search_pages: int,
    row_limit: int,
) -> list[str]:
    queries = [account_name, f"{account_name}:", f"{account_name}："]
    hints: list[str] = []
    seen: set[str] = set()
    page = await browser.new_page()
    try:
        for query in queries:
            for page_index in range(1, max(search_pages, 1) + 1):
                await page.goto(sogou_search_url(query, page=page_index), wait_until="domcontentloaded", timeout=120000)
                await page.wait_for_timeout(2500)
                rows = await extract_sogou_result_rows(page, row_limit)
                if not rows:
                    break
                for row in rows:
                    row_title = (row.get("title") or "").strip()
                    row_source = (row.get("source") or "").strip()
                    if not row_title:
                        continue
                    if strip_account_prefix(row_title, account_name) != row_title or row_source.startswith(account_name):
                        for derived_query in query_variants_from_title(row_title, account_name):
                            if derived_query in seen:
                                continue
                            seen.add(derived_query)
                            hints.append(derived_query)
                        if len(hints) >= 24:
                            return hints
        return hints
    finally:
        await page.close()


def search_row_is_plausible(
    row: dict[str, Any],
    query: str,
    account_name: str,
    author_hint: str,
    known_markers: set[str],
) -> bool:
    title = (row.get("title") or "").strip()
    source = (row.get("source") or "").strip()
    query_core = strip_account_prefix(query, account_name).strip()

    if source == account_name:
        return True
    if account_name and account_name in title:
        return True
    if author_hint and author_hint in title:
        return True
    if query_core and len(query_core) >= 6 and int(row.get("index") or 0) < 3:
        return True
    if known_markers and any(marker in title for marker in known_markers):
        return True
    return False


async def resolve_sogou_popup_candidate(
    search_page: Any,
    row: dict[str, Any],
    account_name: str,
    author_hint: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    box = search_page.locator(SOGOU_RESULT_SELECTOR).nth(int(row.get("index") or 0))
    link = box.locator("h3 a").first
    if not await link.count():
        return None

    popup = None
    try:
        async with search_page.expect_popup(timeout=15000) as popup_info:
            await link.click()
        popup = await popup_info.value

        for _ in range(8):
            await popup.wait_for_timeout(1000)
            current_url = popup.url
            if "mp.weixin.qq.com/s?" not in current_url:
                continue
            try:
                html_text = await popup.content()
            except Exception:
                continue
            validated = validate_candidate_html(
                {
                    "title": (row.get("title") or "").strip(),
                    "source": (row.get("source") or "").strip(),
                    "date": "",
                    "url": current_url,
                    "normalized_url": normalize_article_url(current_url),
                    "query": f"browser-search::{row.get('query') or ''}",
                },
                html_text,
                current_url,
                account_name,
                author_hint,
            )
            if validated:
                return validated
        return None
    except Exception:
        return None
    finally:
        if popup is not None:
            try:
                await popup.close()
            except Exception:
                pass


async def collect_browser_search_candidates(
    browser: AsyncCamoufox,
    account_name: str,
    author_hint: str,
    search_pages: int,
    search_limit: int,
    title_hints: list[str] | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    base_queries = candidate_queries(account_name, author_hint, title_hints=title_hints)
    query_queue: deque[str] = deque(base_queries)
    seen_queries: set[str] = set()
    seen_rows: set[tuple[str, int, str, str]] = set()
    validated_by_url: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    query_budget = max(36, min(60, max(search_limit, 1) * max(search_pages, 1) * 2))
    click_budget = max(40, min(90, max(search_limit, 1) * max(search_pages, 1) * 3))
    rows_per_page = max(6, min(max(search_limit, 1), 8))

    broad_title_hints = await collect_broad_search_title_hints(
        browser,
        account_name,
        search_pages=max(search_pages, 1),
        row_limit=max(4, min(max(search_limit, 1), 8)),
    )
    for broad_hint in reversed(broad_title_hints):
        if broad_hint not in seen_queries:
            query_queue.appendleft(broad_hint)

    broad_query_set = {
        account_name,
        f"{account_name}:",
        f"{account_name}：",
        f"“{account_name}”",
    }

    page = await browser.new_page()
    try:
        while query_queue and len(seen_queries) < query_budget and click_budget > 0:
            query = query_queue.popleft().strip()
            if not query or query in seen_queries:
                continue
            seen_queries.add(query)

            for page_index in range(1, max(search_pages, 1) + 1):
                await page.goto(sogou_search_url(query, page=page_index), wait_until="domcontentloaded", timeout=120000)
                await page.wait_for_timeout(2500)
                rows = await extract_sogou_result_rows(page, rows_per_page)
                if not rows:
                    break

                for row in rows:
                    row["query"] = query
                    row_title = (row.get("title") or "").strip()
                    row_source = (row.get("source") or "").strip()
                    title_has_account_prefix = strip_account_prefix(row_title, account_name) != row_title
                    if row_source.startswith(account_name) or title_has_account_prefix:
                        derived_queries = query_variants_from_title(row_title, account_name)
                        for derived_query in reversed(derived_queries):
                            if derived_query not in seen_queries:
                                query_queue.appendleft(derived_query)

                validate_limit = 0 if query in broad_query_set else min(3, len(rows))
                for row in rows[:validate_limit]:
                    if click_budget <= 0:
                        break
                    row_title = (row.get("title") or "").strip()
                    row_source = (row.get("source") or "").strip()
                    row_key = (query, int(row.get("index") or 0), row_title, row_source)
                    if row_key in seen_rows:
                        continue
                    seen_rows.add(row_key)

                    click_budget -= 1
                    validated = await resolve_sogou_popup_candidate(
                        search_page=page,
                        row=row,
                        account_name=account_name,
                        author_hint=author_hint,
                    )
                    if not validated:
                        continue

                    candidate, details = validated
                    key = article_identity(candidate.get("normalized_url") or candidate.get("url") or "")
                    if not key or key in validated_by_url:
                        continue
                    validated_by_url[key] = validated

                    official_title = (
                        (details.get("meta").title if details.get("meta") else "")
                        or (candidate.get("title") or "")
                        or row_title
                    )
                    derived_queries = query_variants_from_title(official_title, account_name)
                    for derived_query in reversed(derived_queries):
                        if derived_query not in seen_queries:
                            query_queue.appendleft(derived_query)
                if click_budget <= 0:
                    break
    finally:
        await page.close()

    return list(validated_by_url.values())


async def resolve_seed_candidates(
    browser: AsyncCamoufox,
    account_name: str,
    author_hint: str,
    seed_urls: list[str],
    search_pages: int,
    search_limit: int,
    headless: bool,
    allow_headful_fallback: bool = False,
    allow_no_candidates: bool = False,
    failures: list[dict[str, str]] | None = None,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, list[str]]]:
    validated_by_url: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    validated_explicit_seed_count = 0
    locked_account_ids: set[str] = set()
    locked_bizs: set[str] = set()
    locked_account_names: set[str] = set()

    for seed_url in seed_urls:
        record = {
            "title": "",
            "source": account_name,
            "date": "",
            "url": seed_url,
            "normalized_url": normalize_article_url(seed_url),
            "query": "explicit-seed-url",
        }
        validated = await validate_candidate(
            record,
            account_name,
            author_hint,
            headless,
            allow_headful_fallback=allow_headful_fallback,
        )
        if not validated:
            if failures is not None:
                failures.append(
                    {
                        "direction": "seed",
                        "url": seed_url,
                        "error": "Seed URL could not be validated for requested account",
                    }
                )
            continue
        candidate, details = validated
        authority = extract_validation_authority(candidate, details)
        if not (locked_account_ids or locked_bizs or locked_account_names):
            merge_locked_authority(locked_account_ids, locked_bizs, locked_account_names, authority)
        elif not authority_allows_candidate(candidate, details, locked_account_ids, locked_bizs, locked_account_names):
            if failures is not None:
                failures.append(
                    {
                        "direction": "seed",
                        "url": seed_url,
                        "error": "Seed URL resolved to a different account than the authoritative explicit seed",
                    }
                )
            continue
        key = article_identity(candidate.get("normalized_url") or normalize_article_url(seed_url) or seed_url)
        validated_by_url[key] = validated
        validated_explicit_seed_count += 1

    title_hints: list[str] = []
    for candidate, details in validated_by_url.values():
        title_hint = (
            (details.get("meta").title if details.get("meta") else "")
            or (candidate.get("title") or "")
            or ""
        ).strip()
        if title_hint:
            title_hints.append(title_hint)
        for query_hint in details.get("query_hints") or []:
            normalized_hint = str(query_hint or "").strip()
            if normalized_hint:
                title_hints.append(normalized_hint)

    if validated_explicit_seed_count > 0:
        ordered = list(validated_by_url.values())
        ordered.sort(
            key=lambda item: (
                1 if (item[0].get("query") or "") == "explicit-seed-url" else 0,
                max_album_size(item[1].get("album_hints") or []),
                candidate_priority(item[0], account_name, author_hint),
            ),
            reverse=True,
        )
        return ordered, {
            "account_ids": sorted(locked_account_ids),
            "bizs": sorted(locked_bizs),
            "account_names": sorted(locked_account_names),
        }

    try:
        candidates = await collect_search_candidates(
            account_name,
            author_hint,
            search_pages,
            search_limit,
            title_hints=title_hints,
        )
    except Exception:
        candidates = []

    ranked = sorted(candidates, key=lambda row: candidate_priority(row, account_name, author_hint), reverse=True)
    for candidate in ranked:
        validated_candidate = await validate_candidate(
            candidate,
            account_name,
            author_hint,
            headless,
            allow_headful_fallback=allow_headful_fallback,
        )
        if not validated_candidate:
            continue
        validated_row, details = validated_candidate
        authority = extract_validation_authority(validated_row, details)
        if not (locked_account_ids or locked_bizs or locked_account_names):
            merge_locked_authority(locked_account_ids, locked_bizs, locked_account_names, authority)
        elif not authority_allows_candidate(validated_row, details, locked_account_ids, locked_bizs, locked_account_names):
            continue
        key = article_identity(validated_row.get("normalized_url") or validated_row.get("url") or candidate.get("url") or "")
        if key and key not in validated_by_url:
            validated_by_url[key] = validated_candidate

    title_hints = []
    for candidate, details in validated_by_url.values():
        title_hint = (
            (details.get("meta").title if details.get("meta") else "")
            or (candidate.get("title") or "")
            or ""
        ).strip()
        if title_hint:
            title_hints.append(title_hint)
        for query_hint in details.get("query_hints") or []:
            normalized_hint = str(query_hint or "").strip()
            if normalized_hint:
                title_hints.append(normalized_hint)

    browser_validated = await collect_browser_search_candidates(
        browser,
        account_name,
        author_hint,
        search_pages,
        search_limit,
        title_hints=title_hints,
    )
    for validated_row, details in browser_validated:
        authority = extract_validation_authority(validated_row, details)
        if not (locked_account_ids or locked_bizs or locked_account_names):
            merge_locked_authority(locked_account_ids, locked_bizs, locked_account_names, authority)
        elif not authority_allows_candidate(validated_row, details, locked_account_ids, locked_bizs, locked_account_names):
            continue
        key = article_identity(validated_row.get("normalized_url") or validated_row.get("url") or "")
        if key and key not in validated_by_url:
            validated_by_url[key] = (validated_row, details)

    if validated_by_url:
        ordered = list(validated_by_url.values())
        ordered.sort(
            key=lambda item: (
                1 if (item[0].get("query") or "") == "explicit-seed-url" else 0,
                max_album_size(item[1].get("album_hints") or []),
                candidate_priority(item[0], account_name, author_hint),
            ),
            reverse=True,
        )
        return ordered, {
            "account_ids": sorted(locked_account_ids),
            "bizs": sorted(locked_bizs),
            "account_names": sorted(locked_account_names),
        }

    if allow_no_candidates:
        return [], {
            "account_ids": sorted(locked_account_ids),
            "bizs": sorted(locked_bizs),
            "account_names": sorted(locked_account_names),
        }

    raise RuntimeError(f"Could not resolve a live seed article for account: {account_name}")


def apply_canonical_to_record(record: ArticleRecord, canonical_url: str) -> ArticleRecord:
    normalized = normalize_article_url(canonical_url)
    biz = BIZ_RE.search(normalized)
    mid = MID_RE.search(normalized)
    idx = IDX_RE.search(normalized)
    sn = SN_RE.search(normalized)
    chksm = CHKSM_RE.search(normalized)
    record.canonical_url = normalized or record.canonical_url
    record.biz = biz.group(1) if biz else record.biz
    record.mid = mid.group(1) if mid else record.mid
    record.idx = idx.group(1) if idx else record.idx
    record.sn = sn.group(1) if sn else record.sn
    record.chksm = chksm.group(1) if chksm else record.chksm
    return record


def realign_record_storage(record: ArticleRecord, output_root: Path) -> ArticleRecord:
    current_dir = Path(record.article_dir)
    current_markdown = Path(record.markdown_path)
    desired_dir, desired_markdown = article_storage_paths(
        output_root,
        record.title,
        record.date,
        record.mid,
        record.account_name,
        record.author,
        record.series_title,
        record.series_id,
        record.series_item_order,
    )

    if current_dir != desired_dir and current_dir.exists():
        desired_dir.parent.mkdir(parents=True, exist_ok=True)
        if desired_dir.exists():
            if current_dir != desired_dir and current_dir.exists():
                shutil.rmtree(current_dir, ignore_errors=True)
            current_dir = desired_dir
            current_markdown = desired_markdown
        else:
            shutil.move(str(current_dir), str(desired_dir))
            current_dir = desired_dir
            current_markdown = desired_markdown
    elif desired_dir.exists():
        current_dir = desired_dir
        current_markdown = desired_markdown
    elif current_markdown != desired_markdown and current_markdown.exists():
        current_markdown.rename(desired_markdown)
        current_markdown = desired_markdown

    record.article_dir = str(desired_dir)
    record.markdown_path = str(desired_markdown)
    return record


def cleanup_record_storage(record: ArticleRecord) -> None:
    article_dir = Path(record.article_dir)
    if article_dir.exists():
        shutil.rmtree(article_dir)


def record_storage_exists(record: ArticleRecord) -> bool:
    article_dir = Path(record.article_dir)
    markdown_path = Path(record.markdown_path)
    return article_dir.exists() and markdown_path.exists()


def replace_record_if_storage_missing(
    records_by_url: dict[str, ArticleRecord],
    identity: str,
    fresh_record: ArticleRecord,
) -> bool:
    existing = records_by_url.get(identity)
    if existing is None:
        records_by_url[identity] = fresh_record
        return True
    if record_storage_exists(existing):
        cleanup_record_storage(fresh_record)
        return False
    same_article_dir = Path(existing.article_dir) == Path(fresh_record.article_dir)
    same_markdown_path = Path(existing.markdown_path) == Path(fresh_record.markdown_path)
    if not (same_article_dir and same_markdown_path):
        cleanup_record_storage(existing)
    records_by_url[identity] = fresh_record
    return True


def remove_empty_directories(root: Path) -> int:
    if not root.exists():
        return 0

    removed = 0
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True):
        try:
            next(directory.iterdir())
        except StopIteration:
            try:
                directory.rmdir()
                removed += 1
            except OSError:
                continue
        except Exception:
            continue
    return removed


def is_placeholder_record(record: ArticleRecord) -> bool:
    return (
        record.title == "Weixin Official Accounts Platform"
        or (not record.author and not record.date and not record.account_name and record.image_count == 0)
    )


def apply_series_to_record(record: ArticleRecord, album_row: dict[str, Any], album_item: dict[str, Any]) -> ArticleRecord:
    raw_series_title = str(album_row.get("title") or "").strip()
    normalized_title = normalize_series_title(record.account_name, raw_series_title) or raw_series_title
    series_id = str(album_row.get("album_id") or "").strip()
    item_index = str(album_item.get("itemidx") or "").strip()
    try:
        item_order = int(album_item.get("position") or 0)
    except Exception:
        item_order = 0

    if normalized_title:
        record.series_title = normalized_title
    if series_id:
        record.series_id = series_id
    if item_index:
        record.series_item_index = item_index
    if item_order:
        record.series_item_order = item_order
    return record


async def fetch_article_html(page: Any, url: str) -> str:
    await page.goto(url, wait_until="domcontentloaded")
    try:
        await page.wait_for_selector("#js_content", timeout=15000)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        await asyncio.sleep(2)
    return await page.content()


async def build_article_record(
    html_text: str,
    current_url: str,
    direction: str,
    output_root: Path,
    download_images: bool,
) -> tuple[ArticleRecord, str]:
    soup = BeautifulSoup(html_text, "html.parser")
    meta = extract_metadata(soup, html_text, url=current_url)
    if not meta.title:
        meta.title = extract_title_fallback(soup, html_text)
    if not meta.author:
        meta.author = extract_account_name(html_text, soup, meta.author)
    if not meta.publish_time:
        meta.publish_time = extract_publish_time_fallback(html_text)
    if not meta.title:
        raise RuntimeError(f"Failed to parse article title for URL: {current_url}")
    actual_account = extract_account_name(html_text, soup, meta.author)
    account_id = extract_account_id(html_text)
    canonical_url = canonical_from_html(html_text, current_url)
    biz = BIZ_RE.search(canonical_url)
    mid = MID_RE.search(canonical_url)
    idx = IDX_RE.search(canonical_url)
    sn = SN_RE.search(canonical_url)
    chksm = CHKSM_RE.search(canonical_url)
    next_raw = NEXT_RE.search(html_text).group(1) if NEXT_RE.search(html_text) else ""
    pre_raw = PRE_RE.search(html_text).group(1) if PRE_RE.search(html_text) else ""
    article_dir, markdown_path = article_storage_paths(
        output_root,
        meta.title,
        meta.publish_time,
        mid.group(1) if mid else "",
        actual_account,
        meta.author,
    )
    article_dir.mkdir(parents=True, exist_ok=True)

    parsed = process_content(soup)
    markdown = convert_html_to_markdown(parsed.content_html, parsed.code_blocks)
    image_count = len(parsed.image_urls)
    if download_images and parsed.image_urls:
        images_dir = article_dir / "images"
        url_map = await download_all_images(parsed.image_urls, images_dir, concurrency=5)
        markdown = replace_image_urls(markdown, url_map)

    final_markdown = build_markdown(meta, markdown, parsed.media_references, use_frontmatter=True)
    markdown_path.write_text(final_markdown, encoding="utf-8")

    record = ArticleRecord(
        canonical_url=canonical_url,
        title=meta.title,
        author=meta.author,
        account_name=actual_account,
        account_id=account_id,
        date=meta.publish_time,
        biz=biz.group(1) if biz else "",
        mid=mid.group(1) if mid else "",
        idx=idx.group(1) if idx else "",
        sn=sn.group(1) if sn else "",
        chksm=chksm.group(1) if chksm else "",
        next_url=normalize_article_url(next_raw),
        pre_url=normalize_article_url(pre_raw),
        article_dir=str(article_dir),
        markdown_path=str(markdown_path),
        image_count=image_count,
        direction=direction,
        series_title="",
        series_id="",
        series_item_index="",
        series_item_order=0,
    )
    return record, final_markdown


async def crawl_direction(
    browser: AsyncCamoufox,
    start_url: str,
    direction: str,
    output_root: Path,
    download_images: bool,
    headless: bool,
    seen: set[str],
    failures: list[dict[str, str]],
    max_articles: int,
) -> list[ArticleRecord]:
    if direction not in {"next", "pre"}:
        raise ValueError(direction)

    records: list[ArticleRecord] = []
    current = start_url
    page = await browser.new_page()
    try:
        while current:
            normalized = normalize_article_url(current)
            if not normalized or article_identity(normalized) in seen:
                break
            if max_articles and len(seen) >= max_articles:
                break
            try:
                html_text = await fetch_article_html(page, normalized)
                record, _ = await build_article_record(html_text, normalized, direction, output_root, download_images)
                if is_placeholder_record(record):
                    cleanup_record_storage(record)
                    raise RuntimeError("placeholder-page-detected")
            except Exception as exc:
                try:
                    html_text = await fetch_page_html(normalized, headless=headless)
                    record, _ = await build_article_record(html_text, normalized, direction, output_root, download_images)
                except Exception as fallback_exc:
                    failures.append({"direction": direction, "url": normalized, "error": str(fallback_exc or exc)})
                    break
            seen.add(article_identity(record.canonical_url))
            records.append(record)
            current = record.next_url if direction == "next" else record.pre_url
    finally:
        await page.close()
    return records


def album_page_url(biz: str, album_id: str) -> str:
    return f"https://mp.weixin.qq.com/mp/appmsgalbum?__biz={biz}&action=getalbum&album_id={album_id}#wechat_redirect"


async def collect_album_items(
    browser: AsyncCamoufox,
    biz: str,
    album_id: str,
    max_items: int = 0,
    known_article_ids: set[str] | None = None,
) -> tuple[list[dict[str, str]], str, bool]:
    page = await browser.new_page()
    early_exit = False
    try:
        await page.goto(album_page_url(biz, album_id), wait_until="domcontentloaded")
        await page.wait_for_selector(".js_album_item", timeout=30000)

        previous_count = 0
        stable_rounds = 0

        while True:
            snapshot = await page.evaluate(
                """
() => ({
  count: document.querySelectorAll('.js_album_item').length,
  noMore: !!document.querySelector('.js_no_more_album') &&
    getComputedStyle(document.querySelector('.js_no_more_album')).display !== 'none'
})
"""
            )
            count = int(snapshot.get("count") or 0)
            no_more = bool(snapshot.get("noMore"))

            if max_items and count >= max_items:
                break
            if no_more:
                # Small album: all items loaded. If they're all known, treat as early exit.
                if known_article_ids and count > 0:
                    dom_links = await page.evaluate(
                        """() => Array.from(document.querySelectorAll('.js_album_item'))
                           .map(el => el.dataset.link || '')"""
                    )
                    if dom_links and all(article_identity(link) in known_article_ids for link in dom_links):
                        early_exit = True
                break
            # Early exit: stop scrolling once trailing items are all known.
            # New articles appear at the top of the album feed, so a block of
            # known articles at the bottom means we've scrolled past all new content.
            if known_article_ids and count >= 25:
                dom_links = await page.evaluate(
                    """() => Array.from(document.querySelectorAll('.js_album_item'))
                       .map(el => el.dataset.link || '')"""
                )
                recent_known = 0
                for link in reversed(dom_links):
                    if article_identity(link) in known_article_ids:
                        recent_known += 1
                    else:
                        break
                if recent_known >= 10:
                    early_exit = True
                    break
            if count <= previous_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
                previous_count = count
            if stable_rounds >= 3:
                break

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1.5)

        html_text = await page.content()
        soup = BeautifulSoup(html_text, "html.parser")
        album_title = extract_album_title_from_soup(soup)

        raw_items = await page.evaluate(
            """
() => Array.from(document.querySelectorAll('.js_album_item')).map((el) => ({
  title: el.dataset.title || '',
  msgid: el.dataset.msgid || '',
  itemidx: el.dataset.itemidx || '',
  link: el.dataset.link || ''
}))
"""
        )
    finally:
        await page.close()

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_items:
        link = normalize_article_url(str(item.get("link") or ""))
        link_identity = article_identity(link)
        if not link or link_identity in seen:
            continue
        seen.add(link_identity)
        rows.append(
            {
                "title": str(item.get("title") or "").strip(),
                "msgid": str(item.get("msgid") or "").strip(),
                "itemidx": str(item.get("itemidx") or "").strip(),
                "link": link,
                "position": len(rows) + 1,
            }
        )
        if max_items and len(rows) >= max_items:
            break
    return rows, album_title, early_exit


async def archive_album_items(
    browser: AsyncCamoufox,
    album_row: dict[str, Any],
    album_items: list[dict[str, str]],
    output_root: Path,
    download_images: bool,
    records_by_url: dict[str, ArticleRecord],
    seen: set[str],
    failures: list[dict[str, str]],
    max_articles: int,
    headless: bool,
    allow_headful_fallback: bool,
) -> tuple[list[ArticleRecord], list[dict[str, Any]]]:
    records: list[ArticleRecord] = []
    discoveries: list[dict[str, Any]] = []
    page = await browser.new_page()
    try:
        for item in album_items:
            canonical = normalize_article_url(item.get("link") or "")
            canonical_key = article_identity(canonical)
            if not canonical:
                continue
            if canonical_key in records_by_url:
                existing = records_by_url[canonical_key]
                if not record_storage_exists(existing):
                    pass
                else:
                    before = (
                        existing.series_title,
                        existing.series_id,
                        existing.series_item_index,
                        existing.series_item_order,
                        existing.article_dir,
                        existing.markdown_path,
                    )
                    apply_series_to_record(existing, album_row, item)
                    realign_record_storage(existing, output_root)
                    after = (
                        existing.series_title,
                        existing.series_id,
                        existing.series_item_index,
                        existing.series_item_order,
                        existing.article_dir,
                        existing.markdown_path,
                    )
                    if after != before:
                        discoveries.append({"canonical_url": canonical, "biz": existing.biz, "album_hints": [], "related_urls": []})
                    continue
            if canonical_key in seen:
                continue
            if max_articles and len(seen) >= max_articles:
                break
            try:
                html_text = await fetch_article_html(page, canonical)
                record, _ = await build_article_record(html_text, canonical, "album", output_root, download_images)
                if is_placeholder_record(record):
                    cleanup_record_storage(record)
                    raise RuntimeError("placeholder-page-detected")
            except Exception:
                try:
                    html_text = await fetch_page_html(canonical, headless=headless)
                    record, _ = await build_article_record(html_text, canonical, "album", output_root, download_images)
                    if is_placeholder_record(record):
                        cleanup_record_storage(record)
                        raise RuntimeError("placeholder-page-detected")
                except Exception as exc:
                    if allow_headful_fallback:
                        try:
                            html_text = await fetch_page_html(canonical, headless=False)
                            record, _ = await build_article_record(html_text, canonical, "album", output_root, download_images)
                        except Exception as headful_exc:
                            failures.append({"direction": "album", "url": canonical, "error": str(headful_exc or exc)})
                            continue
                    else:
                        failures.append({"direction": "album", "url": canonical, "error": str(exc)})
                        continue

            if not (record.biz and record.mid and record.idx and record.sn):
                apply_canonical_to_record(record, canonical)
            apply_series_to_record(record, album_row, item)
            realign_record_storage(record, output_root)

            seen.add(article_identity(record.canonical_url))
            records.append(record)
            discoveries.append(discovery_from_html(html_text, canonical, record))
    finally:
        await page.close()
    return records, discoveries


def enqueue_url(
    frontier: deque[tuple[str, str]],
    queued_urls: set[str],
    seen_urls: set[str],
    url: str,
    source: str,
    allowed_bizs: set[str] | None = None,
) -> None:
    normalized = normalize_article_url(url)
    identity = article_identity(normalized)
    if not normalized or identity in queued_urls or identity in seen_urls:
        return
    if not url_allowed_by_biz(normalized, allowed_bizs):
        return
    queued_urls.add(identity)
    frontier.append((normalized, source))


def enqueue_discovery_urls(
    frontier: deque[tuple[str, str]],
    queued_urls: set[str],
    seen_urls: set[str],
    discovery: dict[str, Any],
    default_source: str,
    allowed_bizs: set[str] | None = None,
) -> None:
    for url in discovery.get("related_urls") or []:
        enqueue_url(frontier, queued_urls, seen_urls, url, default_source, allowed_bizs=allowed_bizs)


def prune_frontier(
    frontier: deque[tuple[str, str]],
    queued_urls: set[str],
    seen_urls: set[str],
    allowed_bizs: set[str] | None = None,
) -> int:
    kept: deque[tuple[str, str]] = deque()
    new_queued: set[str] = set()
    removed = 0
    for raw_url, source in frontier:
        normalized = normalize_article_url(raw_url)
        identity = article_identity(normalized)
        if not normalized or identity in seen_urls or identity in new_queued:
            removed += 1
            continue
        if not url_allowed_by_biz(normalized, allowed_bizs):
            removed += 1
            continue
        new_queued.add(identity)
        kept.append((normalized, source))
    frontier.clear()
    frontier.extend(kept)
    queued_urls.clear()
    queued_urls.update(new_queued)
    return removed


def register_album_hints(
    album_queue: deque[dict[str, Any]],
    queued_albums: set[tuple[str, str]],
    processed_albums: set[tuple[str, str]],
    album_hints: list[dict[str, str]],
    source_url: str,
    allowed_bizs: set[str] | None = None,
) -> None:
    for album_hint in album_hints:
        biz = (album_hint.get("biz") or "").strip()
        album_id = (album_hint.get("album_id") or "").strip()
        if not (biz and album_id):
            continue
        if allowed_bizs and biz not in allowed_bizs:
            continue
        key = (biz, album_id)
        if key in queued_albums or key in processed_albums:
            continue
        queued_albums.add(key)
        album_queue.append(
            {
                "biz": biz,
                "album_id": album_id,
                "title": (album_hint.get("title") or "").strip(),
                "content_size": (album_hint.get("content_size") or "").strip(),
                "source_url": source_url,
            }
        )


async def fetch_frontier_article(
    candidate_url: str,
    direction: str,
    output_root: Path,
    download_images: bool,
    account_name: str,
    author_hint: str,
    headless: bool,
    allow_headful_fallback: bool = False,
) -> tuple[ArticleRecord, dict[str, Any], dict[str, Any]]:
    candidate = {
        "title": "",
        "source": account_name,
        "date": "",
        "url": candidate_url,
        "normalized_url": normalize_article_url(candidate_url),
        "query": f"frontier-{direction}",
    }
    validated = await validate_candidate(
        candidate,
        account_name,
        author_hint,
        headless,
        allow_headful_fallback=allow_headful_fallback,
    )
    if not validated:
        raise RuntimeError(f"Could not validate frontier article for requested account: {candidate_url}")

    record_meta, details = validated
    canonical = record_meta.get("normalized_url") or canonical_from_html(details["html"], candidate_url)
    record, _ = await build_article_record(
        details["html"],
        canonical,
        direction,
        output_root,
        download_images,
    )
    if is_placeholder_record(record):
        cleanup_record_storage(record)
        if allow_headful_fallback:
            html_text = await fetch_page_html(canonical, headless=False)
            record, _ = await build_article_record(
                html_text,
                canonical,
                direction,
                output_root,
                download_images,
            )
            details["html"] = html_text
            details["album_hints"] = extract_album_hints(html_text)
        else:
            raise RuntimeError("placeholder-page-detected")

    if is_placeholder_record(record):
        cleanup_record_storage(record)
        raise RuntimeError("placeholder-page-detected")

    if not (record.biz and record.mid and record.idx and record.sn):
        apply_canonical_to_record(record, canonical)
        realign_record_storage(record, output_root)

    discovery = discovery_from_html(details["html"], canonical, record)
    return record, details, discovery


def load_bootstrap_archive(root: Path) -> dict[str, Any]:
    crawl_path = root / "crawl-metadata.json"
    index_path = root / "archive-index.json"
    if not crawl_path.exists():
        raise FileNotFoundError(f"Bootstrap crawl metadata not found: {crawl_path}")
    if not index_path.exists():
        raise FileNotFoundError(f"Bootstrap archive index not found: {index_path}")

    crawl = json.loads(crawl_path.read_text(encoding="utf-8"))
    rows = json.loads(index_path.read_text(encoding="utf-8"))

    archive_meta = crawl.get("archive", {}) or {}
    album_hints: list[dict[str, str]] = []
    for run in crawl.get("archive", {}).get("seed_runs", []):
        for album_hint in run.get("album_hints") or []:
            album_hints.append(
                {
                    "title": str(album_hint.get("title") or "").strip(),
                    "biz": str(album_hint.get("biz") or "").strip(),
                    "album_id": str(album_hint.get("album_id") or "").strip(),
                    "content_size": str(album_hint.get("content_size") or "").strip(),
                }
            )
    for album_row in crawl.get("archive", {}).get("processed_albums", []):
        album_hints.append(
            {
                "title": str(album_row.get("title") or "").strip(),
                "biz": str(album_row.get("biz") or "").strip(),
                "album_id": str(album_row.get("album_id") or "").strip(),
                "content_size": str(album_row.get("content_size") or "").strip(),
                }
            )

    # Reindex-based recoveries can preserve per-article series_id/series_title even
    # when the old crawl metadata no longer carries processed_albums. Rebuild album
    # hints from those rows so maintenance can resume every discovered series instead
    # of collapsing to the most recent seed's single album.
    dominant_biz = ""
    for row in rows:
        biz = str(row.get("biz") or "").strip()
        if biz:
            dominant_biz = biz
            break
    if not dominant_biz:
        target_bizs = archive_meta.get("target_bizs") or []
        dominant_biz = str(target_bizs[0]).strip() if target_bizs else ""

    row_album_index: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        album_id = str(row.get("series_id") or "").strip()
        if not album_id:
            continue
        biz = str(row.get("biz") or "").strip() or dominant_biz
        if not biz:
            continue
        key = (biz, album_id)
        current = row_album_index.setdefault(
            key,
            {
                "title": str(row.get("series_title") or "").strip(),
                "biz": biz,
                "album_id": album_id,
                "content_size": "0",
            },
        )
        if not current["title"]:
            current["title"] = str(row.get("series_title") or "").strip()
        current["content_size"] = str(int(current["content_size"]) + 1)

    album_hints.extend(row_album_index.values())

    deduped_album_hints: list[dict[str, str]] = []
    seen_album_keys: set[tuple[str, str]] = set()
    for album_hint in album_hints:
        biz = str(album_hint.get("biz") or "").strip()
        album_id = str(album_hint.get("album_id") or "").strip()
        if not (biz and album_id):
            continue
        key = (biz, album_id)
        if key in seen_album_keys:
            continue
        seen_album_keys.add(key)
        deduped_album_hints.append(
            {
                "title": str(album_hint.get("title") or "").strip(),
                "biz": biz,
                "album_id": album_id,
                "content_size": str(album_hint.get("content_size") or "").strip(),
            }
        )

    frontier_urls: list[str] = []
    for row in rows:
        for key in ("pre_url", "next_url"):
            frontier_urls.append(str(row.get(key) or "").strip())

    return {
        "root": str(root),
        "album_hints": deduped_album_hints,
        "frontier_urls": dedupe_urls(frontier_urls),
        "article_count": len(rows),
        "rows": rows,
    }


def resolve_saved_path(raw_path: str, archive_root: Path) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return archive_root / candidate


def import_bootstrap_records(
    archive_root: Path,
    rows: list[dict[str, Any]],
    output_root: Path,
    records_by_url: dict[str, ArticleRecord],
) -> dict[str, int]:
    imported_count = 0
    skipped_existing_count = 0
    missing_source_count = 0
    reused_destination_count = 0

    for row in rows:
        try:
            record = ArticleRecord(**row)
        except TypeError:
            continue

        canonical = normalize_article_url(record.canonical_url)
        canonical_key = article_identity(canonical)
        if not canonical:
            continue
        if canonical_key in records_by_url:
            skipped_existing_count += 1
            continue

        source_dir = resolve_saved_path(record.article_dir, archive_root)
        source_markdown = resolve_saved_path(record.markdown_path, archive_root)
        desired_dir, desired_markdown = article_storage_paths(
            output_root,
            record.title,
            record.date,
            record.mid,
            record.account_name,
            record.author,
            record.series_title,
            record.series_id,
            record.series_item_order,
        )

        desired_dir.parent.mkdir(parents=True, exist_ok=True)
        if desired_dir.exists():
            reused_destination_count += 1
        elif source_dir.exists():
            shutil.copytree(source_dir, desired_dir)
        elif source_markdown.exists():
            desired_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_markdown, desired_markdown)
        else:
            missing_source_count += 1
            continue

        if not desired_markdown.exists() and source_markdown.exists():
            shutil.copy2(source_markdown, desired_markdown)

        record.canonical_url = canonical
        record.article_dir = str(desired_dir)
        record.markdown_path = str(desired_markdown)
        records_by_url[canonical_key] = record
        imported_count += 1

    return {
        "imported_article_count": imported_count,
        "skipped_existing_count": skipped_existing_count,
        "missing_source_count": missing_source_count,
        "reused_destination_count": reused_destination_count,
    }


def build_archive_snapshot(
    args: argparse.Namespace,
    output_root: Path,
    bootstrap_runs: list[dict[str, Any]],
    seed_runs: list[dict[str, Any]],
    processed_album_rows: list[dict[str, Any]],
    album_queue: deque[dict[str, Any]],
    frontier: deque[tuple[str, str]],
    records_by_url: dict[str, ArticleRecord],
) -> dict[str, Any]:
    deduped_seed_runs: list[dict[str, Any]] = []
    seen_seed_keys: set[tuple[str, str]] = set()
    for row in seed_runs:
        key = (
            article_identity(str(row.get("seed_canonical_url") or row.get("seed_url") or "")),
            str(row.get("query") or "").strip(),
        )
        if key in seen_seed_keys:
            continue
        seen_seed_keys.add(key)
        deduped_seed_runs.append(row)

    deduped_bootstrap_runs: list[dict[str, Any]] = []
    seen_bootstrap_roots: set[str] = set()
    for row in bootstrap_runs:
        root = str(row.get("root") or "").strip()
        if root in seen_bootstrap_roots:
            continue
        seen_bootstrap_roots.add(root)
        deduped_bootstrap_runs.append(row)

    primary_seed = deduped_seed_runs[0] if deduped_seed_runs else {}
    record_account_ids = {
        str(record.account_id or "").strip()
        for record in records_by_url.values()
        if str(record.account_id or "").strip()
    }
    record_bizs = {
        str(record.biz or "").strip()
        for record in records_by_url.values()
        if str(record.biz or "").strip()
    }
    record_account_names = {
        str(record.account_name or "").strip()
        for record in records_by_url.values()
        if str(record.account_name or "").strip()
    }
    seed_account_ids = {
        str(row.get("account_id") or "").strip()
        for row in deduped_seed_runs
        if str(row.get("account_id") or "").strip()
    }
    seed_bizs = {
        str(row.get("biz") or "").strip()
        for row in deduped_seed_runs
        if str(row.get("biz") or "").strip()
    }
    seed_account_names = {
        str(row.get("actual_account") or "").strip()
        for row in deduped_seed_runs
        if str(row.get("actual_account") or "").strip()
    }
    return {
        "account_name": args.account_name,
        "author_hint": args.author_hint,
        "seed_title": primary_seed.get("seed_title") or "",
        "seed_url": primary_seed.get("seed_url") or "",
        "seed_canonical_url": primary_seed.get("seed_canonical_url") or "",
        "account_id": primary_seed.get("account_id") or "",
        "biz": primary_seed.get("biz") or "",
        "article_count": len(records_by_url),
        "bootstrap_run_count": len(deduped_bootstrap_runs),
        "bootstrap_runs": deduped_bootstrap_runs,
        "seed_run_count": len(deduped_seed_runs),
        "seed_runs": deduped_seed_runs,
        "processed_album_count": len(processed_album_rows),
        "processed_albums": processed_album_rows,
        "queued_album_count": len(album_queue),
        "queued_albums": list(album_queue),
        "frontier_pending_count": len(frontier),
        "frontier_pending": [{"url": url, "source": source} for url, source in frontier],
        "target_account_ids": sorted(seed_account_ids or record_account_ids),
        "target_bizs": sorted(seed_bizs or record_bizs),
        "target_account_names": sorted(
            {
                str(args.account_name or "").strip(),
                str(args.author_hint or "").strip(),
                *(seed_account_names or record_account_names),
            }
            - {""}
        ),
        "output_root": str(output_root),
    }


def load_existing_state(output_root: Path) -> dict[str, Any] | None:
    index_path = output_root / "archive-index.json"
    crawl_path = output_root / "crawl-metadata.json"
    if not (index_path.exists() and crawl_path.exists()):
        return None

    rows = json.loads(index_path.read_text(encoding="utf-8"))
    crawl = json.loads(crawl_path.read_text(encoding="utf-8"))
    archive = crawl.get("archive") or {}

    records_by_url: dict[str, ArticleRecord] = {}
    for row in rows:
        try:
            record = ArticleRecord(**row)
        except TypeError:
            continue
        if not record_storage_exists(record):
            continue
        records_by_url[article_identity(record.canonical_url)] = record

    processed_album_rows = archive.get("processed_albums") or []
    queued_album_rows = archive.get("queued_albums") or []
    queued_keys = {
        ((row.get("biz") or "").strip(), (row.get("album_id") or "").strip())
        for row in queued_album_rows
        if (row.get("biz") or "").strip() and (row.get("album_id") or "").strip()
    }
    for row in processed_album_rows:
        key = ((row.get("biz") or "").strip(), (row.get("album_id") or "").strip())
        if not key[0] or not key[1]:
            continue
        if album_row_is_complete(row):
            continue
        if key in queued_keys:
            continue
        queued_album_rows.append(
            {
                "biz": key[0],
                "album_id": key[1],
                "title": str(row.get("title") or "").strip(),
                "content_size": str(row.get("content_size") or "").strip(),
                "source_url": str(row.get("source_url") or output_root).strip(),
            }
        )
        queued_keys.add(key)

    return {
        "records_by_url": records_by_url,
        "failures": crawl.get("failures") or [],
        "bootstrap_runs": archive.get("bootstrap_runs") or [],
        "seed_runs": archive.get("seed_runs") or [],
        "processed_album_rows": processed_album_rows,
        "queued_album_rows": queued_album_rows,
        "frontier_rows": archive.get("frontier_pending") or [],
    }


def prune_resolved_failures(failures: list[dict[str, str]], records_by_url: dict[str, ArticleRecord]) -> list[dict[str, str]]:
    remaining: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    known_identities = set(records_by_url.keys())

    for failure in failures:
        direction = str(failure.get("direction") or "").strip()
        url = str(failure.get("url") or "").strip()
        error = str(failure.get("error") or "").strip()
        identity = article_identity(url) if url else ""
        if identity and identity in known_identities:
            continue
        key = (identity or url, error)
        if key in seen:
            continue
        seen.add(key)
        remaining.append({"direction": direction, "url": url, "error": error})

    return remaining


def album_row_is_complete(row: dict[str, Any]) -> bool:
    if "is_complete" in row:
        return bool(row.get("is_complete"))
    content_size = str(row.get("content_size") or "").strip()
    item_count = row.get("album_item_count")
    try:
        if content_size.isdigit() and item_count is not None:
            return int(item_count) >= int(content_size)
    except Exception:
        pass
    return True


def failure_is_nonfatal_frontier_edge(failure: dict[str, str]) -> bool:
    direction = str(failure.get("direction") or "").strip()
    error = str(failure.get("error") or "").strip()
    if "frontier" not in direction:
        return False
    return (
        "Could not validate frontier article for requested account" in error
        or "placeholder-page-detected" in error
    )


def archive_core_is_complete(archive: dict[str, Any]) -> bool:
    processed_album_rows = archive.get("processed_albums") or []
    queued_album_rows = archive.get("queued_albums") or []
    frontier_rows = archive.get("frontier_pending") or []
    try:
        article_count = int(archive.get("article_count") or 0)
    except Exception:
        article_count = 0

    return (
        article_count > 0
        and bool(processed_album_rows)
        and not queued_album_rows
        and not frontier_rows
        and all(album_row_is_complete(row) for row in processed_album_rows)
    )


def restored_state_core_is_complete(
    records_by_url: dict[str, ArticleRecord],
    processed_album_rows: list[dict[str, Any]],
    album_queue: deque[dict[str, Any]],
    frontier: deque[tuple[str, str]],
) -> bool:
    return (
        bool(records_by_url)
        and bool(processed_album_rows)
        and not album_queue
        and not frontier
        and all(album_row_is_complete(row) for row in processed_album_rows)
    )


def failures_relevant_to_exit(failures: list[dict[str, str]], archive: dict[str, Any]) -> list[dict[str, str]]:
    if not archive_core_is_complete(archive):
        return failures
    return [failure for failure in failures if not failure_is_nonfatal_frontier_edge(failure)]


def log_progress(
    stage: str,
    records_by_url: dict[str, ArticleRecord],
    processed_album_rows: list[dict[str, Any]],
    album_queue: deque[dict[str, Any]],
    frontier: deque[tuple[str, str]],
    failures: list[dict[str, str]],
    detail: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "type": "progress",
        "stage": stage,
        "article_count": len(records_by_url),
        "processed_album_count": len(processed_album_rows),
        "queued_album_count": len(album_queue),
        "frontier_pending_count": len(frontier),
        "failure_count": len(failures),
    }
    if detail:
        payload["detail"] = detail
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def checkpoint_state(
    output_root: Path,
    args: argparse.Namespace,
    bootstrap_runs: list[dict[str, Any]],
    seed_runs: list[dict[str, Any]],
    processed_album_rows: list[dict[str, Any]],
    album_queue: deque[dict[str, Any]],
    frontier: deque[tuple[str, str]],
    records_by_url: dict[str, ArticleRecord],
    failures: list[dict[str, str]],
    stage: str = "checkpoint",
    detail: dict[str, Any] | None = None,
) -> None:
    failures = prune_resolved_failures(failures, records_by_url)
    archive = build_archive_snapshot(
        args,
        output_root,
        bootstrap_runs,
        seed_runs,
        processed_album_rows,
        album_queue,
        frontier,
        records_by_url,
    )
    ordered_records = sorted(
        records_by_url.values(),
        key=lambda record: (record.date or "", record.mid or "", record.title or ""),
    )
    write_indexes(output_root, archive, ordered_records, failures)
    log_progress(stage, records_by_url, processed_album_rows, album_queue, frontier, failures, detail)


def write_indexes(output_root: Path, archive: dict[str, Any], records: list[ArticleRecord], failures: list[dict[str, str]]) -> None:
    index_json = output_root / "archive-index.json"
    index_csv = output_root / "archive-index.csv"
    crawl_json = output_root / "crawl-metadata.json"

    rows = [
        {
            "canonical_url": r.canonical_url,
            "title": r.title,
            "author": r.author,
            "account_name": r.account_name,
            "account_id": r.account_id,
            "series_title": r.series_title,
            "series_id": r.series_id,
            "series_item_index": r.series_item_index,
            "series_item_order": r.series_item_order,
            "date": r.date,
            "biz": r.biz,
            "mid": r.mid,
            "idx": r.idx,
            "sn": r.sn,
            "chksm": r.chksm,
            "next_url": r.next_url,
            "pre_url": r.pre_url,
            "article_dir": r.article_dir,
            "markdown_path": r.markdown_path,
            "image_count": r.image_count,
            "direction": r.direction,
        }
        for r in records
    ]

    index_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with index_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "canonical_url",
                "title",
                "author",
                "account_name",
                "account_id",
                "series_title",
                "series_id",
                "series_item_index",
                "series_item_order",
                "date",
                "biz",
                "mid",
                "idx",
                "sn",
                "chksm",
                "next_url",
                "pre_url",
                "article_dir",
                "markdown_path",
                "image_count",
                "direction",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    crawl_json.write_text(
        json.dumps({"archive": archive, "failures": failures}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def async_main(args: argparse.Namespace) -> int:
    output_root = Path(args.output_root)
    if args.force and output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    restored = None if args.force else load_existing_state(output_root)
    failures: list[dict[str, str]] = list(restored["failures"]) if restored else []
    records_by_url: dict[str, ArticleRecord] = dict(restored["records_by_url"]) if restored else {}
    seed_runs: list[dict[str, Any]] = list(restored["seed_runs"]) if restored else []
    bootstrap_runs: list[dict[str, Any]] = list(restored["bootstrap_runs"]) if restored else []
    processed_album_rows: list[dict[str, Any]] = list(restored["processed_album_rows"]) if restored else []
    processed_albums: set[tuple[str, str]] = {
        ((row.get("biz") or "").strip(), (row.get("album_id") or "").strip())
        for row in processed_album_rows
        if (row.get("biz") or "").strip() and (row.get("album_id") or "").strip() and album_row_is_complete(row)
    }
    queued_album_rows = restored["queued_album_rows"] if restored else []
    queued_albums: set[tuple[str, str]] = {
        ((row.get("biz") or "").strip(), (row.get("album_id") or "").strip())
        for row in queued_album_rows
        if (row.get("biz") or "").strip() and (row.get("album_id") or "").strip()
    }
    album_queue: deque[dict[str, Any]] = deque(queued_album_rows)
    frontier_rows = restored["frontier_rows"] if restored else []
    frontier: deque[tuple[str, str]] = deque(
        (str(row.get("url") or "").strip(), str(row.get("source") or "resume").strip() or "resume")
        for row in frontier_rows
        if str(row.get("url") or "").strip()
    )
    queued_urls: set[str] = {article_identity(url) for url, _ in frontier if normalize_article_url(url)}
    restored_core_complete = restored_state_core_is_complete(records_by_url, processed_album_rows, album_queue, frontier)
    allow_headful_fallback = bool(args.allow_headful_fallback or args.no_headless)
    frontier_activity_since_checkpoint = 0

    async with AsyncCamoufox(headless=not args.no_headless) as browser:
        for bootstrap_root in args.bootstrap_archive_root:
            normalized_root = str(Path(bootstrap_root))
            if restored_core_complete and Path(bootstrap_root).resolve() == output_root.resolve():
                continue
            bootstrap = load_bootstrap_archive(Path(bootstrap_root))
            bootstrap_import = import_bootstrap_records(
                Path(bootstrap_root),
                bootstrap["rows"],
                output_root,
                records_by_url,
            )
            register_album_hints(
                album_queue,
                queued_albums,
                processed_albums,
                bootstrap["album_hints"],
                bootstrap["root"],
            )
            for url in bootstrap["frontier_urls"]:
                enqueue_url(frontier, queued_urls, set(records_by_url.keys()), url, "bootstrap-frontier")
            existing_bootstrap = next((row for row in bootstrap_runs if (row.get("root") or "") == normalized_root), None)
            bootstrap_row = {
                "root": bootstrap["root"],
                "known_album_count": len(bootstrap["album_hints"]),
                "frontier_url_count": len(bootstrap["frontier_urls"]),
                "article_count": bootstrap["article_count"],
                **bootstrap_import,
            }
            if existing_bootstrap is None:
                bootstrap_runs.append(bootstrap_row)
            else:
                existing_bootstrap.update(bootstrap_row)

        pruned_frontier_count = prune_frontier(frontier, queued_urls, set(records_by_url.keys()))
        if pruned_frontier_count:
            checkpoint_state(
                output_root,
                args,
                bootstrap_runs,
                seed_runs,
                processed_album_rows,
                album_queue,
                frontier,
                records_by_url,
                failures,
                stage="bootstrap-import",
                detail={
                    "pruned_frontier_count": pruned_frontier_count,
                    "bootstrap_root_count": len(args.bootstrap_archive_root),
                    "bootstrap_article_count": sum(
                        int((row.get("article_count") or 0)) for row in bootstrap_runs if str(row.get("article_count") or "0").isdigit()
                    ),
                },
            )

        incoming_seed_urls = dedupe_urls(list(args.seed_url))
        archive_is_stable = (
            bool(restored)
            and bool(processed_album_rows)
            and not album_queue
            and not frontier
            and all(album_row_is_complete(row) for row in processed_album_rows)
        )
        if incoming_seed_urls and archive_is_stable:
            seed_runs = []
            failures = []
        should_resolve_seeds = bool(incoming_seed_urls) or not (restored and (album_queue or frontier or records_by_url))
        if args.no_search and not incoming_seed_urls:
            should_resolve_seeds = False
        authority: dict[str, list[str]] = {"account_ids": [], "bizs": [], "account_names": []}
        if should_resolve_seeds:
            seed_candidates, authority = await resolve_seed_candidates(
                browser,
                args.account_name,
                args.author_hint,
                incoming_seed_urls if restored else args.seed_url,
                args.search_pages,
                args.search_limit,
                headless=not args.no_headless,
                allow_headful_fallback=allow_headful_fallback,
                allow_no_candidates=bool(args.bootstrap_archive_root),
                failures=failures,
            )
        else:
            seed_candidates = []

        allowed_account_ids = {account_id for account_id in authority.get("account_ids") or [] if account_id}
        allowed_bizs = {biz for biz in authority.get("bizs") or [] if biz}
        allowed_account_names = {name for name in authority.get("account_names") or [] if name}
        if allowed_account_ids or allowed_bizs or allowed_account_names:
            filtered_records, removed_records = filter_records_by_authority(
                records_by_url,
                allowed_account_ids=allowed_account_ids,
                allowed_bizs=allowed_bizs,
                allowed_account_names=allowed_account_names,
            )
            if removed_records:
                for removed_record in removed_records:
                    cleanup_record_storage(removed_record)
                records_by_url = filtered_records
                failures = prune_resolved_failures(failures, records_by_url)
                checkpoint_state(
                    output_root,
                    args,
                    bootstrap_runs,
                    seed_runs,
                    processed_album_rows,
                    album_queue,
                    frontier,
                    records_by_url,
                    failures,
                    stage="authority-filter",
                    detail={
                        "allowed_account_ids": sorted(allowed_account_ids),
                        "allowed_bizs": sorted(allowed_bizs),
                        "allowed_account_names": sorted(allowed_account_names),
                        "removed_record_count": len(removed_records),
                    },
                )
        if allowed_bizs:
            processed_album_rows = filter_album_rows_by_biz(processed_album_rows, allowed_bizs)
            processed_albums = {
                ((row.get("biz") or "").strip(), (row.get("album_id") or "").strip())
                for row in processed_album_rows
                if (row.get("biz") or "").strip()
                and (row.get("album_id") or "").strip()
                and album_row_is_complete(row)
            }
            filtered_album_rows = filter_album_rows_by_biz(list(album_queue), allowed_bizs)
            album_queue = deque(filtered_album_rows)
            queued_albums = {
                ((row.get("biz") or "").strip(), (row.get("album_id") or "").strip())
                for row in filtered_album_rows
                if (row.get("biz") or "").strip() and (row.get("album_id") or "").strip()
            }
            prune_frontier(frontier, queued_urls, set(records_by_url.keys()), allowed_bizs=allowed_bizs)

        for seed_record, seed_details in seed_candidates:
            if args.max_articles and len(records_by_url) >= args.max_articles:
                break

            seed_title_hint = (seed_details.get("meta").title if seed_details.get("meta") else "") or seed_record.get("title") or ""
            album_hints = sorted(
                seed_details.get("album_hints") or [],
                key=lambda row: int(row.get("content_size") or 0) if str(row.get("content_size") or "").isdigit() else 0,
                reverse=True,
            )

            seed_html = seed_details["html"]
            seed_canonical = seed_record["normalized_url"] or canonical_from_html(seed_html, seed_record["url"])
            seed_article: ArticleRecord | None = None
            seed_discovery = discovery_from_html(seed_html, seed_canonical, None)

            if not seed_details.get("placeholder"):
                seed_article, _ = await build_article_record(
                    seed_html,
                    seed_canonical,
                    "seed",
                    output_root,
                    args.download_images,
                )
                if is_placeholder_record(seed_article):
                    cleanup_record_storage(seed_article)
                    seed_html = await fetch_page_html(seed_canonical, headless=False)
                    seed_article, _ = await build_article_record(
                        seed_html,
                        seed_canonical,
                        "seed",
                        output_root,
                        args.download_images,
                    )

            if seed_article:
                if not (seed_article.biz and seed_article.mid and seed_article.idx and seed_article.sn):
                    apply_canonical_to_record(seed_article, seed_canonical)
                    realign_record_storage(seed_article, output_root)
                seed_discovery = discovery_from_html(seed_html, seed_canonical, seed_article)
                seed_identity = article_identity(seed_article.canonical_url)
                replace_record_if_storage_missing(records_by_url, seed_identity, seed_article)

            register_album_hints(
                album_queue,
                queued_albums,
                processed_albums,
                album_hints,
                seed_canonical,
                allowed_bizs=allowed_bizs,
            )
            enqueue_discovery_urls(
                frontier,
                queued_urls,
                set(records_by_url.keys()),
                seed_discovery,
                "seed-frontier",
                allowed_bizs=allowed_bizs,
            )

            expected_hint = None
            if album_hints:
                try:
                    expected_hint = max(int(row["content_size"]) for row in album_hints if row.get("content_size"))
                except Exception:
                    expected_hint = None

            seed_runs.append(
                {
                    "mode": "seed+album+frontier",
                    "query": seed_record.get("query") or "",
                    "seed_title": seed_article.title if seed_article else (seed_title_hint or (album_hints[0]["title"] if album_hints else "")),
                    "seed_url": seed_record["url"],
                    "seed_canonical_url": seed_article.canonical_url if seed_article else seed_canonical,
                    "account_id": seed_article.account_id if seed_article else seed_details.get("account_id") or "",
                    "biz": seed_article.biz if seed_article else ((album_hints[0].get("biz") if album_hints else "") or ""),
                    "album_hints": album_hints,
                    "expected_count_hint": expected_hint,
                    "seed_related_url_count": len(seed_discovery.get("related_urls") or []),
                    "seed_article_archived": bool(seed_article),
                    "seed_placeholder_only": bool(seed_details.get("placeholder") and not seed_article),
                    "is_expected_count_reached": None,
                }
            )
            checkpoint_state(
                output_root,
                args,
                bootstrap_runs,
                seed_runs,
                processed_album_rows,
                album_queue,
                frontier,
                records_by_url,
                failures,
                stage="seed-resolved",
                detail={"seed_url": seed_record["url"], "album_hint_count": len(album_hints)},
            )

        while album_queue or frontier:
            while album_queue:
                if args.max_articles and len(records_by_url) >= args.max_articles:
                    break
                album_row = album_queue.popleft()
                album_key = (album_row["biz"], album_row["album_id"])
                if album_key in processed_albums and album_row_is_complete(album_row):
                    queued_albums.discard(album_key)
                    continue

                previous_count_raw = str(album_row.get("album_item_count") or "").strip()
                previous_count = int(previous_count_raw) if previous_count_raw.isdigit() else 0
                content_size_raw = str(album_row.get("content_size") or "").strip()
                hinted_size = int(content_size_raw) if content_size_raw.isdigit() else 0
                remaining = max(args.max_articles - len(records_by_url), 0) if args.max_articles else 0
                if args.max_articles:
                    target_item_count = remaining
                else:
                    # Early exit handles termination; just pass the hinted size as a cap.
                    target_item_count = hinted_size if hinted_size else 0

                album_items, resolved_album_title, album_early_exit = await collect_album_items(
                    browser,
                    album_row["biz"],
                    album_row["album_id"],
                    max_items=target_item_count,
                    known_article_ids=set(records_by_url.keys()),
                )
                if album_early_exit:
                    album_row["is_complete"] = True
                if resolved_album_title:
                    album_row["title"] = resolved_album_title

                new_article_count = 0
                batch_size = 25
                for offset in range(0, len(album_items), batch_size):
                    if args.max_articles and len(records_by_url) >= args.max_articles:
                        break
                    batch_items = album_items[offset : offset + batch_size]
                    album_records, album_discoveries = await archive_album_items(
                        browser,
                        album_row,
                        batch_items,
                        output_root,
                        args.download_images,
                        records_by_url,
                        set(records_by_url.keys()),
                        failures,
                        args.max_articles,
                        headless=not args.no_headless,
                        allow_headful_fallback=allow_headful_fallback,
                    )

                    for record in album_records:
                        record_identity = article_identity(record.canonical_url)
                        if record_identity in records_by_url:
                            continue
                        records_by_url[record_identity] = record
                        new_article_count += 1

                    for discovery in album_discoveries:
                        register_album_hints(
                            album_queue,
                            queued_albums,
                            processed_albums,
                            discovery.get("album_hints") or [],
                            discovery.get("canonical_url") or "",
                            allowed_bizs=allowed_bizs,
                        )
                        enqueue_discovery_urls(
                            frontier,
                            queued_urls,
                            set(records_by_url.keys()),
                            discovery,
                            "album-frontier",
                            allowed_bizs=allowed_bizs,
                        )

                    checkpoint_state(
                        output_root,
                        args,
                        bootstrap_runs,
                        seed_runs,
                        processed_album_rows,
                        album_queue,
                        frontier,
                        records_by_url,
                        failures,
                        stage="album-batch",
                        detail={
                            "album_id": album_row["album_id"],
                            "album_title": album_row["title"],
                            "batch_offset": offset,
                            "batch_size": len(batch_items),
                            "new_article_count": new_article_count,
                        },
                    )

                album_complete = True
                if args.max_articles and remaining:
                    if hinted_size and len(album_items) < hinted_size:
                        album_complete = False
                    elif not hinted_size and len(album_items) >= remaining:
                        album_complete = False
                elif not args.max_articles:
                    if hinted_size:
                        album_complete = len(album_items) >= hinted_size
                    elif target_item_count:
                        album_complete = len(album_items) < target_item_count

                # Early exit via bootstrap means we stopped scrolling because
                # all trailing items were already known — the album is effectively complete.
                if album_early_exit:
                    album_complete = True

                processed_album_rows = [
                    row
                    for row in processed_album_rows
                    if not (
                        (row.get("biz") or "").strip() == album_row["biz"]
                        and (row.get("album_id") or "").strip() == album_row["album_id"]
                    )
                ]
                processed_album_rows.append(
                    {
                        "biz": album_row["biz"],
                        "album_id": album_row["album_id"],
                        "title": album_row["title"],
                        "content_size": album_row["content_size"],
                        "source_url": album_row["source_url"],
                        "album_item_count": len(album_items),
                        "new_article_count": new_article_count,
                        "is_complete": album_complete,
                    }
                )
                if album_complete:
                    processed_albums.add(album_key)
                    queued_albums.discard(album_key)
                else:
                    next_album_row = dict(album_row)
                    next_album_row["album_item_count"] = len(album_items)
                    queued_albums.add(album_key)
                    album_queue.append(next_album_row)
                checkpoint_state(
                    output_root,
                    args,
                    bootstrap_runs,
                    seed_runs,
                    processed_album_rows,
                    album_queue,
                    frontier,
                    records_by_url,
                    failures,
                    stage="album-state",
                    detail={
                        "album_id": album_row["album_id"],
                        "album_title": album_row["title"],
                        "album_item_count": len(album_items),
                        "album_complete": album_complete,
                        "new_article_count": new_article_count,
                    },
                )

            if args.max_articles and len(records_by_url) >= args.max_articles:
                break
            if not frontier:
                continue

            frontier_url, direction = frontier.popleft()
            frontier_identity = article_identity(frontier_url)
            queued_urls.discard(frontier_identity)
            frontier_activity_since_checkpoint += 1
            if frontier_identity in records_by_url:
                if frontier_activity_since_checkpoint >= 5:
                    checkpoint_state(
                        output_root,
                        args,
                        bootstrap_runs,
                        seed_runs,
                        processed_album_rows,
                        album_queue,
                        frontier,
                        records_by_url,
                        failures,
                        stage="frontier-skip",
                        detail={"url": frontier_url, "direction": direction},
                    )
                    frontier_activity_since_checkpoint = 0
                continue
            try:
                record, details, discovery = await fetch_frontier_article(
                    frontier_url,
                    direction,
                    output_root,
                    args.download_images,
                    args.account_name,
                    args.author_hint,
                    headless=not args.no_headless,
                    allow_headful_fallback=allow_headful_fallback,
                )
            except Exception as exc:
                failures.append({"direction": direction, "url": frontier_url, "error": str(exc)})
                if frontier_activity_since_checkpoint >= 5:
                    checkpoint_state(
                        output_root,
                        args,
                        bootstrap_runs,
                        seed_runs,
                        processed_album_rows,
                        album_queue,
                        frontier,
                        records_by_url,
                        failures,
                        stage="frontier-failure",
                        detail={"url": frontier_url, "direction": direction, "error": str(exc)},
                    )
                    frontier_activity_since_checkpoint = 0
                continue

            record_identity = article_identity(record.canonical_url)
            if record_identity in records_by_url and not replace_record_if_storage_missing(records_by_url, record_identity, record):
                continue
            records_by_url[record_identity] = record
            register_album_hints(
                album_queue,
                queued_albums,
                processed_albums,
                details.get("album_hints") or [],
                record.canonical_url,
                allowed_bizs=allowed_bizs,
            )
            enqueue_discovery_urls(
                frontier,
                queued_urls,
                set(records_by_url.keys()),
                discovery,
                f"frontier-{direction}",
                allowed_bizs=allowed_bizs,
            )
            if frontier_activity_since_checkpoint >= 5:
                checkpoint_state(
                    output_root,
                    args,
                    bootstrap_runs,
                    seed_runs,
                    processed_album_rows,
                    album_queue,
                    frontier,
                    records_by_url,
                    failures,
                    stage="frontier-success",
                    detail={"url": record.canonical_url, "direction": direction},
                )
                frontier_activity_since_checkpoint = 0

    checkpoint_state(
        output_root,
        args,
        bootstrap_runs,
        seed_runs,
        processed_album_rows,
        album_queue,
        frontier,
        records_by_url,
        failures,
        stage="final",
    )
    remove_empty_directories(output_root / "articles")
    failures = prune_resolved_failures(failures, records_by_url)
    archive = build_archive_snapshot(
        args,
        output_root,
        bootstrap_runs,
        seed_runs,
        processed_album_rows,
        album_queue,
        frontier,
        records_by_url,
    )
    fatal_failures = failures_relevant_to_exit(failures, archive)
    health_status = "success"
    if fatal_failures:
        health_status = "failed"
    elif failures:
        health_status = "success-with-warnings"
    print(
        json.dumps(
            {
                "archive": archive,
                "failures": failures,
                "health": {
                    "status": health_status,
                    "fatal_failure_count": len(fatal_failures),
                    "edge_failure_count": len(failures) - len(fatal_failures),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not fatal_failures else 1


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()
