# 内容工厂 · 多平台视频工作台

在 Windows 上从采集资讯、确认报道和编写分镜，到试剪、导出视频和审片投稿。桌面版和本地 Web 版都面向小红书、抖音和 B 站。项目也提供批量文案和独立的海外商业案例导出工具。

## 从源码启动

在 Windows x64 上安装 64 位 Python 3.12。在项目根目录打开 PowerShell，确认 `python --version` 显示 3.12，再创建虚拟环境并安装依赖。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

| 入口 | 命令 | 用途 |
| --- | --- | --- |
| Windows 桌面版 | `.\run_cli.bat` | 采集资讯、制作视频、审片和管理账号 |
| 本地 Web 工作台 | `.\run_web_ui.bat` | 在浏览器中使用工作台，默认地址为 `http://127.0.0.1:8765` |
| 案例库命令行 | `.\.venv\Scripts\python.exe cli.py --list` | 查看内置海外商业案例 |

### 本地 AI

AI 辅助编稿、分镜和批量文案需要在本机运行 Ollama。默认模型是 `qwen3.5:4b`。启动 Ollama 后运行 `ollama pull qwen3.5:4b`，也可以将 `CONTENT_AI_MODEL` 设为已下载的其他模型名，再重新启动工作台。编稿和编辑上下文通过本机 `127.0.0.1:11434` 发送给 Ollama。

## 桌面版长片制作

1. **采集与选题**。在采集库或平台热点中选择题材，核对并勾选要使用的报道。工作台会定期采集公开 RSS 和榜单，去重并清理超过 7 天的采集记录。已进入制作的选题和成片单独保留。可用 `CONTENT_NEWS_FEEDS` 指定以空格分隔的 RSS 或 Atom 地址。
2. **分镜与试剪**。确认报道后生成开场试剪和分镜。AI 编写的口播按镜头检查完整句子、原文引用和数字依据。桌面版会在新试剪生成后刷新预览入口。可在分镜编辑器中调整口播和素材后保存。缺少合适素材时，可以使用文字卡片。
3. **导出长片**。审看并确认当前分镜后导出。资讯长片以约 12 分钟为目标，程序在配音后按实测时长校验是否在 10–15 分钟内。调整分镜后可以重新导出。

桌面版可按需开启每天本地时间 08:30 的 Windows 计划任务。任务生成的成片会进入审片队列。

## 审片与投稿

在审片页可暂缓当前版本或恢复它的发布资格。在 Web 审片页提交抖音或 B 站审核前，确认目标账号和作品。上传期间按钮会锁定，成功后显示稿件编号。状态为「待核对」时，先到平台创作者后台确认结果，避免重复投稿。

### 平台与账号

| 平台 | 成片画幅 | 投稿方式 |
| --- | --- | --- |
| 抖音 | 9:16 | 授权后提交开放平台审核。超过 128 MB 自动分片 |
| B 站 | 16:9 | 授权后提交开放平台审核。超过 100 MB 自动分片 |
| 小红书 | 9:16 | 从审片页打开创作者后台，手动上传成片和配文 |

抖音和 B 站的分片会在提交审核前合并。通过工作台直接投稿到这两个平台时，单个视频最大为 4 GB。B 站封面或完整素材署名超出接口限制时，可前往创作者后台手动投稿并保留署名。

在「账号与作品 → 应用配置」填入自己的开放平台应用信息，再点击「官方授权登录」。使用与平台登记一致的回调地址，授权完成后将完整回调地址粘贴回工作台。应用配置和令牌使用 Windows DPAPI 保护。

| 平台 | 可用的环境变量 | 应用权限 |
| --- | --- | --- |
| 抖音 | `DOUYIN_CLIENT_KEY`、`DOUYIN_CLIENT_SECRET`、`DOUYIN_REDIRECT_URI` | `user_info`、`video.list`、`video.create` |
| B 站 | `BILIBILI_CLIENT_ID`、`BILIBILI_CLIENT_SECRET`、`BILIBILI_REDIRECT_URI`、`BILIBILI_TID` | `ARC_BASE`，建议开通 `USER_INFO` 以显示昵称 |

## 批量文案与案例物料

- **批量文案**。填写题材和已核实的事实，选择平台，每批可为每个平台生成 1–3 版标题、口播和发布配文。工作台从案例库检索写法摘要，生成结果附参考出处并保存到 `output/copywriting/`。
- **海外商业案例导出**。`cli.py --case pet_rock` 可导出单个案例，`cli.py --batch` 可批量导出。在项目根目录用 `.\.venv\Scripts\python.exe` 运行这些命令。案例物料适配小红书、抖音、B 站和微信视频号，可用成片、封面及配文自行发布。

## 数据位置与可选打包

| 运行方式 | 数据根目录 |
| --- | --- |
| 源码运行 | 项目根目录 |
| 打包版 | `%LOCALAPPDATA%\AutoContentFactory` |

采集库、草稿和成片写到数据根目录下的 `output/`。克隆仓库并安装依赖后，可以运行打包脚本自行构建 Windows EXE。

```powershell
.\.venv\Scripts\python.exe -m pip install PyInstaller==6.22.3
.\build_windows.ps1
```

生成的 `dist\AutoContentFactory.exe` 可双击启动，已包含 Python 运行环境和 FFmpeg。需要 AI 功能时仍需运行本机 Ollama。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```
