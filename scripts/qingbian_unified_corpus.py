from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


ACCOUNT_NAME = "\u8bf7\u8fa9"


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_filename(value: str, fallback: str = "untitled", limit: int = 80) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value or "").strip()
    text = re.sub(r"\s+", " ", text).strip(" .")
    text = text[:limit].strip(" .")
    return text or fallback


def date_prefix(value: str) -> str:
    text = value or ""
    match = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})", text)
    if match:
        return "".join(match.groups())
    match = re.search(r"(\d{8})", text)
    return match.group(1) if match else "unknown-date"


def strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            return text[end + 5 :].lstrip()
    return text.lstrip()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def quote_yaml(value: Any) -> str:
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def build_unified_markdown(row: dict[str, Any], source_text: str, source_path: Path, generated_at: str) -> tuple[str, str, int]:
    body = strip_frontmatter(source_text).strip()
    body_sha = sha256_text(body)
    frontmatter = [
        "---",
        "account: 请辩",
        f"title: {quote_yaml(row.get('title') or '')}",
        f"author: {quote_yaml('请辩')}",
        f"date: {quote_yaml(row.get('date') or '')}",
        f"source_platform: {quote_yaml(row.get('platform') or '')}",
        f"source_kind: {quote_yaml(row.get('kind') or '')}",
        f"source_id: {quote_yaml(row.get('source_id') or '')}",
        f"source_url: {quote_yaml(row.get('url') or '')}",
        f"source_series_title: {quote_yaml(row.get('series_title') or '')}",
        f"origin_markdown_path: {quote_yaml(str(source_path))}",
        f"origin_body_sha256: {quote_yaml(row.get('body_sha256') or '')}",
        f"unified_body_sha256: {quote_yaml(body_sha)}",
        f"unified_generated_at: {quote_yaml(generated_at)}",
        "canonical_status: record",
        "review_state: reviewed",
        "---",
        "",
    ]
    return "\n".join(frontmatter) + body + "\n", body_sha, len(body)


def materialize_items(items: list[dict[str, Any]], output_root: Path, clear: bool) -> dict[str, Any]:
    items_dir = output_root / "items"
    if clear and items_dir.exists():
        shutil.rmtree(items_dir)
    items_dir.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    collisions: dict[str, int] = {}

    for item in items:
        source_path = Path(str(item.get("markdown_path") or ""))
        if not source_path.exists():
            missing.append(
                {
                    "source_platform": item.get("platform") or "",
                    "source_id": item.get("source_id") or "",
                    "markdown_path": str(source_path),
                }
            )
            continue
        source_text = source_path.read_text(encoding="utf-8", errors="replace")
        content, body_sha, body_length = build_unified_markdown(item, source_text, source_path, generated_at)
        title = safe_filename(item.get("title") or "", str(item.get("source_id") or "untitled"))
        identity = f"{item.get('platform')}:{item.get('kind')}:{item.get('source_id')}:{item.get('url')}"
        suffix = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:10]
        filename = f"{date_prefix(item.get('date') or '')}-{title}-{suffix}.md"
        if filename in collisions:
            collisions[filename] += 1
            filename = f"{date_prefix(item.get('date') or '')}-{title}-{suffix}-{collisions[filename]}.md"
        else:
            collisions[filename] = 1
        target = items_dir / filename
        target.write_text(content, encoding="utf-8")
        rows.append(
            {
                "account": ACCOUNT_NAME,
                "title": item.get("title") or "",
                "date": item.get("date") or "",
                "source_platform": item.get("platform") or "",
                "source_kind": item.get("kind") or "",
                "source_id": item.get("source_id") or "",
                "source_url": item.get("url") or "",
                "source_series_title": item.get("series_title") or "",
                "origin_markdown_path": str(source_path),
                "unified_markdown_path": str(target),
                "unified_body_sha256": body_sha,
                "body_length": body_length,
            }
        )
    return {
        "generated_at": generated_at,
        "output_root": str(output_root),
        "items_dir": str(items_dir),
        "source_item_count": len(items),
        "materialized_count": len(rows),
        "missing_source_markdown_count": len(missing),
        "missing_source_markdown": missing,
        "items": rows,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "account",
        "title",
        "date",
        "source_platform",
        "source_kind",
        "source_id",
        "source_url",
        "source_series_title",
        "origin_markdown_path",
        "unified_markdown_path",
        "unified_body_sha256",
        "body_length",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    by_source: dict[str, int] = {}
    for item in summary["items"]:
        source = item.get("source_platform") or "unknown"
        by_source[source] = by_source.get(source, 0) + 1
    lines = [
        "---",
        "producer: codex",
        "producer_role: controller",
        "producer_evidence: qingbian_unified_corpus.py",
        "review_owner: codex-controller",
        "review_state: reviewed",
        "canonical_status: record",
        "---",
        "",
        "# 请辩统一原文库",
        "",
        f"- 生成时间：{summary['generated_at']}",
        f"- 输出根目录：`{summary['output_root']}`",
        f"- 统一正文目录：`{summary['items_dir']}`",
        f"- 来源索引条目：{summary['source_item_count']}",
        f"- 已物化正文：{summary['materialized_count']}",
        f"- 缺失来源正文：{summary['missing_source_markdown_count']}",
        "",
        "## 来源分布",
        "",
    ]
    for source, count in sorted(by_source.items()):
        lines.append(f"- {source}: {count}")
    lines += [
        "",
        "## 说明",
        "",
        "这个目录是请辩账号级统一完成体。微信和知乎原始分库仍保留为平台级原始来源；本目录把所有已入库正文物化到同一个 `items/` 文件夹，来源差异写入每篇 Markdown 的 frontmatter：`source_platform`、`source_kind`、`source_id`、`source_url` 和 `origin_markdown_path`。",
        "",
        "文件名不按平台分层。若标题和日期重复，使用来源身份哈希作为后缀避免覆盖。",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize Qingbian WeChat + Zhihu records into one account-level corpus folder.")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--index-root", default="")
    parser.add_argument("--output-root", default="")
    parser.add_argument("--no-clear", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    index_root = Path(args.index_root).resolve() if args.index_root else repo_root / "ingestion" / "80-Maps" / "Qingbian" / ACCOUNT_NAME
    output_root = Path(args.output_root).resolve() if args.output_root else repo_root / "ingestion" / "10-Raw" / "Qingbian" / ACCOUNT_NAME
    unified = load_json(index_root / "qingbian-unified-index.json", {})
    items = unified.get("items") or []
    summary = materialize_items(items, output_root, clear=not args.no_clear)
    write_json(output_root / "archive-index.json", summary)
    write_csv(output_root / "archive-index.csv", summary["items"])
    write_markdown(output_root / "README.md", summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "items"}, ensure_ascii=True, indent=2))
    if args.strict and summary["missing_source_markdown_count"]:
        return 1
    if args.strict and summary["materialized_count"] != summary["source_item_count"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
