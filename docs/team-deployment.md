# 团队审片部署

团队也可采用 [Cloudflare 托管方案](cloudflare-team.md)。本页适用于已有常开的 Windows 或 Linux 主机，成片和审核记录保存在该主机上。

同事各自在 Windows 上打开桌面工作台，使用同一个 HTTPS 服务地址和不同的个人连接码。制作成员在「发布中心」选中当前成片并点「送团队审片」。审核成员在「团队审片」里播放同一版视频、留言、退回修改或通过。审片动作会记录成员、时间和决定。两人同时修改同一条审核记录时，后提交的人需要先刷新。

改完后打开原送审记录，点「送审新版」，在发布中心选中同平台的新成片，再点「送新版审片」。每版各有自己的成片、文案和反馈。在审片详情点「关联版本」可打开上一版或后续版本。

本阶段共享的是成片、文案和审核记录。分镜编辑、素材采集和渲染继续在各自的电脑上执行；客户端在本机保存下载用于播放的审片视频。团队服务端持有共享数据和审核记录，社交平台账号的连接方式与本服务无关。

## Windows 主机

在常开的 Windows 主机上，将 `dist\tools\AutoContentFactory-Team-Host.exe` 复制到部署目录。用 PowerShell 在该目录运行以下命令。将 `D:\TeamReview` 换成只允许服务运行账号和管理员访问的数据目录。

```powershell
.\AutoContentFactory-Team-Host.exe --data D:\TeamReview create-member "编辑姓名"
.\AutoContentFactory-Team-Host.exe --data D:\TeamReview serve
```

每位同事使用自己的连接码。停用旧码时，运行相同的 `--data` 参数和 `revoke-member "编辑姓名"`。连接码只在创建时显示，请私下交给对应成员。部署人员可将 `serve` 命令设为 Windows 开机任务，使用能读写数据目录的专用账号运行。

主机上的服务监听 `127.0.0.1:8765`。将团队已有的 HTTPS 反向代理指向这个端口，让同事在已有私网内访问 HTTPS 地址。若使用私有 CA，可以在启动工作台的 PowerShell 会话中把 `REQUESTS_CA_BUNDLE` 指向管理员提供的根证书 PEM，然后在该会话启动 `AutoContentFactory-Studio.exe`。主机也可使用已有的受信任 HTTPS 域名。 [Caddy 本地 HTTPS](https://caddyserver.com/docs/automatic-https#local-https)

在维护窗口停止服务和成员管理命令，再备份整个 `D:\TeamReview`。恢复时将备份复制到专用目录，用 `--data` 指向恢复目录启动，确认能打开成片和历史意见。开发环境可用 `deploy\build_team_host.ps1` 重新构建主机版，构建前在项目虚拟环境中安装 `requirements-team.txt` 和 PyInstaller。

## Linux 主机

准备一台可被团队访问的 Linux 主机、Python 3.12 和指向该主机的 HTTPS 域名。把本仓库放在 `/opt/auto-content-factory`，使用专用系统账号 `acf-team` 运行服务。代码和虚拟环境由部署人员维护，服务账号只写 `/var/lib/acf-team`。

```sh
cd /opt/auto-content-factory
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-team.txt
sudo install -m 0644 deploy/acf-team.service /etc/systemd/system/acf-team.service
sudo systemctl daemon-reload
sudo systemctl enable --now acf-team
```

将 [Caddy 配置示例](../deploy/Caddyfile.team.example) 的域名改成实际域名，再放进主机已有的 Caddy 配置。Waitress 只监听主机本地的 `127.0.0.1:8765`。异地客户端通过反向代理的 HTTPS 入口连接。服务接收的 MP4 最大 2 GiB，反向代理的上传限制应允许团队预期的成片大小。

每人生成一个连接码，通过团队已有的私密渠道发给对应成员。服务只保存连接码的哈希。成员离开或需要重置时停用旧码，再按需重新生成。

```sh
cd /opt/auto-content-factory
sudo -u acf-team .venv/bin/python -m core.team_service --data /var/lib/acf-team create-member "编辑姓名"
sudo -u acf-team .venv/bin/python -m core.team_service --data /var/lib/acf-team revoke-member "编辑姓名"
```

## 上线验收

1. 两台 Windows 电脑在不同网络使用不同连接码连接同一地址。
2. 制作成员上传一段已获准测试的 MP4。审核成员能看到标题、文案并播放同一版。
3. 审核成员留言和通过后，制作成员刷新能看到带成员名的审核记录。两人同时决定时，后提交的一方收到刷新提示。
4. 停用一个连接码，确认该码无法继续访问审片清单和视频。保存一次完整备份并在隔离环境恢复后复测。

备份应同时保存 `/var/lib/acf-team` 下的数据库和 `videos/`。最简单的做法是在维护窗口停服务后备份整个目录，完成后再启动。恢复时使用同一份备份中的数据库和视频文件。此目录应由服务账号独占，放在服务端本地磁盘。公网服务和域名上线前应先跑完两台电脑的验收。
