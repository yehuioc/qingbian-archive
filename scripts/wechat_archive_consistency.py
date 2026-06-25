from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


MID_IN_PATH_RE = re.compile(r"-(\d{10})-")


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_path(raw_path: str, archive_root: Path) -> Path:
    path = Path(raw_path or "")
    if path.is_absolute():
        return path
    return archive_root / path


def find_markdown_by_mid(root: Path) -> dict[str, list[Path]]:
    matches: dict[str, list[Path]] = {}
    if not root.exists():
        return matches
    for path in root.rglob("*.md"):
        mids = MID_IN_PATH_RE.findall(str(path))
        if not mids:
            continue
        matches.setdefault(mids[-1], []).append(path)
    return matches


def merge_mid_maps(target: dict[str, list[Path]], source: dict[str, list[Path]]) -> None:
    for mid, paths in source.items():
        bucket = target.setdefault(mid, [])
        for path in paths:
            if path not in bucket:
                bucket.append(path)


def copy_markdown_tree(source_markdown: Path, desired_markdown: Path) -> None:
    desired_dir = desired_markdown.parent
    source_dir = source_markdown.parent
    desired_dir.parent.mkdir(parents=True, exist_ok=True)
    if desired_dir.exists():
        desired_dir.mkdir(parents=True, exist_ok=True)
    elif source_dir.exists():
        shutil.copytree(source_dir, desired_dir)
    else:
        desired_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_markdown, desired_markdown)
    if not desired_markdown.exists() and source_markdown.exists():
        shutil.copy2(source_markdown, desired_markdown)


def write_csv(index_path: Path, rows: list[dict[str, Any]]) -> None:
    csv_path = index_path.with_suffix(".csv")
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def audit_rows(archive_root: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    existing_indexed: set[str] = set()
    missing: list[dict[str, Any]] = []
    duplicate_paths: dict[str, int] = {}
    for index, row in enumerate(rows):
        markdown = resolve_path(str(row.get("markdown_path") or ""), archive_root)
        markdown_key = str(markdown)
        duplicate_paths[markdown_key] = duplicate_paths.get(markdown_key, 0) + 1
        if markdown.exists():
            existing_indexed.add(markdown_key)
        else:
            missing.append(
                {
                    "index": index,
                    "mid": str(row.get("mid") or ""),
                    "title": row.get("title") or "",
                    "canonical_url": row.get("canonical_url") or "",
                    "expected_markdown_path": markdown_key,
                }
            )
    markdown_files = [str(path) for path in archive_root.rglob("*.md")]
    extras = sorted(path for path in markdown_files if path not in existing_indexed)
    duplicate_path_count = sum(1 for count in duplicate_paths.values() if count > 1)
    return {
        "index_rows": len(rows),
        "existing_indexed_markdown": len(existing_indexed),
        "missing_markdown_count": len(missing),
        "missing_markdown": missing,
        "markdown_file_count": len(markdown_files),
        "extra_markdown_not_indexed_count": len(extras),
        "extra_markdown_not_indexed": extras,
        "duplicate_index_path_count": duplicate_path_count,
    }


def repair_from_local_sources(
    archive_root: Path,
    rows: list[dict[str, Any]],
    repair_roots: list[Path],
) -> dict[str, Any]:
    archive_mid_map = find_markdown_by_mid(archive_root)
    source_mid_map: dict[str, list[Path]] = {}
    for root in repair_roots:
        merge_mid_maps(source_mid_map, find_markdown_by_mid(root))

    repaired_existing = 0
    repaired_copied = 0
    ambiguous = []
    for row in rows:
        desired = resolve_path(str(row.get("markdown_path") or ""), archive_root)
        if desired.exists():
            continue
        mid = str(row.get("mid") or "")
        if not mid:
            continue
        archive_matches = [path for path in archive_mid_map.get(mid, []) if path.exists()]
        source_matches = [path for path in source_mid_map.get(mid, []) if path.exists()]
        candidates = archive_matches + [path for path in source_matches if path not in archive_matches]
        if len(candidates) > 1:
            title = str(row.get("title") or "")
            narrowed = [path for path in candidates if title and title in str(path)]
            if len(narrowed) == 1:
                candidates = narrowed
        if len(candidates) != 1:
            if candidates:
                ambiguous.append({"mid": mid, "title": row.get("title") or "", "candidate_count": len(candidates)})
            continue
        source = candidates[0]
        if source.is_relative_to(archive_root):
            row["article_dir"] = str(source.parent)
            row["markdown_path"] = str(source)
            repaired_existing += 1
        else:
            copy_markdown_tree(source, desired)
            if desired.exists():
                row["article_dir"] = str(desired.parent)
                row["markdown_path"] = str(desired)
                repaired_copied += 1
    return {
        "repaired_existing_path_count": repaired_existing,
        "repaired_copied_count": repaired_copied,
        "ambiguous_local_match_count": len(ambiguous),
        "ambiguous_local_matches": ambiguous,
    }


def archive_extra_markdown(archive_root: Path, rows: list[dict[str, Any]], archive_extra_root: Path) -> dict[str, Any]:
    audit = audit_rows(archive_root, rows)
    moved = []
    failed = []
    indexed_dirs = {
        str(resolve_path(str(row.get("article_dir") or ""), archive_root))
        for row in rows
        if row.get("article_dir")
    }
    for raw_path in audit["extra_markdown_not_indexed"]:
        markdown = Path(raw_path)
        article_dir = markdown.parent
        if str(article_dir) in indexed_dirs:
            failed.append({"path": raw_path, "error": "extra file lives in indexed article_dir"})
            continue
        try:
            rel = article_dir.relative_to(archive_root)
        except ValueError:
            rel = Path(article_dir.name)
        target_dir = archive_extra_root / rel
        counter = 1
        original_target_dir = target_dir
        while target_dir.exists():
            counter += 1
            target_dir = original_target_dir.with_name(f"{original_target_dir.name}__dup{counter}")
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(article_dir), str(target_dir))
            moved.append({"from": str(article_dir), "to": str(target_dir)})
        except Exception as exc:
            failed.append({"path": raw_path, "error": str(exc)})
    return {"archived_extra_count": len(moved), "archive_extra_failed_count": len(failed), "archived_extra": moved, "archive_extra_failed": failed}


def refetch_missing(
    archive_root: Path,
    rows: list[dict[str, Any]],
    account_name: str,
    author_hint: str,
    runtime_home: Path,
    repo_root: Path,
    max_refetch: int,
) -> dict[str, Any]:
    missing = audit_rows(archive_root, rows)["missing_markdown"]
    fetched = []
    failed = []
    script = repo_root / "scripts" / "wechat_account_archive.py"
    for item in missing:
        if max_refetch and len(fetched) + len(failed) >= max_refetch:
            break
        url = item.get("canonical_url") or ""
        if not url:
            failed.append({**item, "error": "missing canonical_url"})
            continue
        cmd = [
            sys.executable,
            str(script),
            "--repo-root",
            str(repo_root),
            "--runtime-home",
            str(runtime_home),
            "--account-name",
            account_name,
            "--author-hint",
            author_hint,
            "--seed-url",
            url,
            "--bootstrap-archive-root",
            str(archive_root),
            "--output-root",
            str(archive_root),
            "--search-pages",
            "0",
            "--search-limit",
            "0",
            "--no-search",
        ]
        try:
            result = subprocess.run(cmd, text=True, encoding="utf-8", capture_output=True, timeout=180)
        except subprocess.TimeoutExpired as exc:
            failed.append(
                {
                    "mid": item.get("mid"),
                    "title": item.get("title"),
                    "returncode": "timeout",
                    "stderr_tail": (exc.stderr or "")[-500:] if isinstance(exc.stderr, str) else "",
                    "stdout_tail": (exc.stdout or "")[-500:] if isinstance(exc.stdout, str) else "",
                }
            )
            continue
        if result.returncode == 0:
            fetched.append({"mid": item.get("mid"), "title": item.get("title"), "returncode": result.returncode})
        else:
            failed.append(
                {
                    "mid": item.get("mid"),
                    "title": item.get("title"),
                    "returncode": result.returncode,
                    "stderr_tail": result.stderr[-500:],
                    "stdout_tail": result.stdout[-500:],
                }
            )
    refreshed_rows = load_json(archive_root / "archive-index.json", rows)
    rows.clear()
    rows.extend(refreshed_rows)
    return {"refetched_count": len(fetched), "refetch_failed_count": len(failed), "refetch_failed": failed}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit and repair WeChat archive index-to-Markdown consistency.")
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--author-hint", default="")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--runtime-home", default="")
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--refetch-missing", action="store_true")
    parser.add_argument("--archive-extra", action="store_true")
    parser.add_argument("--archive-extra-root", default="")
    parser.add_argument("--repair-root", action="append", default=[])
    parser.add_argument("--max-refetch", type=int, default=0)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    archive_root = Path(args.archive_root).resolve()
    repo_root = Path(args.repo_root).resolve()
    runtime_home = Path(args.runtime_home or (repo_root / ".runtime" / "agent-reach" / "home")).resolve()
    index_path = archive_root / "archive-index.json"
    rows: list[dict[str, Any]] = load_json(index_path, [])
    if not rows:
        raise FileNotFoundError(f"No archive index rows found: {index_path}")

    before = audit_rows(archive_root, rows)
    repair_result = {
        "repaired_existing_path_count": 0,
        "repaired_copied_count": 0,
        "ambiguous_local_match_count": 0,
        "ambiguous_local_matches": [],
    }
    refetch_result = {"refetched_count": 0, "refetch_failed_count": 0, "refetch_failed": []}
    archive_extra_result = {"archived_extra_count": 0, "archive_extra_failed_count": 0, "archived_extra": [], "archive_extra_failed": []}

    if args.repair:
        repair_roots = [Path(path).resolve() for path in args.repair_root]
        repair_result = repair_from_local_sources(archive_root, rows, repair_roots)
        write_json(index_path, rows)
        write_csv(index_path, rows)

    if args.refetch_missing:
        refetch_result = refetch_missing(
            archive_root,
            rows,
            args.account_name,
            args.author_hint or args.account_name,
            runtime_home,
            repo_root,
            args.max_refetch,
        )

    if args.archive_extra:
        extra_root = Path(args.archive_extra_root) if args.archive_extra_root else (
            repo_root
            / "ingestion"
            / "99-Archive"
            / "WeChat"
            / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-qingbian-unindexed-markdown"
        )
        archive_extra_result = archive_extra_markdown(archive_root, rows, extra_root.resolve())

    after = audit_rows(archive_root, rows)
    payload = {
        "archive_root": str(archive_root),
        "before": before,
        "repair": repair_result,
        "refetch": refetch_result,
        "archive_extra": archive_extra_result,
        "after": after,
    }
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    if args.strict and (
        after["missing_markdown_count"]
        or after["extra_markdown_not_indexed_count"]
        or after["duplicate_index_path_count"]
        or refetch_result["refetch_failed_count"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
