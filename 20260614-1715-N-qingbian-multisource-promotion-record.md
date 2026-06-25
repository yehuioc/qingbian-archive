---
producer: codex
producer_role: controller
producer_evidence: current Codex goal execution 2026-06-14
review_owner: codex-controller
review_state: reviewed
canonical_status: record
---

# 请辩多来源归档 workbench 孵化与 promotion 记录

存储位置：`E:\agentv2\workbench\projects\qingbian-multisource-archive\20260614-1715-N-qingbian-multisource-promotion-record.md`

## 需求

把请辩微信公众号归档修到索引和正文一致，并在此基础上正式安装知乎账号归档 skill，建立请辩跨平台统一索引，用于后续对微信与知乎来源文章做交叉认证、去重、对比、补完和审计。

## 孵化结论

本项目不是把第三方 `floodsung-skill` 直接复制进 runtime，而是吸收其机制：

- Zhihu 账号抓取需要登录 cookie。
- cookie 至少应包含 `d_c0` 和 `z_c0`。
- 直接 API 可能因知乎 `10003` 签名/客户端门禁失败。
- 可用的稳定路径是 direct API 失败后启用本地 Chrome/Edge Playwright 回退，让知乎前端生成有效列表请求。
- 账号材料应区分 articles、answers、pins。

正式落地采用 agentv2 自有脚本和 skill：

- `E:\agentv2\scripts\zhihu_account_archive.py`
- `E:\agentv2\scripts\qingbian_unified_index.py`
- `E:\agentv2\scripts\qingbian-multisource-archive.ps1`
- `E:\agentv2\skills\zhihu-account-archive\SKILL.md`
- `E:\agentv2\skills\qingbian-multisource-archive\SKILL.md`

## Promotion 状态

已 promotion 到正式 runtime 层：

- 脚本层：`scripts/`
- skill 层：`skills/`
- 外部来源材料层：`ingestion/`

没有 promotion 到个人 `memory/`。

## 凭证与平台边界

2026-06-14 用户提供知乎 cookie JSON 后，知乎脚本已完成真实抓取：

- cookie JSON 可被 `--cookie-file` 直接读取。
- `doctor` 确认 `d_c0` 与 `z_c0` 存在。
- `run` 在 direct API 遇到 `10003` 后自动切到 browser fallback。
- articles/answers/pins 三类分页均跑到 `last_is_end: true`。

当前知乎归档结果：

- articles: visible `1260` / reported `1295`
- answers: visible `150` / reported `153`
- pins: visible `154` / reported `155`
- total visible records: `1564`
- missing Markdown: `0`

差额表示知乎 profile/list 口径中包含不可见或列表 API 不返回的项目。不能把差额当成本地漏抓，也不能宣称突破平台不可见项。

## 验收重点

- 微信归档必须达到 `index_rows == markdown_count == 894`。
- 微信归档必须 `missing_markdown_count == 0`。
- 微信归档必须 `extra_markdown_not_indexed_count == 0`。
- 知乎缺凭证时必须返回 `credential_required`。
- 知乎有凭证时必须记录 `fetch_mode`、`browser_stats`、`reported_total`、`visible_count` 和 `hidden_or_unavailable_count`。
- 统一索引必须生成 JSON、CSV、Markdown 三种视图。
- 请辩入口必须从 `qingbian-multisource-archive.ps1` 调用，避免重新拼散装命令。
