---
doc_type: guide
status: active
authority: guide
scope: qingbian-multisource-archive-operations
supersedes: []
superseded_by: []
related:
  - skills/qingbian-multisource-archive/SKILL.md
  - skills/zhihu-account-archive/SKILL.md
  - scripts/qingbian-multisource-archive.ps1
  - scripts/openclaw-job-qingbian-multisource.ps1
  - scripts/qingbian_status.py
  - scripts/qingbian_publish_time.py
  - scripts/qingbian_unified_corpus.py
review_state: reviewed
producer: codex
producer_role: controller
producer_evidence: current Codex run 2026-06-18; qingbian multisource automation timing, exit hardening, and unified corpus landing
review_owner: codex-controller
canonical_status: formal
---

# 请辩多来源归档操作手册

路径：`docs/20260615-0000-N-qingbian-multisource-archive-operations-guide.md`

## 核心判断

请辩归档的正式入口不是模型“读脚本后手动抓”，而是确定性脚本自己抓取、对账、生成索引和报告。模型只负责调用、检查、解释结果和在失败时修复机制。

当前正式入口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 <参数>
```

## 存放位置

- 微信正文：`E:\agentv2\ingestion\10-Raw\WeChat\请辩`
- 知乎正文：`E:\agentv2\ingestion\10-Raw\Zhihu\请辩`
- 统一原文库：`E:\agentv2\ingestion\10-Raw\Qingbian\请辩`
- 统一索引：`E:\agentv2\ingestion\80-Maps\Qingbian\请辩`
- 自动化报告：`E:\agentv2\runs\*qingbian-multisource-cycle*`

这些材料全部属于 `ingestion/` 外部来源层，不会自动进入个人 `memory/`。

统一原文库是账号级阅读入口：所有微信和知乎正文被物化到同一个 `items/` 文件夹，不再按平台拆子目录。平台来源、来源类型、原始 URL、原始 Markdown 路径和哈希写入每篇 Markdown 的 frontmatter。

## 常用命令

### 查看总状态

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action status
```

用途：前台查看微信、知乎、统一索引、跨来源覆盖和知乎分页完整性。

### 查看总状态和定时任务

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action progress
```

用途：同时查看本地归档状态和 Windows Scheduled Task 状态。

### 手动运行一次完整维护

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action run -ZhihuCookieFile E:\GoogleDownload\www.zhihu.com_json_1781442150769.json
```

用途：执行微信已知合集 repair、知乎 cookie 抓取、统一索引重建。

注意：命令只传入 cookie 文件路径，不输出 cookie 值。

### 只重建统一索引

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action build-index
```

用途：正文已经抓完，只重新生成 WeChat + Zhihu 统一索引。

### 只重建统一原文库

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action build-corpus
```

用途：统一索引已经存在，只把当前统一索引中的全部条目物化到 `E:\agentv2\ingestion\10-Raw\Qingbian\请辩\items\`。这一步不会重新抓取平台，只读两侧已有 Markdown 和统一索引。

### 审计本地一致性

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action audit -ZhihuCookieFile E:\GoogleDownload\www.zhihu.com_json_1781442150769.json
```

用途：检查本地索引和 Markdown 是否能对应。

### 推断发布时间与推荐抓取时间

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action publish-time
```

用途：读取本地微信和知乎文章索引中的发布时间，统计近期平均发布时间、中位发布时间和小时分布，并给出每日任务推荐运行时间。

当前策略：请辩多来源自动化以“较晚稳定出现的来源”为瓶颈。现有本地证据显示，微信公众号近期平均发布时间约在 13:35，知乎近期平均发布时间约在 14:38；因此默认抓取时间设为 15:40，也就是知乎近期平均发布时间后约一小时，并向上取整到 10 分钟。

## 定时任务

### 注册或更新每日任务

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action register-task -ZhihuCookieFile E:\GoogleDownload\www.zhihu.com_json_1781442150769.json
```

默认任务名：

```text
agentv2-qingbian-multisource-daily-archive
```

默认时间：

```text
15:40
```

如果要改时间：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action register-task -TaskTime 09:30 -ZhihuCookieFile E:\GoogleDownload\www.zhihu.com_json_1781442150769.json
```

### 立即启动定时任务

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action start-task
```

### 停止正在运行的定时任务

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action stop-task
```

### 查看定时任务详情

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action task-info
```

### 定时任务退出规则

定时任务不应该依赖空白 PowerShell 窗口是否还在来判断状态。正式判断方式是：

- `-Action task-info` 或 `-Action progress` 中的 `State` 应在任务结束后回到 `Ready`。
- `Last result` 为 `0` 表示 Windows Scheduled Task 看到的上一轮退出码为成功。
- `runs/*qingbian-multisource-cycle-report.md` 是本轮完整执行报告。

为避免“脚本已经完成但任务仍显示 Running”，当前 runner 执行两层退出保护：

- `openclaw-job-qingbian-multisource.ps1` 只向任务调度器输出一行报告路径，不把大段 stdout 直接挂在任务控制台上。
- 维护子进程最多运行 25 分钟，超时会终止子进程树并返回 `124`。
- Windows Scheduled Task 自身设置 30 分钟执行上限，且 `MultipleInstances` 为 `IgnoreNew`，避免上一轮未退出时叠加新一轮。

### 删除定时任务

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\agentv2\scripts\qingbian-multisource-archive.ps1 -Action unregister-task
```

## 如何判断抓完了

### 微信侧

微信侧完成的本地条件：

- `archive-index.json` 条目数等于本地 Markdown 数。
- `missing_markdown_count == 0`。
- `extra_markdown_not_indexed_count == 0`。
- 请辩入口使用已知合集 ID，不用搜索公众号文章。

### 知乎侧

知乎侧完成的本地条件：

- `crawl-metadata.json.status == success`。
- `archive-index.json` 有记录。
- `audit` 显示 `missing_markdown_count == 0`。
- 浏览器 fallback 统计中各类 `last_is_end == true`。

知乎的 `reported_total` 可能大于 `visible_count`。这通常表示知乎报告了不可见或不再由列表接口返回的项目，不等于本地漏抓。判断本地是否漏抓，要看分页是否结束和本地 Markdown 是否缺失。

### 统一索引侧

统一索引会显示：

- 微信条目数。
- 知乎条目数。
- 标题日期跨平台匹配。
- 正文哈希跨平台匹配。
- 仅微信分组。
- 仅知乎分组。
- 多来源分组。
- 当前全部来源均出现的分组。

`neither` 当前不适用，因为还没有第三方权威全集目录。未来如果接入新的 reference catalog，才可以判断“两边都没有”的文章。

### 统一原文库侧

统一原文库完成的本地条件：

- `E:\agentv2\ingestion\10-Raw\Qingbian\请辩\archive-index.json` 条目数等于 `items/` 下 Markdown 数。
- `source_item_count == materialized_count`。
- `missing_source_markdown_count == 0`。
- `items/` 下不再按平台拆子目录；来源只写在每篇 Markdown 的 frontmatter。

当前完成状态可通过 `-Action status` 查看 `Unified corpus index/markdown` 和 `Unified corpus missing source markdown`。

## cookie 处理

知乎抓取需要登录态 cookie，至少包含可用的 `d_c0` 和 `z_c0`。

允许输入：

- 浏览器导出的 JSON cookie 文件。
- 原始 Cookie header 文本文件。
- 环境变量 `QINGBIAN_ZHIHU_COOKIE_FILE` 指向 cookie 文件。
- 环境变量 `ZHIHU_COOKIE` 保存原始 Cookie header。

禁止：

- 把 cookie 值写入脚本。
- 把 cookie 值写入 docs、runs、memory 或聊天。
- 把搜索结果片段当成知乎账号级抓取完成。

## 自动化主体

自动化主体是脚本，不是模型。

定时任务调用：

```text
scripts/openclaw-job-qingbian-multisource.ps1
```

这个 runner 只负责：

- 调用正式 wrapper。
- 记录 stdout。
- 记录 stderr。
- 生成状态快照。
- 写运行报告。

它不重新实现抓取逻辑，也不做语义 promotion。正式 wrapper 的 `run` 会在抓取和统一索引后继续执行统一原文库构建，因此每日任务结束后应同时刷新 `ingestion\10-Raw\Qingbian\请辩\`。

## 故障定位

优先顺序：

1. 运行 `-Action progress` 看总状态和任务状态。
2. 看最近的 `E:\agentv2\runs\*qingbian-multisource-cycle-report.md`。
3. 看对应的 stdout/stderr 文件。
4. 如果知乎失败，先检查 cookie 文件是否还存在、是否仍是登录态。
5. 如果微信失败，先确认请辩已知合集路径是否仍能 repair，不要改回搜索入口。

## 非目标

- 不把请辩材料自动 promotion 到个人 `memory/`。
- 不用模型逐篇判断哪些文章要抓。
- 不用微信搜索作为请辩账号级入口。
- 不把知乎 `reported_total - visible_count` 自动当成本地漏抓。
