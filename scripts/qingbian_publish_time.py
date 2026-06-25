from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def minute_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def format_hhmm(minutes: int | float) -> str:
    value = int(round(minutes)) % (24 * 60)
    return f"{value // 60:02d}:{value % 60:02d}"


def round_up(minutes: int | float, step: int = 10) -> int:
    value = int(round(minutes))
    return ((value + step - 1) // step) * step


def summarize(items: list[dict[str, Any]], windows: tuple[int, ...] = (30, 90, 180)) -> dict[str, Any]:
    items = sorted(items, key=lambda item: item["datetime"], reverse=True)
    summaries: dict[str, Any] = {}
    for window in windows:
        subset = items[:window]
        if not subset:
            continue
        mins = [minute_of_day(item["datetime"]) for item in subset]
        buckets: dict[str, int] = {}
        for value in mins:
            buckets[f"{value // 60:02d}:00"] = buckets.get(f"{value // 60:02d}:00", 0) + 1
        summaries[f"recent_{window}"] = {
            "count": len(subset),
            "mean_time": format_hhmm(statistics.mean(mins)),
            "median_time": format_hhmm(statistics.median(mins)),
            "top_hours": sorted(buckets.items(), key=lambda kv: (-kv[1], kv[0]))[:5],
            "newest_date": subset[0]["datetime"].strftime("%Y-%m-%d"),
            "oldest_date": subset[-1]["datetime"].strftime("%Y-%m-%d"),
        }
    if items:
        mins = [minute_of_day(item["datetime"]) for item in items]
        summaries["all"] = {
            "count": len(items),
            "mean_time": format_hhmm(statistics.mean(mins)),
            "median_time": format_hhmm(statistics.median(mins)),
        }
    return summaries


def collect(repo_root: Path) -> dict[str, list[dict[str, Any]]]:
    account = "\u8bf7\u8fa9"
    wechat_rows = load_json(repo_root / "ingestion" / "10-Raw" / "WeChat" / account / "archive-index.json")
    zhihu_rows = load_json(repo_root / "ingestion" / "10-Raw" / "Zhihu" / account / "archive-index.json")
    collected: dict[str, list[dict[str, Any]]] = {"wechat": [], "zhihu": []}
    for row in wechat_rows if isinstance(wechat_rows, list) else []:
        dt = parse_datetime(row.get("date"))
        if dt and (dt.hour or dt.minute):
            collected["wechat"].append({"datetime": dt, "title": row.get("title") or ""})
    for row in zhihu_rows if isinstance(zhihu_rows, list) else []:
        dt = parse_datetime(row.get("created"))
        if dt and (dt.hour or dt.minute):
            collected["zhihu"].append({"datetime": dt, "title": row.get("title") or "", "kind": row.get("kind") or ""})
    return collected


def build_report(repo_root: Path) -> dict[str, Any]:
    collected = collect(repo_root)
    summaries = {source: summarize(items) for source, items in collected.items()}
    zhihu_recent = summaries.get("zhihu", {}).get("recent_30", {})
    wechat_recent = summaries.get("wechat", {}).get("recent_30", {})
    source_basis = "zhihu_recent_30_mean"
    if zhihu_recent.get("mean_time"):
        hour, minute = [int(part) for part in zhihu_recent["mean_time"].split(":")]
        basis_minutes = hour * 60 + minute
    elif wechat_recent.get("mean_time"):
        source_basis = "wechat_recent_30_mean"
        hour, minute = [int(part) for part in wechat_recent["mean_time"].split(":")]
        basis_minutes = hour * 60 + minute
    else:
        source_basis = "fallback"
        basis_minutes = 14 * 60
    recommended_minutes = round_up(basis_minutes + 60, 10)
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "policy": "Use the latest stable multi-source bottleneck. For Qingbian, Zhihu usually lags WeChat, so schedule one hour after the recent Zhihu mean and round up to 10 minutes.",
        "recommendation_basis": source_basis,
        "recommended_task_time": format_hhmm(recommended_minutes),
        "sources": summaries,
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Qingbian Publish Time Analysis",
        "",
        f"- Generated at: {payload['generated_at']}",
        f"- Recommended task time: {payload['recommended_task_time']}",
        f"- Basis: {payload['recommendation_basis']}",
        f"- Policy: {payload['policy']}",
        "",
        "## Source summaries",
        "",
    ]
    for source, summary in payload["sources"].items():
        lines += [f"### {source}", ""]
        for key in ("recent_30", "recent_90", "recent_180", "all"):
            if key not in summary:
                continue
            item = summary[key]
            lines.append(
                f"- {key}: count={item.get('count')}, mean={item.get('mean_time')}, "
                f"median={item.get('median_time')}, range={item.get('oldest_date', '')}..{item.get('newest_date', '')}"
            )
            if item.get("top_hours"):
                lines.append(f"  - top_hours={item['top_hours']}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Infer Qingbian publish time and recommended archive schedule.")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[4]))
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    args = parser.parse_args()
    payload = build_report(Path(args.repo_root).resolve())
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    else:
        print(markdown(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
