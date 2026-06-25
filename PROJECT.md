# 请辩多来源归档 — 项目架构

## 调用链

```
qingbian-multisource-archive.ps1 (主入口)
├── wechat-qingbian-album-archive.ps1 (微信合集引擎)
│   ├── wechat-source-entry.ps1 → wechat-account-archive.ps1
│   │   └── wechat_account_archive.py (Playwright 抓取引擎)
│   ├── wechat_archive_consistency.py (索引一致性修复)
│   └── wechat_archive_audit.py (审计)
├── zhihu_account_archive.py (知乎 cookie 抓取)
├── qingbian_unified_index.py (跨平台统一索引)
├── qingbian_unified_corpus.py (统一原文库)
├── qingbian_status.py (状态面板)
└── qingbian_publish_time.py (发布时间分析)

openclaw-job-qingbian-multisource.ps1 (定时任务 runner)
└── qingbian-multisource-archive.ps1 -Action run
```

## 脚本职责

| 脚本 | 职责 |
|---|---|
| `qingbian-multisource-archive.ps1` | 主入口，调度全部 action |
| `wechat-qingbian-album-archive.ps1` | 微信合集管理：已知合集队列、抓取、修复、审计 |
| `wechat_account_archive.py` | Playwright 微信抓取引擎，含 album feed 爬取和 early exit 优化 |
| `zhihu_account_archive.py` | 知乎 cookie 抓取引擎 |
| `qingbian_unified_index.py` | 标题+日期跨平台匹配，生成分组索引 |
| `qingbian_unified_corpus.py` | 从统一索引物化统一原文库 |
| `qingbian_status.py` | 读取所有数据目录，生成状态面板 |

## 已知合集管理

微信抓取依赖 `config.yaml` 中的 `account.albums` 列表。每个合集的 `id` 是微信内部 album_id。

如果作者新增合集，需要：
1. 获取合集链接 → 用浏览器打开文章 → 页面源码搜 `album_id=`
2. 添加到 `config.yaml` 的 `account.albums`
3. 运行 `-Action run`

## Bootstrap Early Exit

`wechat_account_archive.py` 的 `collect_album_items` 支持提前退出：每日维护时，如果滚到底部 10 篇连续都是已知文章，则停止滚动。大合集（400+ 篇）只需加载 ~30 篇即可完成检查。首次全量抓取不受影响。

## 定时任务

- Windows Task Scheduler，任务名 `agentv2-qingbian-multisource-daily-archive`
- 每日 15:40（可在 config.yaml 调整）
- 子进程 45 分钟超时，Windows 任务 50 分钟上限
- 不叠加并发（`MultipleInstances: IgnoreNew`）

## 故障排查

| 症状 | 检查 |
|---|---|
| 微信新文章未抓取 | `-Action status` 看 WeChat 计数；确认文章在已知合集中；查看 runs 报告 |
| 知乎抓取失败 | cookie 是否过期（看 `Zhihu status`）；重新导出 cookie → `-Action run` |
| 定时任务未触发 | `-Action task-info` 看 State；确认已登录 Windows |
| 统一索引不一致 | `-Action build-index` + `-Action build-corpus` 重建 |
