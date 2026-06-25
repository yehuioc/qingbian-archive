from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def markdown_count(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(1 for _ in root.rglob("*.md"))


def build_status(repo_root: Path) -> dict[str, Any]:
    account_name = "\u8bf7\u8fa9"
    wechat_root = repo_root / "ingestion" / "10-Raw" / "WeChat" / account_name
    zhihu_root = repo_root / "ingestion" / "10-Raw" / "Zhihu" / account_name
    corpus_root = repo_root / "ingestion" / "10-Raw" / "Qingbian" / account_name
    unified_root = repo_root / "ingestion" / "80-Maps" / "Qingbian" / account_name

    unified = load_json(unified_root / "qingbian-unified-index.json", {})
    corpus = load_json(corpus_root / "archive-index.json", {})
    summary = unified.get("summary") or {}
    zhihu_meta = load_json(zhihu_root / "crawl-metadata.json", {})
    wechat_rows = load_json(wechat_root / "archive-index.json", [])
    zhihu_rows = load_json(zhihu_root / "archive-index.json", [])

    return {
        "account": account_name,
        "paths": {
            "wechat_root": str(wechat_root),
            "zhihu_root": str(zhihu_root),
            "corpus_root": str(corpus_root),
            "unified_root": str(unified_root),
        },
        "source_status": summary.get("source_status") or {},
        "counts": {
            "wechat_index": len(wechat_rows) if isinstance(wechat_rows, list) else 0,
            "wechat_markdown": markdown_count(wechat_root),
            "zhihu_index": len(zhihu_rows) if isinstance(zhihu_rows, list) else 0,
            "zhihu_markdown": markdown_count(zhihu_root / "items"),
            "unified_items": len(unified.get("items") or []),
            "corpus_index": len(corpus.get("items") or []),
            "corpus_markdown": markdown_count(corpus_root / "items"),
        },
        "corpus": {
            "generated_at": corpus.get("generated_at") or "",
            "source_item_count": corpus.get("source_item_count") or 0,
            "materialized_count": corpus.get("materialized_count") or 0,
            "missing_source_markdown_count": corpus.get("missing_source_markdown_count") or 0,
        },
        "zhihu": {
            "status": zhihu_meta.get("status") or "unknown",
            "fetch_mode": zhihu_meta.get("fetch_mode") or "",
            "fetched_at": zhihu_meta.get("fetched_at") or "",
            "failed_at": zhihu_meta.get("failed_at") or "",
            "failure_reason": zhihu_meta.get("failure_reason") or "",
            "last_success_fetched_at": zhihu_meta.get("last_success_fetched_at") or "",
            "last_success_record_count": zhihu_meta.get("last_success_record_count") or 0,
            "record_count": zhihu_meta.get("record_count") or 0,
            "counts": zhihu_meta.get("counts") or {},
            "browser_stats": zhihu_meta.get("browser_stats") or {},
        },
        "cross_source": {
            "missing_markdown_count": summary.get("missing_markdown_count"),
            "title_date_matches": len((summary.get("cross_checks") or {}).get("title_date_matches") or []),
            "body_hash_matches": len((summary.get("cross_checks") or {}).get("body_hash_matches") or []),
            "source_presence": summary.get("source_presence") or {},
            "generated_at": summary.get("generated_at") or "",
        },
    }


def format_markdown(status: dict[str, Any]) -> str:
    counts = status["counts"]
    cross = status["cross_source"]
    corpus = status["corpus"]
    presence = cross.get("source_presence") or {}
    source_only = presence.get("source_only_group_count") or {}
    zhihu = status["zhihu"]
    lines = [
        "# Qingbian Multi-Source Archive Status",
        "",
        "## Local Counts",
        "",
        f"- WeChat index/markdown: {counts['wechat_index']} / {counts['wechat_markdown']}",
        f"- Zhihu index/markdown: {counts['zhihu_index']} / {counts['zhihu_markdown']}",
        f"- Unified items: {counts['unified_items']}",
        f"- Unified corpus index/markdown: {counts['corpus_index']} / {counts['corpus_markdown']}",
        "",
        "## Source Status",
        "",
        f"- WeChat status: {status['source_status'].get('wechat', 'unknown')}",
        f"- Zhihu status: {status['source_status'].get('zhihu', zhihu.get('status', 'unknown'))}",
        f"- Zhihu fetch mode: {zhihu.get('fetch_mode', '')}",
        f"- Zhihu fetched at: {zhihu.get('fetched_at', '')}",
    ]
    if zhihu.get("status") != "success":
        lines.extend(
            [
                f"- Zhihu failed at: {zhihu.get('failed_at', '')}",
                f"- Zhihu last success: {zhihu.get('last_success_fetched_at', '')}",
                f"- Zhihu last success records: {zhihu.get('last_success_record_count', 0)}",
                f"- Zhihu failure reason: {zhihu.get('failure_reason', '')[:240]}",
            ]
        )
    lines += [
        "",
        "## Cross-Source Reconciliation",
        "",
        f"- Group count: {presence.get('group_count', 0)}",
        f"- Multi-source groups: {presence.get('multi_source_group_count', 0)}",
        f"- All-current-source groups: {presence.get('all_current_sources_group_count', 0)}",
        f"- WeChat-only groups: {source_only.get('wechat', 0)}",
        f"- Zhihu-only groups: {source_only.get('zhihu', 0)}",
        f"- Neither: {presence.get('neither_status', 'not_applicable_without_reference_catalog')}",
        f"- Title/date matches: {cross.get('title_date_matches', 0)}",
        f"- Body-hash matches: {cross.get('body_hash_matches', 0)}",
        f"- Missing markdown: {cross.get('missing_markdown_count', 'unknown')}",
        f"- Unified index generated at: {cross.get('generated_at', '')}",
        f"- Unified corpus generated at: {corpus.get('generated_at', '')}",
        f"- Unified corpus missing source markdown: {corpus.get('missing_source_markdown_count', 'unknown')}",
        "",
        "## Zhihu Pagination Completeness",
        "",
    ]
    browser_stats = zhihu.get("browser_stats") or {}
    if browser_stats:
        for kind, stat in browser_stats.items():
            lines.append(
                f"- {kind}: visible={stat.get('visible_count')}, reported={stat.get('reported_total')}, "
                f"hidden_or_unavailable={stat.get('hidden_or_unavailable_count')}, "
                f"pages={stat.get('pages')}, last_is_end={stat.get('last_is_end')}"
            )
    else:
        lines.append("- No browser pagination stats.")
    lines += [
        "",
        "## Paths",
        "",
        f"- WeChat: `{json.dumps(status['paths']['wechat_root'], ensure_ascii=True)}`",
        f"- Zhihu: `{json.dumps(status['paths']['zhihu_root'], ensure_ascii=True)}`",
        f"- Unified corpus: `{json.dumps(status['paths']['corpus_root'], ensure_ascii=True)}`",
        f"- Unified index: `{json.dumps(status['paths']['unified_root'], ensure_ascii=True)}`",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Show Qingbian multi-source archive status.")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    args = parser.parse_args()

    status = build_status(Path(args.repo_root).resolve())
    if args.format == "json":
        print(json.dumps(status, ensure_ascii=True, indent=2))
    else:
        print(format_markdown(status), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
