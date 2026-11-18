# Cloudflare 团队运营

打开 `dist\AutoContentFactory-Studio.exe`，从左侧进入「团队运营」。工作台通过 HTTPS 连接 Workers。D1 保存团队成员、共享账号备注、负责人、投稿任务、进度和算力队列。个人采集库、成片、平台登录会话和本机路径保存在各自电脑上。团队运营的 Worker 只绑定 D1。

## 工作台用法

1. 打开「团队运营」，填入团队服务地址和管理员发给你的个人连接码。
2. 在「共享矩阵」登记平台和账号备注。团队成员可以查看矩阵，登记人维护自己账号的投稿任务。账号备注可以与本机抖音账号备注保持一致。
3. 在「发布进度」为自己的矩阵账号创建任务。打开任务对应的本机发布中心后，标题和账号备注均匹配的抖音任务会在填稿成功时同步为「已准备投稿页」。平台提交和发布状态由运营人员核对后更新。
4. 在「计算队列」开启本机接单。工作台保持运行时，它会用本机已安装的 Ollama 模型领取任务。文案使用本机编稿模型，分镜使用本机视觉设计模型处理文字。
5. 需要借用算力时，可以从选题界面勾选报道并点「交给团队算力」，或者在队列直接填写已核实的摘要。选好文案或分镜后，预览并确认要发送的文字。只有领取任务的同事电脑会收到摘要。工作台会自动收取结果，在队列提示未读，并将结果保存在发起人的电脑上。

团队运营服务暂存确认提交的摘要和生成结果。任务完成后，服务清除任务中的原摘要。任务自创建起保留七天，供发起人收取结果。Cloudflare 账号管理员可以管理这套 D1 数据，数据库的备份保留期由 Cloudflare 管理。

## 本机验证

```powershell
npm ci --prefix cloudflare/team
cd cloudflare/operations
$acfWrangler = '..\team\node_modules\.bin\wrangler.cmd'
& $acfWrangler d1 migrations apply DB --local
node ../team/member.mjs create "编辑测试" --config wrangler.jsonc
node ../team/member.mjs create "算力测试" --config wrangler.jsonc
& $acfWrangler dev --ip 127.0.0.1
```

两个工作台各使用一个连接码，服务地址填 Wrangler 显示的本机地址。由编辑端登记账号和任务，再确认算力端能看到矩阵与投稿进度。算力端开启接单，编辑端提交一段测试摘要，确认结果只回到编辑端。

## 公网试用

由部署人员在团队 Cloudflare 账号中创建专用 D1 数据库，并填入 `wrangler.production.jsonc`。保持数据库和 Worker 一一对应，成员连接码按团队分别签发。

```powershell
Copy-Item -LiteralPath wrangler.jsonc -Destination wrangler.production.jsonc
& $acfWrangler d1 create acf-operations
```

在生产配置中填入新数据库的 `database_name` 和 `database_id`。可以先启用 `workers_dev` 做内部试用，长期使用时绑定团队已有的域名。生产配置已在 Git 忽略列表中。

```powershell
& $acfWrangler d1 migrations apply DB --remote --config wrangler.production.jsonc
& $acfWrangler deploy --config wrangler.production.jsonc
node ../team/member.mjs create "编辑姓名" --remote --config wrangler.production.jsonc
```

通过团队已有的私密渠道把地址和连接码交给对应成员。停用连接码用 `node ../team/member.mjs revoke "编辑姓名" --remote --config wrangler.production.jsonc`。正式开放前，用不同网络的 Windows 电脑验证矩阵、投稿进度和算力结果。 [Workers 试用地址](https://developers.cloudflare.com/workers/configuration/routing/workers-dev/) · [D1 免费配额](https://developers.cloudflare.com/d1/platform/pricing/)
