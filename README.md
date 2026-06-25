# 请辩多来源归档

## 这是什么

对微信公众平台"请辩"（蔡垒磊）和知乎"请辩"账号的全量文章归档系统。支持：

- 微信 7 个已知合集的自动增量抓取（含 bootstrap early exit 优化）
- 知乎文章/回答/想法的 cookie 验证抓取
- 跨平台统一索引和对账
- 统一原文库（单文件夹，文章级 frontmatter 标注来源）
- Windows 定时任务自动维护

## 前置依赖

- **Python 3.12+**
- **PowerShell 7+** (pwsh)
- **Windows**（定时任务依赖；macOS/Linux 可手工运行）

## 三步上手

```powershell
# 1. 安装 Python 依赖
python -m venv venv
venv\Scripts\pip install -r requirements.txt
python -m camoufox fetch          # 下载 Playwright 浏览器

# 2. 编辑 config.yaml，填入知乎 cookie 文件路径（可选）
#    不需要知乎归档则留空 zhihu.cookie_file

# 3. 首次全量抓取
pwsh scripts\qingbian-multisource-archive.ps1 -Action run -ZhihuCookieFile "C:\path\to\zhihu-cookie.json"
```

## 日常命令

```powershell
# 查看状态面板
pwsh scripts\qingbian-multisource-archive.ps1 -Action status

# 查看状态 + 定时任务状态
pwsh scripts\qingbian-multisource-archive.ps1 -Action progress

# 手动维护（微信 + 知乎 + 统一索引 + 统一原文库）
pwsh scripts\qingbian-multisource-archive.ps1 -Action run -ZhihuCookieFile "C:\path\to\zhihu-cookie.json"

# 仅重建统一索引
pwsh scripts\qingbian-multisource-archive.ps1 -Action build-index

# 仅重建统一原文库
pwsh scripts\qingbian-multisource-archive.ps1 -Action build-corpus

# 审计本地一致性
pwsh scripts\qingbian-multisource-archive.ps1 -Action audit -ZhihuCookieFile "C:\path\to\zhihu-cookie.json"

# 注册每日 15:40 定时任务
pwsh scripts\qingbian-multisource-archive.ps1 -Action register-task -ZhihuCookieFile "C:\path\to\zhihu-cookie.json"

# 停止/启动定时任务
pwsh scripts\qingbian-multisource-archive.ps1 -Action stop-task
pwsh scripts\qingbian-multisource-archive.ps1 -Action start-task
```

## 数据存放

| 内容 | 路径 |
|---|---|
| 微信文章 | `data/ingestion/10-Raw/WeChat/请辩/` |
| 知乎文章 | `data/ingestion/10-Raw/Zhihu/请辩/` |
| 统一原文库 | `data/ingestion/10-Raw/Qingbian/请辩/` |
| 统一索引 | `data/ingestion/80-Maps/Qingbian/请辩/` |
| 运行日志 | `data/runs/` |

可在 `config.yaml` 中修改 `paths.data_root`。

## 给 AI Agent 的说明

如果你是一个 AI agent，拿到这个项目后：

1. 阅读 `PROJECT.md` 了解架构
2. 确认 python 和 pwsh 可用
3. 按"三步上手"初始化环境
4. 用户需要提供知乎 cookie 文件路径（如不需要知乎则跳过）
5. 首次运行 `-Action run` 会做全量抓取（~30 分钟）
6. 后续维护运行时间 ~5 分钟（bootstrap early exit 优化）
7. 如需定时自动化，运行 `-Action register-task` 注册 Windows 定时任务

## 已知限制

- 微信抓取依赖已知合集 ID。如果作者新增合集，需手动添加到 `config.yaml`
- 知乎抓取需要定期更新 cookie 文件（cookie 过期后重新从浏览器导出）
- 定时任务方案仅支持 Windows
