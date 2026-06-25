from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from miku_ai import get_wexin_article


def load_archive_module(script_path: Path):
    spec = importlib.util.spec_from_file_location("wechat_archive_runtime", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load archive runtime from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["wechat_archive_runtime"] = module
    spec.loader.exec_module(module)
    return module


def parse_date(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--author-hint", default="")
    parser.add_argument("--seed-url", action="append", default=[])
    parser.add_argument("--review-days", type=int, default=3)
    parser.add_argument("--search-limit", type=int, default=10)
    args = parser.parse_args()

    archive_root = Path(args.archive_root)
    index_path = archive_root / "archive-index.json"
    crawl_path = archive_root / "crawl-metadata.json"
    items = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    crawl = json.loads(crawl_path.read_text(encoding="utf-8")) if crawl_path.exists() else {}
    archive = crawl.get("archive") or {}
    markdown_count = len(list((archive_root / "articles").rglob("*.md"))) if (archive_root / "articles").exists() else 0

    latest_rows = sorted(items, key=lambda row: (row.get("date") or row.get("publish_datetime") or ""), reverse=True)
    latest_dates = [row.get("date") or row.get("publish_datetime") for row in latest_rows[:5]]
    cutoff = datetime.now() - timedelta(days=args.review_days)
    recent_local = [
        {
            "title": row.get("title"),
            "date": row.get("date") or row.get("publish_datetime"),
            "canonical_url": row.get("canonical_url"),
            "biz": row.get("biz"),
            "series_title": row.get("series_title") or "未分系列",
        }
        for row in latest_rows
        if parse_date(row.get("date") or row.get("publish_datetime") or "") and parse_date(row.get("date") or row.get("publish_datetime") or "") >= cutoff
    ]

    result: dict[str, object] = {
        "archive_root": str(archive_root),
        "index_count": len(items),
        "markdown_count": markdown_count,
        "crawl_article_count": archive.get("article_count"),
        "index_markdown_gap": markdown_count - len(items),
        "latest_dates": latest_dates,
        "recent_local_count": len(recent_local),
        "recent_local_items": recent_local,
        "series_top": Counter((row.get("series_title") or "未分系列") for row in items).most_common(10),
        "biz_top": Counter((row.get("biz") or "") for row in items).most_common(10),
        "failures_count": len(crawl.get("failures") or []),
        "target_bizs": archive.get("target_bizs") or [],
        "target_account_ids": archive.get("target_account_ids") or [],
        "live_search_status": "skipped",
        "raw_search_count": None,
        "validated_same_account_count": None,
        "validated_other_account_count": None,
        "unvalidated_count": None,
        "live_recent_missing_local": [],
        "validated_same_account_items": [],
    }

    if args.seed_url:
        archive_module = load_archive_module(Path(__file__).with_name("wechat_account_archive.py"))
        seed_url = args.seed_url[0]
        seed_candidate = {
            "title": "",
            "source": args.account_name,
            "date": "",
            "url": seed_url,
            "normalized_url": archive_module.normalize_article_url(seed_url),
            "query": "explicit-seed-url",
        }
        try:
            validated_seed = await archive_module.validate_candidate(
                seed_candidate,
                args.account_name,
                args.author_hint or args.account_name,
                True,
                allow_headful_fallback=False,
            )
        except Exception as exc:
            result["live_search_status"] = f"seed_validation_error:{exc}"
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if not validated_seed:
            result["live_search_status"] = "seed_validation_failed"
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        _, seed_details = validated_seed
        authority = archive_module.extract_validation_authority(seed_candidate, seed_details)
        locked_ids: set[str] = set()
        locked_bizs: set[str] = set()
        locked_names: set[str] = set()
        archive_module.merge_locked_authority(locked_ids, locked_bizs, locked_names, authority)

        try:
            search_results = await get_wexin_article(args.account_name, args.search_limit)
        except Exception as exc:
            result["live_search_status"] = f"search_error:{exc}"
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        same_account: list[dict[str, str]] = []
        other_account: list[dict[str, str]] = []
        unvalidated: list[dict[str, str]] = []
        local_identities = {row.get("canonical_url") or "" for row in items}

        for item in search_results:
            candidate = {
                "title": item.get("title") or "",
                "source": item.get("name") or item.get("account") or item.get("author") or "",
                "date": item.get("date") or "",
                "url": item.get("url") or "",
                "normalized_url": archive_module.normalize_article_url(item.get("url") or ""),
                "query": "search-result",
            }
            try:
                validated = await archive_module.validate_candidate(
                    candidate,
                    args.account_name,
                    args.author_hint or args.account_name,
                    True,
                    allow_headful_fallback=False,
                )
            except Exception:
                validated = None
            if not validated:
                unvalidated.append({"title": candidate["title"], "url": candidate["url"]})
                continue

            row, details = validated
            payload = {
                "title": details["meta"].title if details.get("meta") else row.get("title"),
                "date": details["meta"].publish_time if details.get("meta") else row.get("date"),
                "canonical_url": row.get("normalized_url") or row.get("url"),
                "actual_account": details.get("actual_account"),
                "account_id": details.get("account_id"),
            }
            if archive_module.authority_allows_candidate(row, details, locked_ids, locked_bizs, locked_names):
                same_account.append(payload)
            else:
                other_account.append(payload)

        same_recent = []
        for row in same_account:
            published = parse_date(str(row.get("date") or ""))
            if published and published >= cutoff and (row.get("canonical_url") or "") not in local_identities:
                same_recent.append(row)

        result.update(
            {
                "live_search_status": "ok",
                "raw_search_count": len(search_results),
                "validated_same_account_count": len(same_account),
                "validated_other_account_count": len(other_account),
                "unvalidated_count": len(unvalidated),
                "validated_same_account_items": same_account,
                "live_recent_missing_local": same_recent,
            }
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
