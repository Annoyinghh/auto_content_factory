# Cloudflare 团队审片

团队成员继续使用 Windows 桌面工作台。Worker 只提供审片 API，D1 保存成员与审核记录，私有 R2 桶保存成片。每个成员拿自己的连接码。R2 不开公共访问，视频下载也通过需要连接码的 API。客户端将 MP4 按 8 MiB 分段上传，全部分段完成后，这条成片才出现在团队清单里。

上传中断后，在同一台电脑使用原连接码重新送审同一成片和文案。工作台在六天内会接着传剩下的分片，即使已经重启过。Cloudflare 默认在七天后清理未完成的分段上传。[R2 分段上传生命周期](https://developers.cloudflare.com/r2/objects/upload-objects/#incomplete-upload-lifecycles)

## R2 用量上限

| 项目 | 工作台上限 | Cloudflare Standard 免费额度 |
| --- | ---: | ---: |
| 存储 | 8 GB，包含已存成片和未完成上传的整片预留 | 每月 10 GB-month，约等于连续存 10 GB 一个月 |
| A 类操作 | 最近 32 个 UTC 日期累计 80 万次 | 每月 100 万次 |
| B 类操作 | 最近 32 个 UTC 日期累计 800 万次 | 每月 1,000 万次 |

Worker 在调用 R2 之前记账，达到上限时拒绝对应操作。分片上传、重试和成片读取在尝试调用 R2 前计数，调用失败也保留计数。新成片使用 Standard 存储类。未完成的送审预留继续计入存储上限，避免提前释放仍可能被占用的空间。

默认值在 `wrangler.jsonc` 的 `vars`，生产配置可调低。生产使用一个空的专用私有桶，并由这一套 Worker 和 D1 管理。Cloudflare 会按账号结算，直接操作桶或账号内其他项目的用量会另外计入账号总量。Cloudflare 的预算提醒会发送邮件，工作台的拒绝策略由服务端执行。[R2 定价](https://developers.cloudflare.com/r2/pricing/) · [Cloudflare 预算提醒](https://developers.cloudflare.com/billing/manage/budget-alerts/)

## 本机验证

```powershell
cd cloudflare/team
npm ci
npm run db:local
node member.mjs create "本地测试编辑"
node member.mjs create "本地测试审核"
npm run dev
```

终端会显示两个不同的连接码，只在本机测试中使用。桌面团队连接填 Wrangler 打印的 `http://127.0.0.1:8787`，两位成员应能完成送审、查看、留言和通过。在同一份旧记录上同时提交决定时，后一位应收到刷新提示。关闭本机 Worker 后，生产配置和数据不受影响。

## 异地上线

由团队在自己的 Cloudflare 账户开启 Workers、D1 和 R2。部署人员在本地登录 Wrangler，创建专用 D1 和 R2，使用实际数据库 ID 和桶名。可以先用 Cloudflare 提供的 `workers.dev` HTTPS 地址做受控试用，正式上线使用团队域名。

首次使用 R2 时，由账户管理员在 Cloudflare 控制台开通 R2 订阅并确认用量计费。部署人员需有 Workers、D1 和 R2 的管理权限。[R2 开通说明](https://developers.cloudflare.com/r2/get-started/)

```powershell
cd cloudflare/team
npm ci
npx wrangler login
npx wrangler d1 create acf-team
npx wrangler r2 bucket create acf-team-videos
Copy-Item -LiteralPath wrangler.jsonc -Destination wrangler.production.jsonc
```

在 `wrangler.production.jsonc` 中填写刚创建的 `database_name`、`database_id` 和 `bucket_name`。启用 `workers_dev` 以使用 `workers.dev`，或添加已获授权的 Cloudflare 自定义域名路由。生产文件已在 Git 忽略列表中。数据库迁移和 Worker 发布应使用同一份生产配置。

```powershell
npx wrangler d1 migrations apply DB --remote --config wrangler.production.jsonc
npx wrangler deploy --config wrangler.production.jsonc
node member.mjs create "编辑姓名" --remote
```

通过团队已有的私密渠道将个人连接码发给对应成员。停用旧连接码用 `node member.mjs revoke "编辑姓名" --remote`。每个团队使用自己的 Worker、数据库和桶。R2 保存的是原视频，应与 D1 一起安排备份和恢复演练。

正式开放前用两台不同网络的电脑测试一条长片的完整送审流程，并验证连接码撤销。已部署的服务地址应使用 HTTPS。尚未完成这些外部检查时，保持在测试范围内。

[Workers 请求限制](https://developers.cloudflare.com/workers/platform/limits/) · [R2 分段上传](https://developers.cloudflare.com/r2/api/workers/workers-multipart-usage/) · [D1 迁移命令](https://developers.cloudflare.com/workers/wrangler/commands/d1/) · [Workers 试用地址](https://developers.cloudflare.com/workers/configuration/routing/workers-dev/)
