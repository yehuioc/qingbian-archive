from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode, urlparse

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as html_to_markdown


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class ZhihuRecord:
    platform: str
    kind: str
    source_id: str
    title: str
    author: str
    url: str
    created: str
    updated: str
    voteup_count: int
    comment_count: int
    markdown_path: str
    body_sha256: str
    body_length: int
    fetched_at: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive a Zhihu user's articles, answers, and pins.")
    parser.add_argument("--action", choices=["doctor", "run", "audit"], default="run")
    parser.add_argument("--user-token", default="qingbian")
    parser.add_argument("--account-name", default="\u8bf7\u8fa9")
    parser.add_argument("--output-root", default="")
    parser.add_argument("--kinds", default="articles,answers,pins")
    parser.add_argument("--cookie-file", default="")
    parser.add_argument("--max-items", type=int, default=0)
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--sleep", type=float, default=0.8)
    parser.add_argument("--fetch-mode", choices=["auto", "api", "browser"], default="auto")
    parser.add_argument("--browser-executable", default="")
    return parser.parse_args()


def parse_cookie(raw: str) -> dict[str, str]:
    jar: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        jar[key.strip()] = value.strip()
    return jar


def _extract_cookie_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("cookies", "Cookies", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def cookie_header_from_json(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    records = _extract_cookie_records(payload)
    pairs: dict[str, str] = {}
    for record in records:
        name = record.get("name") or record.get("Name")
        value = record.get("value") or record.get("Value")
        domain = str(record.get("domain") or record.get("Domain") or "")
        if not name or value is None:
            continue
        # Accept browser exports for Zhihu plus domain-less test fixtures.
        if domain and "zhihu.com" not in domain.lower():
            continue
        pairs[str(name)] = str(value)
    return "; ".join(f"{name}={value}" for name, value in pairs.items())


def playwright_cookies(cookie_raw: str, cookie_file: str = "") -> list[dict[str, Any]]:
    if cookie_file:
        path = Path(cookie_file)
        if path.exists():
            raw = path.read_text(encoding="utf-8-sig").strip()
            try:
                records = _extract_cookie_records(json.loads(raw))
            except json.JSONDecodeError:
                records = []
            cookies = []
            for record in records:
                name = record.get("name") or record.get("Name")
                value = record.get("value") or record.get("Value")
                domain = str(record.get("domain") or record.get("Domain") or "www.zhihu.com")
                if not name or value is None or "zhihu.com" not in domain.lower():
                    continue
                item: dict[str, Any] = {
                    "name": str(name),
                    "value": str(value),
                    "domain": domain,
                    "path": str(record.get("path") or record.get("Path") or "/"),
                }
                expires = record.get("expirationDate") or record.get("expires") or record.get("Expires")
                if expires:
                    try:
                        item["expires"] = float(expires)
                    except Exception:
                        pass
                cookies.append(item)
            if cookies:
                return cookies
    return [
        {"name": name, "value": value, "domain": ".zhihu.com", "path": "/"}
        for name, value in parse_cookie(cookie_raw).items()
    ]


def load_cookie(cookie_file: str = "") -> tuple[str, str]:
    if cookie_file:
        path = Path(cookie_file)
        if path.exists():
            raw = path.read_text(encoding="utf-8-sig").strip()
            if raw:
                converted = cookie_header_from_json(raw)
                if converted:
                    return converted, f"file:{path}:json-cookie-export"
                return raw, f"file:{path}:raw-cookie-header"
    raw = os.environ.get("ZHIHU_COOKIE", "").strip()
    if raw:
        return raw, "env:ZHIHU_COOKIE"
    return "", ""


def sign(url: str, dc0: str) -> str:
    parsed = urlparse(url)
    path_q = parsed.path + (("?" + parsed.query) if parsed.query else "")
    raw = f"101_3_3.0+{path_q}+{dc0}"
    return "2.0_" + hashlib.md5(raw.encode("utf-8")).hexdigest()


def clean_markdown(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for img in soup.find_all("img"):
        if img.get("data-original") and not img.get("src"):
            img["src"] = img["data-original"]
    return html_to_markdown(str(soup), heading_style="ATX").strip()


def safe_filename(value: str, fallback: str = "untitled") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value or "").strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned[:120] or fallback).strip()


def format_ts(value: Any) -> str:
    try:
        number = int(value)
    except Exception:
        return ""
    if number <= 0:
        return ""
    return datetime.fromtimestamp(number, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def normalize_zhihu_url(url: str) -> str:
    return (url or "").replace("http://www.zhihu.com/", "https://www.zhihu.com/")


def browser_executable(explicit: str = "") -> str:
    candidates = [
        explicit,
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("browser fallback requires a local Chrome or Edge executable")


def browser_route(kind: str) -> str:
    if kind == "articles":
        return "posts"
    if kind == "answers":
        return "answers"
    if kind == "pins":
        return "pins"
    raise ValueError(f"unsupported browser kind: {kind}")


def browser_url_needle(user_token: str, kind: str) -> str:
    if kind == "articles":
        return f"/members/{user_token}/articles"
    if kind == "answers":
        return f"/members/{user_token}/answers"
    if kind == "pins":
        return f"/api/v4/v2/pins/{user_token}/moments"
    raise ValueError(f"unsupported browser kind: {kind}")


async def browser_fetch_page(page: Any, url: str) -> dict[str, Any]:
    url = normalize_zhihu_url(url)
    last_result: dict[str, Any] = {}
    for attempt in range(1, 4):
        result = await page.evaluate(
            """async url => {
                const response = await fetch(url, { credentials: 'include' });
                const text = await response.text();
                return { status: response.status, text };
            }""",
            url,
        )
        last_result = result
        if int(result.get("status") or 0) == 200:
            return json.loads(result.get("text") or "{}")
        if attempt < 3:
            await page.wait_for_timeout(1500 * attempt)
    raise RuntimeError(
        f"browser fetch HTTP {last_result.get('status')} on {url}: {str(last_result.get('text') or '')[:300]}"
    )


async def browser_fetch_kind(
    playwright: Any,
    browser: Any,
    cookie_items: list[dict[str, Any]],
    user_token: str,
    kind: str,
    max_items: int,
    sleep_seconds: float,
) -> dict[str, Any]:
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
    )
    context = await browser.new_context(locale="zh-CN", user_agent=ua)
    await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    await context.add_cookies(cookie_items)
    page = await context.new_page()
    needle = browser_url_needle(user_token, kind)
    captured: list[dict[str, Any]] = []

    async def on_response(response: Any) -> None:
        if needle not in response.url or response.status != 200:
            return
        try:
            payload = await response.json()
        except Exception:
            return
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            captured.append(payload)

    page.on("response", lambda response: asyncio.create_task(on_response(response)))
    await page.goto(f"https://www.zhihu.com/people/{user_token}/{browser_route(kind)}", wait_until="domcontentloaded", timeout=60000)
    for _ in range(30):
        if captured:
            break
        await page.wait_for_timeout(500)
    if not captured:
        await page.mouse.wheel(0, 3000)
        await page.wait_for_timeout(3000)
    if not captured:
        await context.close()
        raise RuntimeError(f"browser fallback did not observe a {kind} list response")

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    current = captured[0]
    reported_total = 0
    page_count = 0
    last_is_end = False
    while True:
        page_count += 1
        for item in current.get("data") or []:
            source_id = str(item.get("id") or "")
            if source_id and source_id in seen:
                continue
            seen.add(source_id)
            records.append(item)
            if max_items and len(records) >= max_items:
                await context.close()
                return {
                    "items": records,
                    "reported_total": reported_total or len(records),
                    "visible_count": len(records),
                    "pages": page_count,
                    "last_is_end": last_is_end,
                }
        paging = current.get("paging") or {}
        if paging.get("totals") is not None:
            reported_total = int(paging.get("totals") or 0)
        last_is_end = bool(paging.get("is_end"))
        if paging.get("is_end"):
            break
        next_url = paging.get("next")
        if not next_url:
            break
        await page.wait_for_timeout(max(0, int(sleep_seconds * 1000)))
        current = await browser_fetch_page(page, next_url)

    await context.close()
    return {
        "items": records,
        "reported_total": reported_total or len(records),
        "visible_count": len(records),
        "pages": page_count,
        "last_is_end": last_is_end,
    }


def browser_fetch_kinds(
    cookie_raw: str,
    cookie_file: str,
    user_token: str,
    kinds: list[str],
    max_items: int,
    sleep_seconds: float,
    browser_path: str,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    from playwright.async_api import async_playwright

    async def run() -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
        cookie_items = playwright_cookies(cookie_raw, cookie_file)
        if not cookie_items:
            raise RuntimeError("browser fallback requires usable Zhihu cookies")
        executable = browser_executable(browser_path)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                executable_path=executable,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                result: dict[str, list[dict[str, Any]]] = {}
                stats: dict[str, dict[str, Any]] = {}
                for kind in kinds:
                    payload = await browser_fetch_kind(
                        playwright, browser, cookie_items, user_token, kind, max_items, sleep_seconds
                    )
                    items = payload["items"]
                    result[kind] = items
                    reported_total = int(payload.get("reported_total") or len(items))
                    stats[kind] = {
                        "reported_total": reported_total,
                        "visible_count": len(items),
                        "hidden_or_unavailable_count": None if max_items else max(0, reported_total - len(items)),
                        "pages": payload.get("pages"),
                        "last_is_end": payload.get("last_is_end"),
                    }
                return result, stats
            finally:
                await browser.close()

    return asyncio.run(run())


class ZhihuClient:
    def __init__(self, cookie_raw: str):
        self.cookie = parse_cookie(cookie_raw)
        self.dc0 = self.cookie.get("d_c0", "")
        self.zc0 = self.cookie.get("z_c0", "")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://www.zhihu.com/",
                "x-requested-with": "fetch",
                "x-zse-93": "101_3_3.0",
            }
        )
        self.session.cookies.update(self.cookie)

    def check_cookie(self) -> dict[str, Any]:
        return {
            "has_cookie": bool(self.cookie),
            "has_d_c0": bool(self.dc0),
            "has_z_c0": bool(self.zc0),
            "status": "ok" if self.cookie and self.dc0 and self.zc0 else "credential_required",
        }

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        full = url
        if params:
            full = f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"
        headers = {"x-zse-96": sign(full, self.dc0)} if self.dc0 else {}
        response = self.session.get(full, headers=headers, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code} on {full}: {response.text[:300]}")
        return response.json()

    def paginate(self, url: str, page_size: int, max_items: int, sleep_seconds: float) -> Iterable[dict[str, Any]]:
        offset = 0
        yielded = 0
        while True:
            data = self.get_json(url, {"offset": offset, "limit": page_size})
            items = data.get("data") or []
            for item in items:
                yield item
                yielded += 1
                if max_items and yielded >= max_items:
                    return
            paging = data.get("paging") or {}
            if paging.get("is_end") or not items:
                return
            offset += page_size
            time.sleep(sleep_seconds)


def item_body(kind: str, item: dict[str, Any]) -> str:
    if kind == "pins":
        blocks = item.get("content") or []
        if isinstance(blocks, str):
            return BeautifulSoup(blocks, "lxml").get_text(" ", strip=True)
        pieces = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                pieces.append(BeautifulSoup(block.get("content") or "", "lxml").get_text(" ", strip=True))
        if pieces:
            return "\n\n".join(part for part in pieces if part).strip()
        return str(item.get("excerpt") or item.get("content_text") or "").strip()
    return clean_markdown(item.get("content") or "")


def item_title(kind: str, item: dict[str, Any]) -> str:
    if kind == "articles":
        return str(item.get("title") or "")
    if kind == "answers":
        question = item.get("question") or {}
        return str(question.get("title") or f"answer-{item.get('id')}")
    text = item_body(kind, item)
    return text[:60] or f"pin-{item.get('id')}"


def item_url(kind: str, item: dict[str, Any]) -> str:
    if kind == "articles":
        return str(item.get("url") or f"https://zhuanlan.zhihu.com/p/{item.get('id')}")
    if kind == "answers":
        question = item.get("question") or {}
        return f"https://www.zhihu.com/question/{question.get('id')}/answer/{item.get('id')}"
    return f"https://www.zhihu.com/pin/{item.get('id')}"


def kind_endpoint(user_token: str, kind: str) -> str:
    if kind == "articles":
        return f"https://www.zhihu.com/api/v4/members/{user_token}/articles?include=content,voteup_count,comment_count,created,updated"
    if kind == "answers":
        return (
            f"https://www.zhihu.com/api/v4/members/{user_token}/answers"
            "?include=content,voteup_count,comment_count,created_time,updated_time,question.title"
        )
    if kind == "pins":
        return f"https://www.zhihu.com/api/v4/v2/pins/profile/{user_token}"
    raise ValueError(f"unsupported kind: {kind}")


def write_record(output_root: Path, account_name: str, kind: str, item: dict[str, Any]) -> ZhihuRecord:
    source_id = str(item.get("id") or "")
    title = item_title(kind, item).strip()
    body = item_body(kind, item)
    created_raw = item.get("created") if kind == "articles" else item.get("created_time") or item.get("created")
    updated_raw = item.get("updated") if kind == "articles" else item.get("updated_time") or item.get("updated")
    created = format_ts(created_raw)
    updated = format_ts(updated_raw)
    date_prefix = created[:10].replace("-", "") if created else "unknown-date"
    safe_title = safe_filename(title, source_id or kind)
    article_dir = output_root / "items" / safe_filename(account_name, "unknown-account") / kind / f"{date_prefix}-{source_id}-{safe_title}"
    article_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = article_dir / f"{safe_title}.md"
    url = item_url(kind, item)
    header = [
        "---",
        f"platform: zhihu",
        f"kind: {kind}",
        f"source_id: {source_id}",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"author: {json.dumps(account_name, ensure_ascii=False)}",
        f"url: {json.dumps(url, ensure_ascii=False)}",
        f"created: {json.dumps(created, ensure_ascii=False)}",
        f"updated: {json.dumps(updated, ensure_ascii=False)}",
        "---",
        "",
        f"# {title or source_id}",
        "",
        body,
        "",
    ]
    markdown_path.write_text("\n".join(header), encoding="utf-8")
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return ZhihuRecord(
        platform="zhihu",
        kind=kind,
        source_id=source_id,
        title=title,
        author=account_name,
        url=url,
        created=created,
        updated=updated,
        voteup_count=int(item.get("voteup_count") or item.get("like_count") or 0),
        comment_count=int(item.get("comment_count") or 0),
        markdown_path=str(markdown_path),
        body_sha256=body_hash,
        body_length=len(body),
        fetched_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def write_indexes(output_root: Path, records: list[ZhihuRecord], status: dict[str, Any]) -> None:
    rows = [asdict(record) for record in records]
    (output_root / "archive-index.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (output_root / "archive-index.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = list(rows[0].keys()) if rows else [field.name for field in ZhihuRecord.__dataclass_fields__.values()]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    (output_root / "crawl-metadata.json").write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_status(output_root: Path, status: dict[str, Any]) -> None:
    (output_root / "crawl-metadata.json").write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def existing_archive_health(output_root: Path) -> dict[str, Any]:
    index_path = output_root / "archive-index.json"
    metadata_path = output_root / "crawl-metadata.json"
    rows = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    previous = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    missing = []
    for row in rows if isinstance(rows, list) else []:
        path = Path(row.get("markdown_path") or "")
        if not path.exists():
            missing.append({"source_id": row.get("source_id"), "markdown_path": str(path)})
    return {
        "has_index": isinstance(rows, list) and bool(rows),
        "record_count": len(rows) if isinstance(rows, list) else 0,
        "missing_markdown_count": len(missing),
        "previous_metadata": previous if isinstance(previous, dict) else {},
    }


def degraded_status(
    output_root: Path,
    args: argparse.Namespace,
    cookie_source: str,
    used_fetch_mode: str,
    reason: str,
) -> dict[str, Any]:
    health = existing_archive_health(output_root)
    previous = health["previous_metadata"]
    last_success_at = previous.get("fetched_at") if previous.get("status") == "success" else previous.get("last_success_fetched_at", "")
    last_success_count = (
        previous.get("record_count")
        if previous.get("status") == "success"
        else previous.get("last_success_record_count", health["record_count"])
    )
    return {
        "platform": "zhihu",
        "user_token": args.user_token,
        "account_name": args.account_name,
        "status": "degraded_fetch_failed_local_cache_preserved",
        "fetch_mode": used_fetch_mode,
        "cookie_source": cookie_source,
        "fetched_at": previous.get("fetched_at", ""),
        "failed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "failure_reason": reason[:1000],
        "last_success_fetched_at": last_success_at,
        "last_success_record_count": int(last_success_count or health["record_count"]),
        "record_count": health["record_count"],
        "existing_missing_markdown_count": health["missing_markdown_count"],
        "counts": previous.get("counts") or {},
        "browser_stats": previous.get("browser_stats") or {},
    }


def print_payload(payload: dict[str, Any]) -> None:
    # ASCII stdout avoids Windows console mojibake; persisted files remain UTF-8.
    print(json.dumps(payload, ensure_ascii=True, indent=2))


def audit(output_root: Path) -> dict[str, Any]:
    index_path = output_root / "archive-index.json"
    rows = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    missing = []
    for row in rows:
        path = Path(row.get("markdown_path") or "")
        if not path.exists():
            missing.append({"source_id": row.get("source_id"), "title": row.get("title"), "markdown_path": str(path)})
    return {
        "output_root": str(output_root),
        "index_count": len(rows),
        "markdown_count": len(list((output_root / "items").rglob("*.md"))) if (output_root / "items").exists() else 0,
        "missing_markdown_count": len(missing),
        "missing_markdown": missing,
    }


def main() -> int:
    args = parse_args()
    if args.output_root:
        output_root = Path(args.output_root).resolve()
    else:
        repo_root = Path(__file__).resolve().parent.parent
        output_root = repo_root / "ingestion" / "10-Raw" / "Zhihu" / args.account_name
    output_root.mkdir(parents=True, exist_ok=True)
    cookie_raw, cookie_source = load_cookie(args.cookie_file)
    client = ZhihuClient(cookie_raw)
    cookie_status = client.check_cookie()

    if args.action == "doctor":
        payload = {
            "platform": "zhihu",
            "user_token": args.user_token,
            "output_root": str(output_root),
            "cookie_source": cookie_source,
            **cookie_status,
        }
        print_payload(payload)
        return 0 if payload["status"] == "ok" else 2

    if args.action == "audit":
        print_payload(audit(output_root))
        return 0

    if cookie_status["status"] != "ok":
        status = {
            "platform": "zhihu",
            "user_token": args.user_token,
            "account_name": args.account_name,
            "status": "credential_required",
            "reason": "ZHIHU_COOKIE or --cookie-file must include at least d_c0 and z_c0.",
            "cookie_source": cookie_source,
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "counts": {},
        }
        if existing_archive_health(output_root)["has_index"]:
            write_status(output_root, status)
        else:
            write_indexes(output_root, [], status)
        print_payload(status)
        return 2

    kinds = [part.strip() for part in args.kinds.split(",") if part.strip()]
    records: list[ZhihuRecord] = []
    counts: dict[str, int] = {}
    used_fetch_mode = "api"
    browser_stats: dict[str, dict[str, Any]] = {}

    def write_items(items_by_kind: dict[str, list[dict[str, Any]]]) -> None:
        for item_kind, items in items_by_kind.items():
            count = 0
            for item in items:
                records.append(write_record(output_root, args.account_name, item_kind, item))
                count += 1
            counts[item_kind] = count

    try:
        if args.fetch_mode == "browser":
            used_fetch_mode = "browser"
            browser_items, browser_stats = browser_fetch_kinds(
                cookie_raw, args.cookie_file, args.user_token, kinds, args.max_items, args.sleep, args.browser_executable
            )
            write_items(browser_items)
        else:
            api_items: dict[str, list[dict[str, Any]]] = {}
            try:
                for kind in kinds:
                    endpoint = kind_endpoint(args.user_token, kind)
                    api_items[kind] = list(client.paginate(endpoint, args.page_size, args.max_items, args.sleep))
                write_items(api_items)
            except RuntimeError as exc:
                if args.fetch_mode != "auto" or "10003" not in str(exc):
                    raise
                used_fetch_mode = "browser-after-api-10003"
                records.clear()
                counts.clear()
                browser_items, browser_stats = browser_fetch_kinds(
                    cookie_raw, args.cookie_file, args.user_token, kinds, args.max_items, args.sleep, args.browser_executable
                )
                write_items(browser_items)
    except RuntimeError as exc:
        health = existing_archive_health(output_root)
        if health["has_index"] and health["missing_markdown_count"] == 0:
            status = degraded_status(output_root, args, cookie_source, used_fetch_mode, str(exc))
            write_status(output_root, status)
            print_payload(status)
            return 3
        raise

    status = {
        "platform": "zhihu",
        "user_token": args.user_token,
        "account_name": args.account_name,
        "status": "success",
        "fetch_mode": used_fetch_mode,
        "cookie_source": cookie_source,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "counts": counts,
        "browser_stats": browser_stats,
        "record_count": len(records),
    }
    write_indexes(output_root, records, status)
    print_payload(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
