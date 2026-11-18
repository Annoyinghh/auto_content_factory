using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using Microsoft.Win32;

namespace Acf.Studio
{
    internal sealed partial class WorkbenchWindow
    {
        private IDictionary<string, object> flowSnapshot;
        private bool flowLoading;
        private string flowProblem = "";

        private FrameworkElement BuildFlow()
        {
            var panel = new StackPanel();
            var head = new DockPanel { Margin = new Thickness(0, 0, 0, 14) };
            var actions = new WrapPanel { VerticalAlignment = VerticalAlignment.Center };
            var refresh = Ui.Button(flowLoading ? "正在刷新…" : "刷新工作台", async () =>
            {
                await ReadOverviewAsync("force");
                await LoadFlowAsync(true);
            }, false, "\uE72C");
            refresh.IsEnabled = !flowLoading;
            refresh.Margin = new Thickness(0, 0, 8, 0);
            actions.Children.Add(refresh);
            actions.Children.Add(Ui.Button("粘贴文案新建视频", () => OpenQuickScriptDialog(), true, "\uE710"));
            DockPanel.SetDock(actions, Dock.Right);
            head.Children.Add(actions);
            var name = new StackPanel();
            name.Children.Add(Ui.Title("创作工作台"));
            name.Children.Add(Ui.Text("从「① 挑热点或贴文案」交给本地 AI，到「② 审素材改分镜」，再到「③ 自动质检与免 API 平台直投」，全流程一屏直达。", 13, true));
            head.Children.Add(name);
            panel.Children.Add(head);

            if (flowProblem.Length > 0)
            {
                var warning = Ui.Text(flowProblem, 13);
                warning.Foreground = Ui.Color("BlushInkBrush");
                warning.Margin = new Thickness(0, 0, 0, 10);
                panel.Children.Add(warning);
            }

            var data = flowSnapshot ?? J.Obj();
            panel.Children.Add(BuildEngineAndNoticeBar(data));

            var notices = J.Rows(J.Get(data, "notices")).ToArray();
            if (notices.Length > 0)
                panel.Children.Add(BuildQualityAlertsCard(notices));

            panel.Children.Add(BuildQuickStartRow(J.Map(J.Get(data, "collection"))));
            panel.Children.Add(BuildRecentCreationsCard(J.Rows(J.Get(data, "recent_creations")).ToArray()));
            return Ui.Scroll(panel);
        }

        private FrameworkElement BuildEngineAndNoticeBar(IDictionary<string, object> data)
        {
            var grid = new Grid { Margin = new Thickness(0, 0, 0, 14) };
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });

            var ai = J.Map(J.Get(data, "ai"));
            bool aiReady = Object.Equals(J.Get(ai, "ready"), true);
            bool visualReady = Object.Equals(J.Get(ai, "visual_ready"), true);
            bool portable = Object.Equals(J.Get(ai, "portable_available"), true);
            var aiPanel = new StackPanel();
            var aiTop = new DockPanel { Margin = new Thickness(0, 0, 0, 6) };
            aiTop.Children.Add(Ui.Badge(flowSnapshot == null ? "正在检测本地 AI" : aiReady ? "本地 AI 已就绪" : "本地 AI 未启动",
                aiReady ? "Sage" : "Honey"));
            var aiBtn = Ui.Filter(aiReady ? "重新检测模型" : portable ? "启动随包本地 AI" : "检测本地 Ollama", async () =>
            {
                Notify(portable && !aiReady ? "正在启动本机 Ollama 服务…" : "正在检查本机 Ollama 模型…");
                try
                {
                    await Worker.RunAsync(portable && !aiReady ? "workspace_ai_start" : "workspace_ai_status");
                    await LoadFlowAsync(true);
                    Notify("本地 AI 状态已刷新。");
                }
                catch (Exception error) { Notify(error.Message); }
            });
            DockPanel.SetDock(aiBtn, Dock.Right);
            aiTop.Children.Add(aiBtn);
            aiPanel.Children.Add(aiTop);
            aiPanel.Children.Add(EditorialStrong("本地 AI 创作引擎（本机 Ollama）", 15));
            string writingModel = J.Text(ai, "writing_model");
            aiPanel.Children.Add(Ui.Text(aiReady
                ? "写稿模型：" + writingModel + "  ·  " + (visualReady ? "多模态看图选镜已就绪" : "当前使用文本分镜模式")
                : "未检测到运行中的本地 Ollama（127.0.0.1:11434）。先启动 Ollama 后即可在本机免费写文案、排分镜。", 12, true));
            var leftCard = Ui.Card(aiPanel, "SkyBrush");
            leftCard.Margin = new Thickness(0, 0, 7, 0);
            grid.Children.Add(leftCard);

            var webhook = J.Map(J.Get(data, "webhook"));
            bool hookOn = Object.Equals(J.Get(webhook, "enabled"), true);
            bool opsOn = Object.Equals(J.Get(data, "ops_connected"), true);
            bool teamOn = Object.Equals(J.Get(data, "team_connected"), true);
            var noticePanel = new StackPanel();
            var noticeTop = new DockPanel { Margin = new Thickness(0, 0, 0, 6) };
            noticeTop.Children.Add(Ui.Badge(hookOn ? "群通知已开启 · " + J.Text(webhook, "label")
                : (opsOn || teamOn) ? "团队同步已开启" : "本机拦截已开启 · 可配群通知",
                hookOn || opsOn || teamOn ? "Sage" : "Honey"));
            var configHook = Ui.Filter("配置群机器人通知", () => OpenSettings(1));
            DockPanel.SetDock(configHook, Dock.Right);
            noticeTop.Children.Add(configHook);
            noticePanel.Children.Add(noticeTop);
            noticePanel.Children.Add(EditorialStrong("成片质检拦截与免 API 通知", 15));
            var activeChannels = new List<string> { "工作台待改拦截" };
            if (hookOn) activeChannels.Add(J.Text(webhook, "label"));
            if (opsOn) activeChannels.Add("共享运营");
            if (teamOn) activeChannels.Add("团队审片");
            noticePanel.Children.Add(Ui.Text("已启用渠道：" + String.Join(" + ", activeChannels.ToArray())
                + "。支持飞书 / 企业微信 / 钉钉群机器人直推，无需申请平台开发 API。", 12, true));
            var rightCard = Ui.Card(noticePanel, "HoneyBrush");
            rightCard.Margin = new Thickness(7, 0, 0, 0);
            Grid.SetColumn(rightCard, 1);
            grid.Children.Add(rightCard);
            return grid;
        }

        private FrameworkElement BuildQualityAlertsCard(IDictionary<string, object>[] notices)
        {
            var box = new StackPanel();
            var header = new DockPanel { Margin = new Thickness(0, 0, 0, 8) };
            header.Children.Add(Ui.Badge("待处理质检退回 · " + notices.Length + " 项", "Blush"));
            box.Children.Add(header);
            box.Children.Add(EditorialStrong("以下成片未通过质检或被标记为不合适（已自动暂缓发布）", 16));
            foreach (var notice in notices.Take(5))
            {
                string id = J.Text(notice, "id");
                string platform = J.Text(notice, "platform");
                string day = J.Text(notice, "day");
                string title = J.Text(notice, "title");
                var row = new DockPanel { Margin = new Thickness(0, 8, 0, 0) };
                var btns = new WrapPanel { VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(10, 0, 0, 0) };
                btns.Children.Add(Ui.Button("去剪辑台重改 →", () => OpenProduction(platform, day), true));
                var dismiss = Ui.Filter("已知晓", async () =>
                {
                    try
                    {
                        await Worker.RunAsync("workspace_notice_dismiss", payload: J.Obj("id", id));
                        await LoadFlowAsync(true);
                    }
                    catch (Exception error) { Notify(error.Message); }
                });
                dismiss.Margin = new Thickness(6, 0, 0, 0);
                btns.Children.Add(dismiss);
                DockPanel.SetDock(btns, Dock.Right);
                row.Children.Add(btns);

                var info = new StackPanel();
                info.Children.Add(EditorialStrong("【" + J.Text(notice, "tag") + "】" + J.Text(notice, "platform_name") + " · 《" + title + "》 (" + day + ")", 14));
                var findings = J.Items(J.Get(notice, "findings")).Select(v => Convert.ToString(v)).ToArray();
                if (findings.Length > 0)
                {
                    var reason = Ui.Text(String.Join("；", findings), 13);
                    reason.Foreground = Ui.Color("BlushInkBrush");
                    info.Children.Add(reason);
                }
                var channels = J.Items(J.Get(notice, "channels")).Select(v => Convert.ToString(v)).ToArray();
                if (channels.Length > 0)
                    info.Children.Add(Ui.Text("已通知：" + String.Join("、", channels), 12, true));
                row.Children.Add(info);
                box.Children.Add(new Border
                {
                    Child = row, BorderBrush = Ui.Color("LineBrush"), BorderThickness = new Thickness(0, 1, 0, 0),
                    Padding = new Thickness(0, 8, 0, 4)
                });
            }
            var card = Ui.Card(box, "BlushBrush");
            card.Margin = new Thickness(0, 0, 0, 14);
            return card;
        }

        private FrameworkElement BuildQuickStartRow(IDictionary<string, object> collection)
        {
            var section = new StackPanel { Margin = new Thickness(0, 0, 0, 14) };
            var title = EditorialStrong("开始新创作（选一种你手头有的材料）", 17);
            title.Margin = new Thickness(0, 0, 0, 8);
            section.Children.Add(title);

            var grid = new Grid();
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });

            int reports = IntValue(collection, "reports");
            int hotspots = IntValue(collection, "hotspots");
            var cardA = BuildStartCard(
                "方式 A · 从采集的热点/报道做片",
                "今日已入库 " + reports.ToString("N0") + " 篇报道 · " + hotspots.ToString("N0") + " 条平台热榜",
                "挑一条热点或新闻报道，自动带入正文作为依据，交给本地 AI 生成视频分镜或图文稿。",
                "去挑热点或报道 →", () => OpenTopicBrowser(), "SageBrush", true);
            cardA.Margin = new Thickness(0, 0, 8, 0);
            grid.Children.Add(cardA);

            var cardB = BuildStartCard(
                "方式 B · 粘贴自有文案直接剪视频",
                "已有写好的文案 / 口播稿 / 拍摄提纲",
                "无需在采集库里绕弯：直接贴入你的文案和本地视频/图片素材，一键让本地 AI 拆好分镜并试剪。",
                "粘贴文案做视频 →", () => OpenQuickScriptDialog(), "SkyBrush", false);
            cardB.Margin = new Thickness(4, 0, 4, 0);
            Grid.SetColumn(cardB, 1);
            grid.Children.Add(cardB);

            var cardC = BuildStartCard(
                "方式 C · 本地 AI 批量写图文/文案",
                "小红书实拍图文 · 抖音/B站口播文案",
                "输入一个主题或生活关键词，本地 AI 检索同题爆款结构，一次生成 2–3 版不同切入点的文案与配图。",
                "打开图文写稿台 →", () => OpenWriting(), "HoneyBrush", false);
            cardC.Margin = new Thickness(8, 0, 0, 0);
            Grid.SetColumn(cardC, 2);
            grid.Children.Add(cardC);

            section.Children.Add(grid);
            return section;
        }

        private static Border BuildStartCard(string heading, string badge, string desc, string buttonText, Action click, string brush, bool primary)
        {
            var stack = new StackPanel();
            stack.Children.Add(EditorialStrong(heading, 15));
            var sub = Ui.Text(badge, 12);
            sub.FontWeight = FontWeights.SemiBold;
            sub.Margin = new Thickness(0, 5, 0, 6);
            stack.Children.Add(sub);
            var body = Ui.Text(desc, 12, true);
            body.Margin = new Thickness(0, 0, 0, 12);
            stack.Children.Add(body);
            var btn = Ui.Button(buttonText, click, primary);
            btn.HorizontalAlignment = HorizontalAlignment.Left;
            stack.Children.Add(btn);
            return Ui.Card(stack, brush);
        }

        private FrameworkElement BuildRecentCreationsCard(IDictionary<string, object>[] creations)
        {
            var panel = new StackPanel();
            var head = new DockPanel { Margin = new Thickness(0, 0, 0, 8) };
            var more = new WrapPanel { VerticalAlignment = VerticalAlignment.Center };
            more.Children.Add(Ui.Filter("切换全部历史作品", OpenProductionHistory));
            more.Children.Add(Ui.Filter("打开作品书架", () => Navigate("works")));
            DockPanel.SetDock(more, Dock.Right);
            head.Children.Add(more);
            head.Children.Add(EditorialStrong("最近正在制作的作品（点击直接继续）", 17));
            panel.Children.Add(head);

            if (creations.Length == 0)
            {
                panel.Children.Add(Ui.Text(flowSnapshot == null ? "正在读取本机作品记录…"
                    : "目前还没有保存的视频创作。可以从上方任一入口开始第一条作品。", 13, true));
                return Ui.Card(panel);
            }

            foreach (var item in creations)
            {
                string platform = J.Text(item, "platform");
                string day = J.Text(item, "day");
                string title = J.Text(item, "title");
                int pendingMedia = IntValue(item, "pending_media");
                int approvedMedia = IntValue(item, "approved_media");
                bool hasPackage = Object.Equals(J.Get(item, "has_package"), true);
                bool held = Object.Equals(J.Get(item, "held"), true);
                bool qualityOk = Object.Equals(J.Get(item, "quality_ready"), true);
                bool active = Object.Equals(J.Get(item, "active"), true);

                var row = new DockPanel { Margin = new Thickness(0, 4, 0, 4) };
                var actions = new WrapPanel { VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(12, 0, 0, 0) };
                actions.Children.Add(Ui.Button(pendingMedia > 0 ? "去审素材并剪辑" : "继续剪辑", async () =>
                {
                    if (!active)
                    {
                        try { await Worker.RunAsync("workspace_creation_open", platform, item, day); }
                        catch (Exception error) { Notify(error.Message); return; }
                    }
                    OpenProduction(platform, day);
                }, !hasPackage || !qualityOk));
                if (hasPackage && active)
                {
                    var goPub = Ui.Button(held ? "查看质检记录" : "去投稿 →", () =>
                    {
                        DateTime parsed;
                        if (DateTime.TryParseExact(day, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed))
                            publishingDate = parsed;
                        publishingPlatform = platform;
                        Navigate("publishing");
                    }, qualityOk && !held);
                    goPub.Margin = new Thickness(6, 0, 0, 0);
                    actions.Children.Add(goPub);
                }
                DockPanel.SetDock(actions, Dock.Right);
                row.Children.Add(actions);

                var left = new StackPanel();
                var badges = new WrapPanel { Margin = new Thickness(0, 0, 0, 4) };
                var pBadge = Ui.Badge(Ui.Platform(platform) + " · " + day, "Sky");
                pBadge.Margin = new Thickness(0, 0, 6, 0);
                badges.Children.Add(pBadge);
                string stageLabel = hasPackage ? (held || !qualityOk ? "质检未通过 / 已暂缓" : "成片已质检合格")
                    : pendingMedia > 0 ? "待审素材 " + pendingMedia + " 项"
                    : J.Text(item, "artifact_state");
                string stageTone = hasPackage ? (held || !qualityOk ? "Blush" : "Sage") : pendingMedia > 0 ? "Honey" : "Sage";
                var sBadge = Ui.Badge(stageLabel, stageTone);
                sBadge.Margin = new Thickness(0, 0, 6, 0);
                badges.Children.Add(sBadge);
                badges.Children.Add(Ui.Text("分镜 " + J.Text(item, "scenes") + " 镜 · 可用素材 " + approvedMedia + " 项", 12, true));
                left.Children.Add(badges);
                left.Children.Add(EditorialStrong(title, 15));
                string qMsg = J.Text(item, "quality_message");
                if (qMsg.Length > 0 && (!qualityOk || held))
                {
                    var hint = Ui.Text(qMsg, 12);
                    hint.Foreground = Ui.Color("BlushInkBrush");
                    left.Children.Add(hint);
                }
                row.Children.Add(left);
                panel.Children.Add(new Border
                {
                    Child = row, BorderBrush = Ui.Color("LineBrush"),
                    BorderThickness = new Thickness(0, 1, 0, 0), Padding = new Thickness(0, 8, 0, 6)
                });
            }
            return Ui.Card(panel);
        }

        internal void OpenQuickScriptDialog(string initialTitle = "", string initialScript = "", string initialPlatform = "douyin")
        {
            var dialog = new Window
            {
                Title = "粘贴文案或提纲 · 直接交给本地 AI 分镜剪辑",
                Owner = this, Width = 640, Height = 580, MinWidth = 520, MinHeight = 480,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                Background = Ui.Color("PaperBrush"), Icon = Icon
            };
            var dock = new DockPanel { Margin = new Thickness(22) };
            dialog.Content = dock;

            var top = EditorialHeading("把现成文案交给本地 AI 做成视频",
                "填好标题、粘贴你的文案或拍摄要点，可选加入本地实拍视频/图片。本地 AI 会自动拆解每镜口播并配好画面。");
            DockPanel.SetDock(top, Dock.Top);
            dock.Children.Add(top);

            var footer = new DockPanel { Margin = new Thickness(0, 12, 0, 0) };
            var start = Ui.Button("创建并让本地 AI 生成分镜 →", delegate { }, true);
            DockPanel.SetDock(start, Dock.Right);
            footer.Children.Add(start);
            var feedback = Ui.Text("", 13);
            feedback.Foreground = Ui.Color("BlushInkBrush");
            feedback.VerticalAlignment = VerticalAlignment.Center;
            footer.Children.Add(feedback);
            DockPanel.SetDock(footer, Dock.Bottom);
            dock.Children.Add(footer);

            var form = new StackPanel();
            var titleBox = EditorialField(initialTitle ?? "", "视频标题");
            titleBox.MaxLength = 160;
            form.Children.Add(EditorialLabeled("1. 视频标题", titleBox));

            var scriptBox = EditorialField(initialScript ?? "", "粘贴口播文案、段落要点或步骤说明（至少 40 字）", 150);
            scriptBox.MaxLength = 12000;
            form.Children.Add(EditorialLabeled("2. 口播文案或讲述要点（至少 40 字）", scriptBox));

            var optionsBar = EditorialBar();
            int pIdx = Math.Max(0, Array.IndexOf(EditorialPlatforms, initialPlatform));
            var platformBox = EditorialChoice(EditorialPlatformNames, pIdx, "目标平台");
            var durationBox = EditorialChoice(new[] { "约 1 分钟", "约 2 分钟", "约 3 分钟" }, 1, "目标时长");
            Action syncDur = () =>
            {
                bool bili = EditorialPlatforms[Math.Max(0, platformBox.SelectedIndex)] == "bilibili";
                durationBox.Visibility = bili ? Visibility.Collapsed : Visibility.Visible;
            };
            platformBox.SelectionChanged += delegate { syncDur(); };
            syncDur();
            optionsBar.Children.Add(EditorialLabeled("3. 发布平台", platformBox));
            optionsBar.Children.Add(EditorialLabeled("目标时长", durationBox));
            form.Children.Add(optionsBar);

            var selectedFiles = new List<string>();
            var filesSummary = Ui.Text("尚未添加本地视频/图片（可先生成分镜，之后在剪辑台补充素材）", 12, true);
            var fileBar = EditorialBar();
            fileBar.Children.Add(Ui.Button("+ 添加本机视频或图片", () =>
            {
                var picker = new OpenFileDialog { Filter = EditorialMediaFilter, Multiselect = true, Title = "选择实拍视频或图片（自动审核通过）" };
                if (picker.ShowDialog(dialog) != true) return;
                foreach (string f in picker.FileNames)
                    if (selectedFiles.Count < 8 && !selectedFiles.Contains(f)) selectedFiles.Add(f);
                filesSummary.Text = "已加入 " + selectedFiles.Count + " 个本地素材（自动标为已通过）："
                    + String.Join("、", selectedFiles.Select(Path.GetFileName).ToArray());
            }));
            fileBar.Children.Add(Ui.Filter("清空已选", () =>
            {
                selectedFiles.Clear();
                filesSummary.Text = "尚未添加本地视频/图片";
            }));
            var mediaLabel = Ui.Text("4. 画面素材（可选，最多 8 个）", 12, true);
            mediaLabel.Margin = new Thickness(0, 8, 0, 4);
            form.Children.Add(mediaLabel);
            form.Children.Add(fileBar);
            form.Children.Add(filesSummary);
            dock.Children.Add(Ui.Scroll(form));

            start.Click += async delegate
            {
                string chosenTitle = titleBox.Text.Trim();
                string chosenScript = scriptBox.Text.Trim();
                if (chosenTitle.Length == 0) { feedback.Text = "请先填写视频标题。"; titleBox.Focus(); return; }
                if (chosenScript.Length < 40)
                {
                    feedback.Text = "文案至少需要 40 字（当前 " + chosenScript.Length + " 字），请再补充几句具体内容。";
                    scriptBox.Focus();
                    return;
                }
                string chosenPlatform = EditorialPlatforms[Math.Max(0, platformBox.SelectedIndex)];
                int targetSeconds = chosenPlatform == "bilibili" ? 720 : (Math.Max(0, durationBox.SelectedIndex) + 1) * 60;
                string today = EditorialToday();
                start.IsEnabled = false;
                form.IsEnabled = false;
                feedback.Foreground = Ui.Color("MutedBrush");
                feedback.Text = "正在创建工作稿并导入素材…";
                try
                {
                    var created = J.Map(await Worker.RunAsync("workspace_quick_create", chosenPlatform, J.Obj(
                        "title", chosenTitle, "script", chosenScript,
                        "asset_paths", selectedFiles.ToArray(), "target_seconds", targetSeconds), today));
                    dialog.Close();
                    OpenProduction(chosenPlatform, today);
                    await RunProductionJob("editorial_preview", chosenPlatform, today,
                        J.Obj("id", J.Text(created, "id"), "direct", true),
                        "本地 AI 正在根据你的文案编排分镜与开场试剪…", "分镜与开场试剪已就绪，请审看！");
                }
                catch (Exception error)
                {
                    feedback.Foreground = Ui.Color("BlushInkBrush");
                    feedback.Text = error.Message;
                    start.IsEnabled = true;
                    form.IsEnabled = true;
                }
            };
            dialog.ShowDialog();
        }

        internal void OpenQualityRejectDialog(string platform, string day, string title, Func<Task> onDone, bool allowJumpEdit = true)
        {
            var dialog = new Window
            {
                Title = "标记成片不合适 · 自动拦截并发送通知",
                Owner = this, Width = 540, SizeToContent = SizeToContent.Height,
                ResizeMode = ResizeMode.NoResize, WindowStartupLocation = WindowStartupLocation.CenterOwner,
                Background = Ui.Color("PaperBrush"), Icon = Icon
            };
            var root = new StackPanel { Margin = new Thickness(22) };
            dialog.Content = root;
            root.Children.Add(EditorialStrong("《" + title + "》 · " + Ui.Platform(platform), 18));
            var sub = Ui.Text("勾选或填写不合适的原因。确认后将立即暂缓这版成片的发布资格，并自动推送到已启用的群机器人及团队空间。", 13, true);
            sub.Margin = new Thickness(0, 6, 0, 12);
            root.Children.Add(sub);

            string[] presets =
            {
                "画面与口播内容不匹配，或文字图卡过多",
                "成片时长、语速或剪辑节奏不合适",
                "口播文案表达生硬，或事实信息需修正",
                "配音、字幕排版或画面清晰度不过关"
            };
            var checks = presets.Select(label => new CheckBox
            {
                Content = label, Margin = new Thickness(0, 0, 0, 7)
            }).ToArray();
            foreach (var cb in checks) root.Children.Add(cb);

            var custom = EditorialField("", "补充具体修改要求（可选）", 72);
            custom.MaxLength = 350;
            root.Children.Add(EditorialLabeled("补充说明（可选）", custom));

            var statusText = Ui.Text("", 12);
            statusText.Foreground = Ui.Color("BlushInkBrush");
            statusText.Margin = new Thickness(0, 6, 0, 8);
            root.Children.Add(statusText);

            var bar = new WrapPanel { HorizontalAlignment = HorizontalAlignment.Right };
            var cancel = Ui.Button("取消", () => dialog.Close());
            cancel.Margin = new Thickness(0, 0, 8, 0);
            bar.Children.Add(cancel);

            Func<bool, Task> submitDecision = async jumpToEdit =>
            {
                var picked = checks.Where(c => c.IsChecked == true).Select(c => Convert.ToString(c.Content)).ToList();
                if (!String.IsNullOrWhiteSpace(custom.Text)) picked.Add(custom.Text.Trim());
                string reason = picked.Count > 0 ? String.Join("；", picked.ToArray()) : "人工审看认为当前版本不合适，需退回修改。";
                bar.IsEnabled = false;
                statusText.Foreground = Ui.Color("MutedBrush");
                statusText.Text = "正在暂缓成片并推送通知…";
                try
                {
                    var reply = J.Map(await Worker.RunAsync("workspace_quality_decide", platform,
                        J.Obj("decision", "rejected", "reason", reason), day));
                    var receipt = J.Map(J.Get(reply, "receipt"));
                    var channels = J.Items(J.Get(receipt, "channels")).Select(v => Convert.ToString(v)).ToArray();
                    var errors = J.Items(J.Get(receipt, "errors")).Select(v => Convert.ToString(v)).ToArray();
                    string summary = "已暂缓发布并通知：" + String.Join("、", channels);
                    if (errors.Length > 0) summary += "（提示：" + String.Join("；", errors) + "）";
                    Notify(summary);
                    dialog.Close();
                    if (onDone != null) await onDone();
                    if (jumpToEdit) OpenProduction(platform, day);
                }
                catch (Exception error)
                {
                    statusText.Foreground = Ui.Color("BlushInkBrush");
                    statusText.Text = error.Message;
                    bar.IsEnabled = true;
                }
            };

            if (allowJumpEdit)
            {
                var rejectAndEdit = Ui.Button("退回通知并去重剪", async () => await submitDecision(true));
                rejectAndEdit.Margin = new Thickness(0, 0, 8, 0);
                bar.Children.Add(rejectAndEdit);
            }
            bar.Children.Add(Ui.Button("确认不合适并发送通知", async () => await submitDecision(false), true));
            root.Children.Add(bar);
            dialog.ShowDialog();
        }

        internal FrameworkElement BuildQualityGateCard(IDictionary<string, object> inspection, string platform, string day,
            string title, bool hasVideo, Func<Task> refreshHost, bool showGoPublish)
        {
            var box = new StackPanel();
            bool ready = Object.Equals(J.Get(inspection, "ready"), true);
            string stage = J.Text(inspection, "stage");
            var review = J.Map(J.Get(inspection, "review"));
            bool manualRejected = J.Text(review, "decision") == "rejected";

            var header = new DockPanel { Margin = new Thickness(0, 0, 0, 8) };
            string badgeLabel = !hasVideo && stage != "package"
                ? (ready ? "草稿预检通过 · 可导出成片" : "草稿预检有待完善项")
                : manualRejected ? "已人工退回 · 暂缓发布"
                : ready ? "自动质检通过 · 可发布" : "自动质检未通过 · 已暂缓";
            string badgeTone = ready && !manualRejected ? "Sage" : hasVideo ? "Blush" : "Honey";
            header.Children.Add(Ui.Badge(badgeLabel, badgeTone));
            var rightTools = new WrapPanel { VerticalAlignment = VerticalAlignment.Center };
            rightTools.Children.Add(Ui.Filter("重新检测", async () =>
            {
                try
                {
                    await Worker.RunAsync("workspace_quality_inspect", platform, null, day);
                    if (refreshHost != null) await refreshHost();
                    Notify("已完成最新质量检测。");
                }
                catch (Exception error) { Notify(error.Message); }
            }));
            rightTools.Children.Add(Ui.Filter("配置群通知", () => OpenSettings(1)));
            DockPanel.SetDock(rightTools, Dock.Right);
            header.Children.Add(rightTools);
            box.Children.Add(header);

            box.Children.Add(EditorialStrong("成片质量自动检测单", 16));
            var checks = J.Map(J.Get(inspection, "checks"));
            var mediaCheck = J.Map(J.Get(checks, "media"));
            var visualCheck = J.Map(J.Get(checks, "visuals"));
            var durationCheck = J.Map(J.Get(checks, "duration"));
            var fileCheck = J.Map(J.Get(checks, "file"));

            var grid = new WrapPanel { Margin = new Thickness(0, 8, 0, 8) };
            int approvedMedia = IntValue(mediaCheck, "approved");
            int pendingMedia = IntValue(mediaCheck, "pending");
            grid.Children.Add(QualityCheckChip("① 素材审核",
                pendingMedia > 0 ? "待审 " + pendingMedia + " 项（已过 " + approvedMedia + " 项）" : "已过审 " + approvedMedia + " 项",
                pendingMedia == 0));

            if (visualCheck.Count > 0)
            {
                bool vOk = Object.Equals(J.Get(visualCheck, "ready"), true);
                grid.Children.Add(QualityCheckChip("② 画面组成",
                    "视频 " + IntValue(visualCheck, "video_shots") + " · 图片 " + IntValue(visualCheck, "image_shots")
                    + " · 解释动画 " + IntValue(visualCheck, "animated_shots") + " / 共 "
                    + IntValue(visualCheck, "total") + " 镜", vOk));
            }
            if (durationCheck.Count > 0)
            {
                bool dOk = Object.Equals(J.Get(durationCheck, "ok"), true);
                var range = J.Items(J.Get(durationCheck, "range")).Select(v => Convert.ToString(v)).ToArray();
                string rangeText = range.Length == 2 ? range[0] + "–" + range[1] + " 秒" : "";
                double sec;
                Double.TryParse(J.Text(durationCheck, "seconds"), NumberStyles.Number, CultureInfo.InvariantCulture, out sec);
                bool measured = Object.Equals(J.Get(durationCheck, "measured"), true);
                grid.Children.Add(QualityCheckChip("③ 口播时长",
                    (dOk ? "✓ " : "✗ ") + (measured ? "实测 " : "预估 ") + Math.Round(sec) + " 秒（要求 " + rangeText + "）", dOk));
            }
            if (hasVideo || J.Get(fileCheck, "ok") is bool)
            {
                bool fOk = Object.Equals(J.Get(fileCheck, "ok"), true);
                double mb = IntValue(fileCheck, "bytes") / 1048576.0;
                grid.Children.Add(QualityCheckChip("④ MP4 成片文件",
                    fOk ? "✓ 封装正常 · " + mb.ToString("0.1") + " MB" : "✗ 文件缺失或损坏", fOk));
            }
            box.Children.Add(grid);

            var findings = J.Items(J.Get(inspection, "findings")).Select(v => Convert.ToString(v)).Where(s => s.Length > 0).ToArray();
            if (findings.Length > 0)
            {
                foreach (string line in findings)
                {
                    var itemText = Ui.Text("· " + line, 13);
                    itemText.Foreground = Ui.Color("BlushInkBrush");
                    itemText.Margin = new Thickness(0, 1, 0, 2);
                    box.Children.Add(itemText);
                }
            }

            var actions = new WrapPanel { Margin = new Thickness(0, 8, 0, 0) };
            if (hasVideo)
            {
                var rejectBtn = Ui.Button("觉得不合适 · 退回并通知", () =>
                    OpenQualityRejectDialog(platform, day, title, refreshHost, showGoPublish));
                rejectBtn.Margin = new Thickness(0, 0, 8, 4);
                actions.Children.Add(rejectBtn);

                if (!ready || manualRejected)
                {
                    var approveBtn = Ui.Button("人工复核合格 · 允许发布", async () =>
                    {
                        try
                        {
                            await Worker.RunAsync("workspace_quality_decide", platform,
                                J.Obj("decision", "approved", "reason", "人工复核确认可发布"), day);
                            if (refreshHost != null) await refreshHost();
                            Notify("已解除暂缓，允许直投发布。");
                        }
                        catch (Exception error) { Notify(error.Message); }
                    });
                    approveBtn.Margin = new Thickness(0, 0, 8, 4);
                    actions.Children.Add(approveBtn);
                }
                if (showGoPublish && ready && !manualRejected)
                {
                    var goPublish = Ui.Button("质检合格 · 去免 API 直投 →", () =>
                    {
                        DateTime parsed;
                        if (DateTime.TryParseExact(day, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed))
                            publishingDate = parsed;
                        publishingPlatform = platform;
                        Navigate("publishing");
                    }, true);
                    goPublish.Margin = new Thickness(0, 0, 8, 4);
                    actions.Children.Add(goPublish);
                }
            }
            if (actions.Children.Count > 0) box.Children.Add(actions);
            return Ui.Card(box, ready && !manualRejected ? "SageBrush" : hasVideo ? "BlushBrush" : "SurfaceBrush");
        }

        private static Border QualityCheckChip(string name, string detail, bool ok)
        {
            var stack = new StackPanel();
            var top = Ui.Text(name, 11, true);
            var bottom = Ui.Text(detail, 13);
            bottom.FontWeight = FontWeights.SemiBold;
            bottom.Foreground = Ui.Color(ok ? "SageInkBrush" : "BlushInkBrush");
            stack.Children.Add(top);
            stack.Children.Add(bottom);
            return new Border
            {
                Child = stack,
                Background = Ui.Color("SurfaceBrush"),
                BorderBrush = Ui.Color("LineBrush"),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(7),
                Padding = new Thickness(10, 6, 10, 6),
                Margin = new Thickness(0, 0, 8, 6)
            };
        }

        private static int IntValue(IDictionary<string, object> row, string key)
        {
            int value;
            return Int32.TryParse(J.Text(row, key), out value) ? value : 0;
        }

        private async Task LoadFlowAsync(bool force)
        {
            if (flowLoading) return;
            flowLoading = true;
            if (currentPage == "flow" && force) RefreshFlowSurface();
            try
            {
                flowSnapshot = J.Map(await Worker.RunAsync("workspace_pipeline"));
                flowProblem = "";
            }
            catch (Exception error)
            {
                flowProblem = "工作台状态暂时无法读取：" + error.Message;
            }
            finally
            {
                flowLoading = false;
                if (currentPage == "flow") RefreshFlowSurface();
            }
        }

        private void RefreshFlowSurface()
        {
            var previous = pageHost.Content as ScrollViewer;
            double offset = previous == null ? 0 : previous.VerticalOffset;
            var current = (ScrollViewer)BuildFlow();
            if (offset > 0) current.Loaded += delegate { current.ScrollToVerticalOffset(offset); };
            pageHost.Content = current;
        }
    }
}
