from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_title(title: str) -> str:
    cleaned = re.sub(r"\s+", "", title or "")
    cleaned = cleaned.replace("：", ":").replace("“", "\"").replace("”", "\"").replace("？", "?")
    return cleaned.lower()


def date_key(value: str) -> str:
    raw = value or ""
    match = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})", raw)
    if match:
        return "".join(match.groups())
    match = re.search(r"(\d{8})", raw)
    return match.group(1) if match else ""


def body_hash(path: str) -> tuple[str, int]:
    markdown = Path(path or "")
    if not markdown.exists():
        return "", 0
    text = markdown.read_text(encoding="utf-8", errors="replace")
    body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S).strip()
    return hashlib.sha256(body.encode("utf-8")).hexdigest(), len(body)


def load_wechat(wechat_root: Path) -> list[dict[str, Any]]:
    rows = load_json(wechat_root / "archive-index.json", [])
    out: list[dict[str, Any]] = []
    for row in rows:
        markdown_path = str(row.get("markdown_path") or "")
        sha, length = body_hash(markdown_path)
        out.append(
            {
                "platform": "wechat",
                "kind": "article",
                "source_id": str(row.get("mid") or ""),
                "title": row.get("title") or "",
                "title_key": normalize_title(row.get("title") or ""),
                "date": row.get("date") or "",
                "date_key": date_key(row.get("date") or ""),
                "url": row.get("canonical_url") or "",
                "series_title": row.get("series_title") or "未分系列",
                "markdown_path": markdown_path,
                "body_sha256": sha,
                "body_length": length,
                "status": "ok" if markdown_path and Path(markdown_path).exists() else "missing_markdown",
            }
        )
    return out


def load_zhihu(zhihu_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta = load_json(zhihu_root / "crawl-metadata.json", {})
    rows = load_json(zhihu_root / "archive-index.json", [])
    out: list[dict[str, Any]] = []
    for row in rows:
        created = row.get("created") or ""
        out.append(
            {
                "platform": "zhihu",
                "kind": row.get("kind") or "",
                "source_id": str(row.get("source_id") or ""),
                "title": row.get("title") or "",
                "title_key": normalize_title(row.get("title") or ""),
                "date": created,
                "date_key": date_key(created),
                "url": row.get("url") or "",
                "series_title": "",
                "markdown_path": row.get("markdown_path") or "",
                "body_sha256": row.get("body_sha256") or "",
                "body_length": int(row.get("body_length") or 0),
                "status": "ok" if row.get("markdown_path") and Path(row.get("markdown_path")).exists() else "missing_markdown",
            }
        )
    return out, meta


def group_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_title_date: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_title_date[(row["title_key"], row["date_key"])].append(row)
        if row["body_sha256"]:
            by_hash[row["body_sha256"]].append(row)

    title_date_matches = []
    for (title_key, key_date), group in by_title_date.items():
        platforms = sorted({row["platform"] for row in group})
        if len(platforms) > 1:
            title_date_matches.append(
                {
                    "title_key": title_key,
                    "date_key": key_date,
                    "platforms": platforms,
                    "items": [row["platform"] + ":" + row["source_id"] for row in group],
                }
            )

    body_hash_matches = []
    for sha, group in by_hash.items():
        platforms = sorted({row["platform"] for row in group})
        if len(platforms) > 1:
            body_hash_matches.append(
                {
                    "body_sha256": sha,
                    "platforms": platforms,
                    "items": [row["platform"] + ":" + row["source_id"] for row in group],
                }
            )

    return {"title_date_matches": title_date_matches, "body_hash_matches": body_hash_matches}


def source_presence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sources = sorted({row.get("platform") or "" for row in rows if row.get("platform")})
    by_title_date: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        title_key = row.get("title_key") or ""
        key_date = row.get("date_key") or ""
        if title_key and key_date:
            group_key = (title_key, key_date)
        else:
            # Records without a comparable title/date stay source-local instead of
            # being falsely grouped with unrelated blank-key records.
            group_key = (f"{row.get('platform')}:{row.get('source_id')}", "")
        by_title_date[group_key].append(row)

    source_only_group_count = {source: 0 for source in sources}
    source_only_item_count = {source: 0 for source in sources}
    groups_by_source_count: dict[str, int] = defaultdict(int)
    multi_source_group_count = 0
    all_current_sources_group_count = 0
    group_count = 0

    for group in by_title_date.values():
        group_count += 1
        platforms = sorted({row.get("platform") or "" for row in group if row.get("platform")})
        groups_by_source_count[str(len(platforms))] += 1
        if len(platforms) > 1:
            multi_source_group_count += 1
        if sources and set(platforms) == set(sources):
            all_current_sources_group_count += 1
        if len(platforms) == 1:
            source = platforms[0]
            source_only_group_count[source] += 1
            source_only_item_count[source] += len(group)

    return {
        "sources": sources,
        "grouping_key": "normalized_title + date_key",
        "group_count": group_count,
        "groups_by_source_count": dict(groups_by_source_count),
        "multi_source_group_count": multi_source_group_count,
        "all_current_sources_group_count": all_current_sources_group_count,
        "source_only_group_count": source_only_group_count,
        "source_only_item_count": source_only_item_count,
        "neither_status": "not_applicable_without_reference_catalog",
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "platform",
        "kind",
        "source_id",
        "title",
        "date",
        "url",
        "series_title",
        "markdown_path",
        "body_sha256",
        "body_length",
        "status",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def write_markdown(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    presence = summary.get("source_presence") or {}
    source_only = presence.get("source_only_group_count") or {}
    lines = [
        "---",
        "producer: codex",
        "producer_role: controller",
        "producer_evidence: qingbian_unified_index.py",
        "review_owner: codex-controller",
        "review_state: reviewed",
        "canonical_status: record",
        "---",
        "",
        "# 请辩多来源统一索引",
        "",
        f"- 生成时间：{summary['generated_at']}",
        f"- 微信条目：{summary['counts'].get('wechat', 0)}",
        f"- 知乎条目：{summary['counts'].get('zhihu', 0)}",
        f"- 微信状态：{summary['source_status'].get('wechat')}",
        f"- 知乎状态：{summary['source_status'].get('zhihu')}",
        f"- 缺失正文：{summary['missing_markdown_count']}",
        f"- 标题日期跨平台匹配：{len(summary['cross_checks']['title_date_matches'])}",
        f"- 正文哈希跨平台匹配：{len(summary['cross_checks']['body_hash_matches'])}",
        f"- 统一分组总数：{presence.get('group_count', 0)}",
        f"- 当前全部来源均出现的分组：{presence.get('all_current_sources_group_count', 0)}",
        f"- 仅微信分组：{source_only.get('wechat', 0)}",
        f"- 仅知乎分组：{source_only.get('zhihu', 0)}",
        f"- neither：{presence.get('neither_status', 'not_applicable_without_reference_catalog')}",
        "",
        "## 用途",
        "",
        "这份索引用于把请辩微信公众号与知乎账号材料放到同一个外部来源对账面里，支持后续按标题、日期、正文哈希、平台路径判断重复、缺失和跨平台补完。",
        "",
        "## 跨来源覆盖说明",
        "",
        "- `当前全部来源均出现的分组`：按标准化标题和日期分组后，当前已接入的全部来源都出现过同一组。",
        "- `仅微信分组` / `仅知乎分组`：当前只在单一来源出现的分组，供后续补抓或差异分析使用。",
        "- `neither`：当前没有第三方权威目录作为全集，所以无法定义“两边都没有”的文章；需要未来接入外部 reference catalog 后才成立。",
        "",
        "## 最近条目样例",
        "",
    ]
    for row in sorted(rows, key=lambda item: (item.get("date_key") or "", item.get("platform") or ""), reverse=True)[:20]:
        lines.append(f"- {row['platform']} | {row['date']} | {row['title']} | {row['status']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Qingbian cross-platform source index.")
    parser.add_argument("--wechat-root", default="")
    parser.add_argument("--zhihu-root", default="")
    parser.add_argument("--output-root", default="")
    parser.add_argument("--repo-root", default="")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parents[1]
    account_name = "\u8bf7\u8fa9"
    wechat_root = Path(args.wechat_root).resolve() if args.wechat_root else repo_root / "ingestion" / "10-Raw" / "WeChat" / account_name
    zhihu_root = Path(args.zhihu_root).resolve() if args.zhihu_root else repo_root / "ingestion" / "10-Raw" / "Zhihu" / account_name
    output_root = Path(args.output_root).resolve() if args.output_root else repo_root / "ingestion" / "80-Maps" / "Qingbian" / account_name
    wechat_rows = load_wechat(wechat_root)
    zhihu_rows, zhihu_meta = load_zhihu(zhihu_root)
    rows = wechat_rows + zhihu_rows
    counts = defaultdict(int)
    for row in rows:
        counts[row["platform"]] += 1
    source_status = {
        "wechat": "ok" if wechat_rows and not any(row["status"] != "ok" for row in wechat_rows) else "incomplete",
        "zhihu": zhihu_meta.get("status") or ("ok" if zhihu_rows else "not_fetched"),
    }
    missing = [row for row in rows if row["status"] != "ok"]
    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "wechat_root": str(wechat_root),
        "zhihu_root": str(zhihu_root),
        "output_root": str(output_root),
        "counts": dict(counts),
        "source_status": source_status,
        "missing_markdown_count": len(missing),
        "missing_markdown": missing,
        "cross_checks": group_rows(rows),
        "source_presence": source_presence(rows),
    }
    write_json(output_root / "qingbian-unified-index.json", {"summary": summary, "items": rows})
    write_csv(output_root / "qingbian-unified-index.csv", rows)
    write_markdown(output_root / "qingbian-unified-index.md", summary, rows)
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    if args.strict and (source_status["wechat"] != "ok" or missing):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
