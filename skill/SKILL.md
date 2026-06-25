---
name: qingbian-multisource-archive
description: Maintain and verify the 请辩/蔡垒磊 account-level multi-source corpus across WeChat Official Account and Zhihu. Use when the user asks for 请辩 cross-source archive, unified account folder, unified index, WeChat-Zhihu dedupe, cross-validation, missing-article repair, or stable scripts/skills for this account. Runs WeChat known-album maintenance, Zhihu cookie-gated archive, unified index generation, and unified corpus materialization under ingestion.
---

# Qingbian Multi-Source Archive

Use this as the account-specific entry for 请辩 / 蔡垒磊 cross-platform source work.

## Boundary

All fetched material and indexes stay in `ingestion/`.

This skill does not promote source-derived content into personal `memory/`. Promotion requires a separate reviewed source-loop or memory-pipeline decision.

## Cross-Agent Output Contract

When this skill creates or modifies durable output, follow `docs/20260606-2130-N-cross-agent-skill-output-contract-standard.md`.

Semantic targets:

- source-derived raw material belongs in `ingestion/10-Raw/`
- unified source indexes belong in `ingestion/80-Maps/`
- unified account-level corpus belongs in `ingestion/10-Raw/Qingbian/请辩/`
- automation execution reports belong in `runs/`
- operational scripts belong in `{project_root}/scripts/`
- this skill's canonical instructions belong in `{project_root}/skill/`

Producer metadata:

- Codex controller edits to docs, skills, or reports must use `producer: codex`, `producer_role: controller`, `review_state: reviewed`, and `canonical_status: formal` or `record` as appropriate.
- Automation runner reports must use `producer: openclaw`, `producer_role: automation`, `review_owner: codex-controller`, `review_state: reviewed`, and `canonical_status: record`.
- Claude Code or another worker must not claim promotion, memory landing, or canonical completion. It must hand off evidence for Codex review when changing tracked scripts, skills, docs, or task configuration.

## Canonical Command

Use the dedicated wrapper:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action doctor
```

Full maintenance:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action run
```

Full maintenance with a user-supplied Zhihu cookie export:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action run -ZhihuCookieFile <path-to-zhihu-cookie-json-or-header-file>
```

Status dashboard:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action status
```

Status plus Windows scheduled-task state:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action progress
```

Publish-time analysis and schedule recommendation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action publish-time
```

Build only the unified index:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action build-index
```

Build only the unified account-level corpus from the existing unified index:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action build-corpus
```

Audit:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action audit
```

Register or update the daily multi-source Windows Scheduled Task:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action register-task -ZhihuCookieFile <path-to-zhihu-cookie-json-or-header-file>
```

Manual task control:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action start-task
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action stop-task
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action task-info
```

The default task name is `agentv2-qingbian-multisource-daily-archive`.
The default task time is `15:40`, chosen from local Qingbian publish-time
evidence: recent Zhihu posts are the multi-source bottleneck, and the task runs
about one hour after the recent Zhihu mean publish time.

## What The Wrapper Does

1. Runs the 请辩 WeChat known-album repair path:
   - `scripts/wechat-qingbian-album-archive.ps1 -Action repair`
2. Runs Zhihu archive if credentials exist:
   - `scripts/zhihu_account_archive.py --action run --user-token qingbian`
3. Builds the unified cross-platform index:
   - `scripts/qingbian_unified_index.py`
4. Materializes the unified account-level corpus:
   - `scripts/qingbian_unified_corpus.py`
5. For scheduled execution, runs through:
   - `scripts/openclaw-job-qingbian-multisource.ps1`
6. For foreground status, reads:
   - `scripts/qingbian_status.py`
7. For schedule timing evidence, reads:
   - `scripts/qingbian_publish_time.py`

The wrapper must not pass Chinese account paths from PowerShell into Python
when the Python script can construct the default `请辩` paths internally.
This prevents native argv mojibake under Windows PowerShell, scheduled tasks,
or sandboxed shells. Python-side persisted files remain UTF-8.

Zhihu list APIs may reject direct signed requests with `10003`. In default
`auto` mode, `zhihu_account_archive.py` falls back to a temporary local
Chrome/Edge Playwright context using the supplied cookie file. Completion should
be judged by `crawl-metadata.json.status`, `browser_stats.*.last_is_end`, local
index counts, and missing Markdown checks, not by profile headline counts alone.

## Storage

WeChat corpus:

```text
E:\agentv2\ingestion\10-Raw\WeChat\请辩
```

Zhihu corpus:

```text
E:\agentv2\ingestion\10-Raw\Zhihu\请辩
```

Unified account-level corpus:

```text
E:\agentv2\ingestion\10-Raw\Qingbian\请辩
```

The unified corpus keeps all materialized Markdown files in one `items/`
folder instead of platform subfolders. Per-note frontmatter carries source
identity through `source_platform`, `source_kind`, `source_id`, `source_url`,
and `origin_markdown_path`.

Unified index:

```text
E:\agentv2\ingestion\80-Maps\Qingbian\请辩
```

Automation reports:

```text
E:\agentv2\runs\*qingbian-multisource-cycle*
```

Archive residue:

```text
E:\agentv2\ingestion\99-Archive\WeChat\<timestamp>-qingbian-unindexed-markdown
```

## Quality Rules

- WeChat account archive must use known album IDs through `wechat-qingbian-album-archive`; do not rediscover 请辩 through generic search.
- Zhihu account archive requires credentials. Missing credentials are `credential_required`, not success.
- Unified index must distinguish source status for each platform.
- Unified index must expose source coverage groups, including single-source groups, multi-source groups, and the fact that `neither` is not meaningful without an external reference catalog.
- Unified corpus must materialize every unified index item into `ingestion/10-Raw/Qingbian/请辩/items/`, with source identity in Markdown frontmatter rather than platform-specific directories.
- A complete WeChat side requires `index_rows == markdown_count`, `missing_markdown_count == 0`, and `extra_markdown_not_indexed_count == 0`.
- Cross-source matches are evidence candidates; they do not prove semantic equivalence unless title/date or body hash evidence supports it.
- Scheduled-task success is checked through the wrapper's `progress` or `task-info` actions plus the generated `runs/*qingbian-multisource-cycle*` report, not by looking at an empty PowerShell window.
- Scheduled-task runner output must remain small. The job runner writes full stdout/stderr/report files to `runs/` and prints only a one-line pointer so Windows Task Scheduler does not keep a finished task in `Running`.
- Scheduled-task execution has two hard stops: the child maintenance process is terminated after 25 minutes, and the Windows task has a 30-minute execution limit.
