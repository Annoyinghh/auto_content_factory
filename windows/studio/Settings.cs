using System;
using System.Collections.Generic;
using System.IO;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;

namespace Acf.Studio
{
    internal sealed partial class WorkbenchWindow
    {
        private FrameworkElement BuildSettings()
        {
            var page = new Grid();
            page.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            page.RowDefinitions.Add(new RowDefinition());
            page.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            var heading = new StackPanel { Margin = new Thickness(0, 0, 0, 22) };
            heading.Children.Add(Ui.Title("设置"));
            heading.Children.Add(Ui.Text("调到顺手的大小，按自己的节奏创作。", 14, true));
            page.Children.Add(heading);

            var tabs = new TabControl { Background = Brushes.Transparent };
            Grid.SetRow(tabs, 1);
            page.Children.Add(tabs);
            var windowSize = SettingsChoice("窗口尺寸", new[] { "紧凑 · 1040 × 720", "标准 · 1260 × 850", "宽敞 · 1440 × 900", "最大化" });
            var scale = SettingsChoice("界面缩放", new[] { "90% · 更多内容", "100% · 标准", "110% · 舒适", "125% · 放大" });
            var text = SettingsChoice("文字大小", new[] { "标准", "较大 · 110%", "大号 · 120%" });
            var resolution = SettingsChoice("视频清晰度", new[] { "720p · 轻量", "1080p · 推荐", "1440p · 高分辨率" });
            var remember = SettingsSwitch("记住窗口位置和大小");
            var close = SettingsSwitch("关闭窗口后继续后台运行");
            var minimize = SettingsSwitch("最小化时收进系统托盘");
            var refresh = SettingsSwitch("自动刷新热点");
            var feedback = Ui.Text(preferencesError ?? "设置保存在这台电脑上。", 12, preferencesError == null);
            feedback.TextWrapping = TextWrapping.Wrap;
            var sampleTitle = Ui.Text("让好内容，清楚地被看见。", 20);
            sampleTitle.FontWeight = FontWeights.SemiBold;
            var sampleBody = Ui.Text("这是一段正文预览。你可以找到适合自己的阅读大小。", 14, true);
            var pixels = Ui.Text("", 14);
            var qualityHint = Ui.Text("", 13, true);
            var dimensions = new[] { "compact", "comfortable", "spacious", "maximized" };
            var scales = new[] { 90, 100, 110, 125 };
            var fonts = new[] { 100, 110, 120 };
            var qualities = new[] { 720, 1080, 1440 };
            bool populating = true;
            bool resetPlacement = false;
            Action<StudioPreferences> populate = value =>
            {
                populating = true;
                windowSize.SelectedIndex = Array.IndexOf(dimensions, value.WindowSize);
                scale.SelectedIndex = Array.IndexOf(scales, value.InterfaceScale);
                text.SelectedIndex = Array.IndexOf(fonts, value.TextScale);
                resolution.SelectedIndex = Array.IndexOf(qualities, value.VideoResolution);
                remember.IsChecked = value.RememberWindow;
                close.IsChecked = value.CloseToTray;
                minimize.IsChecked = value.MinimizeToTray;
                refresh.IsChecked = value.AutoRefresh;
                populating = false;
            };
            populate(preferences);

            var display = new StackPanel();
            display.Children.Add(SettingsSection("显示与布局", "适配你的屏幕与阅读习惯。", "\uE7F4", "Honey",
                SettingsRow("窗口尺寸", "超出屏幕的部分会自动适配。", windowSize),
                SettingsRow("界面缩放", "同时调整按钮、间距和文字；放大幅度受屏幕大小限制。", scale),
                SettingsRow("文字大小", "单独放大文字，界面结构保持一致。", text),
                SettingsRow("记住窗口", "下次打开时恢复位置与大小。", remember)));
            var sample = new StackPanel { Margin = new Thickness(16) };
            sample.Children.Add(Ui.Text("文字效果预览", 12, true));
            sampleTitle.Margin = new Thickness(0, 10, 0, 7);
            sample.Children.Add(sampleTitle);
            sample.Children.Add(sampleBody);
            display.Children.Add(new Border { Child = sample, Background = Ui.Color("HoneyBrush"), CornerRadius = new CornerRadius(10), Margin = new Thickness(0, 14, 0, 0) });
            SettingsTab(tabs, "显示与文字", display);
            SettingsTab(tabs, "质检与群通知", BuildNotificationSettingsTab());

            var export = new StackPanel();
            export.Children.Add(SettingsSection("视频导出", "清晰度用于下一次生成和导出，已有作品保持原尺寸。", "\uE714", "Sky",
                SettingsRow("默认视频清晰度", "保留各平台的横竖屏比例，统一使用 30 帧／秒。", resolution)));
            var quality = new StackPanel { Margin = new Thickness(20) };
            quality.Children.Add(Ui.Text("实际导出尺寸", 12, true));
            pixels.Margin = new Thickness(0, 12, 0, 12);
            quality.Children.Add(pixels);
            quality.Children.Add(qualityHint);
            export.Children.Add(new Border { Child = quality, Background = Ui.Color("SkyBrush"), CornerRadius = new CornerRadius(10), Margin = new Thickness(0, 14, 0, 0) });
            export.Children.Add(Ui.Text("提高输出分辨率会增加生成时间与文件体积，原始素材的细节仍取决于素材本身。", 13, true));
            SettingsTab(tabs, "视频导出", export);

            var background = new StackPanel();
            background.Children.Add(SettingsSection("后台运行", "把工作台收起来，让已经开始的任务继续运行。", "\uE7F8", "Sage",
                SettingsRow("关闭窗口后留在后台", "点击 × 收进托盘，双击图标返回。默认关闭此功能。", close),
                SettingsRow("最小化到系统托盘", "开启后不占用任务栏位置。", minimize),
                SettingsRow("自动刷新热点", "程序运行时，每 10 分钟检查一次热点更新。", refresh)));
            var tip = Ui.Text("退出程序请使用托盘菜单中的「退出热采放映室」。后台运行需要电脑保持唤醒；它不会自动设置开机启动。", 13, true);
            tip.Margin = new Thickness(2, 16, 2, 0);
            background.Children.Add(tip);
            SettingsTab(tabs, "后台运行", background);

            var files = new StackPanel();
            files.Children.Add(SettingsSection("文件与应用", "作品、素材与偏好都保存在本机。", "\uE8B7", "Blush",
                SettingsRow("作品与素材", Path.Combine(Worker.DataRoot, "output"), Ui.Button("打开文件夹", () => OpenSettingsFolder(Path.Combine(Worker.DataRoot, "output")), false, "\uE8B7")),
                SettingsRow("应用数据", Worker.DataRoot, Ui.Button("打开数据目录", () => OpenSettingsFolder(Worker.DataRoot), false, "\uE8B7")),
                SettingsRow("热采放映室", "Windows 桌面版 · 本机内容工作台", Ui.Badge("本机偏好", "Sage"))));
            SettingsTab(tabs, "文件与关于", files);

            var footer = new Grid { Margin = new Thickness(0, 18, 0, 0) };
            footer.ColumnDefinitions.Add(new ColumnDefinition());
            footer.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            feedback.VerticalAlignment = VerticalAlignment.Center;
            feedback.Margin = new Thickness(0, 0, 14, 0);
            footer.Children.Add(feedback);
            var actions = new StackPanel { Orientation = Orientation.Horizontal };
            Grid.SetColumn(actions, 1);
            footer.Children.Add(actions);
            var save = Ui.Button("应用设置", () =>
            {
                var value = preferences.Copy();
                value.WindowSize = dimensions[windowSize.SelectedIndex];
                value.InterfaceScale = scales[scale.SelectedIndex];
                value.TextScale = fonts[text.SelectedIndex];
                value.VideoResolution = qualities[resolution.SelectedIndex];
                value.RememberWindow = remember.IsChecked == true;
                value.CloseToTray = close.IsChecked == true;
                value.MinimizeToTray = minimize.IsChecked == true;
                value.AutoRefresh = refresh.IsChecked == true;
                if (resetPlacement) { value.SavedWidth = value.SavedHeight = 0; value.SavedMaximized = false; }
                bool saved = CommitPreferences(value);
                feedback.Text = saved ? "已保存 · 视频设置从下次生成起生效" : "保存失败，请检查数据目录是否可写。";
                feedback.Foreground = Ui.Color(saved ? "SageInkBrush" : "BlushInkBrush");
                if (saved) resetPlacement = false;
            }, true, "\uE73E");
            Action refreshPreviews = () =>
            {
                double fontFactor = fonts[Math.Max(0, text.SelectedIndex)] / 100.0;
                sampleTitle.FontSize = 20 * fontFactor;
                sampleBody.FontSize = 14 * fontFactor;
                int shortSide = qualities[Math.Max(0, resolution.SelectedIndex)];
                int longSide = shortSide * 16 / 9;
                pixels.Text = "抖音 / 小红书   " + shortSide + " × " + longSide + "  ·  竖屏\n\nB 站   " + longSide + " × " + shortSide + "  ·  横屏";
                qualityHint.Text = shortSide == 720 ? "适合快速出片和体积较小的视频。" : shortSide == 1080 ? "兼顾细节、体积和生成速度，适合日常发布。" : "适合高分辨率原始素材，生成耗时与存储占用会增加。";
            };
            var reset = Ui.Button("恢复默认", () =>
            {
                populate(new StudioPreferences()); resetPlacement = true; refreshPreviews();
                feedback.Text = "已恢复默认选项，点击「应用设置」后保存。";
                feedback.Foreground = Ui.Color("MutedBrush");
            });
            reset.Style = (Style)FindResource("QuietButton");
            reset.Margin = new Thickness(0, 0, 8, 0);
            actions.Children.Add(reset);
            actions.Children.Add(save);
            Grid.SetRow(footer, 2);
            page.Children.Add(footer);
            Action changed = () =>
            {
                if (populating) return;
                refreshPreviews(); feedback.Text = "有未应用的更改"; feedback.Foreground = Ui.Color("HoneyInkBrush");
            };
            foreach (var choice in new[] { windowSize, scale, text, resolution }) choice.SelectionChanged += delegate { changed(); };
            foreach (var toggle in new[] { remember, close, minimize, refresh })
            { toggle.Checked += delegate { changed(); }; toggle.Unchecked += delegate { changed(); }; }
            refreshPreviews();
            if (settingsInitialTab > 0 && settingsInitialTab < tabs.Items.Count)
                tabs.SelectedIndex = settingsInitialTab;
            settingsInitialTab = 0;
            return page;
        }

        private FrameworkElement BuildNotificationSettingsTab()
        {
            var panel = new StackPanel();
            var statusBadge = Ui.Badge("正在读取通知配置…", "Honey");
            var maskedInfo = Ui.Text("", 12, true);
            maskedInfo.Margin = new Thickness(0, 6, 0, 10);

            var labelBox = new TextBox { MinWidth = 280, MaxLength = 60, Margin = new Thickness(0, 4, 0, 10) };
            AutomationProperties.SetName(labelBox, "群通知渠道名称");
            var urlBox = new PasswordBox { MaxLength = 1024, Margin = new Thickness(0, 4, 0, 8) };
            AutomationProperties.SetName(urlBox, "群机器人 Webhook HTTPS 地址");
            var enabledSwitch = SettingsSwitch("成片质检未通过或人工退回时自动推送群通知");
            enabledSwitch.IsChecked = true;

            var cardBody = new StackPanel();
            var topBar = new DockPanel { Margin = new Thickness(0, 0, 0, 8) };
            topBar.Children.Add(statusBadge);
            cardBody.Children.Add(topBar);
            cardBody.Children.Add(EditorialStrong("飞书 / 企业微信 / 钉钉群机器人 Webhook（免平台开放 API）", 16));
            var desc = Ui.Text("在飞书群、企业微信群或钉钉群添加一个「自定义机器人」，把它的 HTTPS Webhook 地址粘贴在下方即可。成片自动质检不过关或被标记不合适时，群里会立即收到原因提醒，无需申请任何开发者 API。", 13, true);
            desc.Margin = new Thickness(0, 4, 0, 8);
            cardBody.Children.Add(desc);
            cardBody.Children.Add(maskedInfo);
            cardBody.Children.Add(Ui.Text("渠道备注名称（如：内容组飞书群）", 13, true));
            cardBody.Children.Add(labelBox);
            cardBody.Children.Add(Ui.Text("群机器人 Webhook 地址（HTTPS，留空不修改已存地址）", 13, true));
            cardBody.Children.Add(urlBox);
            cardBody.Children.Add(SettingsRow("启用自动群推送", "关闭后仅保留工作台内的待改拦截清单。", enabledSwitch));

            var msg = Ui.Text("", 13);
            msg.Margin = new Thickness(0, 8, 0, 8);
            cardBody.Children.Add(msg);

            string savedUrlPresent = "";
            Action<IDictionary<string, object>> applyState = info =>
            {
                bool configured = Object.Equals(J.Get(info, "configured"), true);
                bool on = Object.Equals(J.Get(info, "enabled"), true);
                savedUrlPresent = configured ? J.Text(info, "masked_url") : "";
                var badgeText = (TextBlock)statusBadge.Child;
                badgeText.Text = configured ? (on ? "✓ 已启用 · " + J.Text(info, "label") : "已暂停 · " + J.Text(info, "label")) : "尚未配置群机器人";
                maskedInfo.Text = configured ? "当前已加密保存地址：" + savedUrlPresent + "（自动识别为 " + J.Text(info, "kind_name") + "）"
                    : "尚未保存群机器人地址。支持 open.feishu.cn、qyapi.weixin.qq.com、oapi.dingtalk.com 及标准 JSON Webhook。";
                if (configured && String.IsNullOrWhiteSpace(labelBox.Text))
                    labelBox.Text = J.Text(info, "label");
                enabledSwitch.IsChecked = !configured || on;
            };

            var buttons = new WrapPanel { Margin = new Thickness(0, 4, 0, 0) };
            buttons.Children.Add(Ui.Button("保存群通知设置", async () =>
            {
                string typed = urlBox.Password.Trim();
                if (typed.Length == 0 && savedUrlPresent.Length == 0)
                {
                    msg.Foreground = Ui.Color("BlushInkBrush");
                    msg.Text = "请粘贴群机器人的 HTTPS Webhook 地址。";
                    return;
                }
                if (typed.Length == 0)
                {
                    msg.Foreground = Ui.Color("HoneyInkBrush");
                    msg.Text = "如需更换或修改启用状态，请重新粘贴一次完整 Webhook 地址以加密保存。";
                    return;
                }
                try
                {
                    string sealedUrl = WorkerBridge.Seal(J.Obj("url", typed));
                    var updated = J.Map(await Worker.RunAsync("workspace_webhook_save", payload: J.Obj(
                        "sealed", sealedUrl, "enabled", enabledSwitch.IsChecked == true, "label", labelBox.Text.Trim())));
                    urlBox.Clear();
                    applyState(updated);
                    msg.Foreground = Ui.Color("SageInkBrush");
                    msg.Text = "群机器人通知已加密保存！可点右侧「发送测试消息」验证群内是否收到。";
                    Notify("群机器人通知已保存。");
                }
                catch (Exception error)
                {
                    msg.Foreground = Ui.Color("BlushInkBrush");
                    msg.Text = error.Message;
                }
            }, true));
            var testBtn = Ui.Button("发送测试消息到群里", async () =>
            {
                msg.Foreground = Ui.Color("MutedBrush");
                msg.Text = "正在向群机器人发送测试消息…";
                try
                {
                    var payload = J.Obj();
                    if (urlBox.Password.Trim().Length > 0)
                        payload["sealed"] = WorkerBridge.Seal(J.Obj("url", urlBox.Password.Trim()));
                    await Worker.RunAsync("workspace_webhook_test", payload: payload);
                    msg.Foreground = Ui.Color("SageInkBrush");
                    msg.Text = "✓ 测试消息已成功送达！请到你的飞书／企业微信／钉钉群查看。";
                    Notify("测试通知发送成功！");
                }
                catch (Exception error)
                {
                    msg.Foreground = Ui.Color("BlushInkBrush");
                    msg.Text = error.Message;
                }
            });
            testBtn.Margin = new Thickness(8, 0, 8, 0);
            buttons.Children.Add(testBtn);
            buttons.Children.Add(Ui.Filter("清空关闭群通知", async () =>
            {
                try
                {
                    var cleared = J.Map(await Worker.RunAsync("workspace_webhook_save", payload: J.Obj("clear", true)));
                    urlBox.Clear();
                    labelBox.Clear();
                    applyState(cleared);
                    msg.Foreground = Ui.Color("MutedBrush");
                    msg.Text = "已关闭群机器人推送，保留本机质检拦截。";
                }
                catch (Exception error) { msg.Text = error.Message; }
            }));
            cardBody.Children.Add(buttons);

            var mainCard = Ui.Card(cardBody);
            mainCard.Margin = new Thickness(0, 0, 0, 14);
            panel.Children.Add(mainCard);

            var teamCard = new StackPanel();
            teamCard.Children.Add(EditorialStrong("团队协作空间同步（可选）", 15));
            teamCard.Children.Add(Ui.Text("如果团队已部署 Cloudflare 共享运营或团队审片服务，质检退回时也会自动同步发布状态或上传问题成片。", 12, true));
            var teamBtns = new WrapPanel { Margin = new Thickness(0, 8, 0, 0) };
            var opsBtn = Ui.Button("设置共享运营连接", async () => await TeamConnection(false, true));
            opsBtn.Margin = new Thickness(0, 0, 8, 0);
            teamBtns.Children.Add(opsBtn);
            teamBtns.Children.Add(Ui.Button("设置团队审片连接", async () => await TeamConnection(true, true)));
            teamCard.Children.Add(teamBtns);
            panel.Children.Add(Ui.Card(teamCard, "SageBrush"));

            panel.Loaded += async delegate
            {
                try { applyState(J.Map(await Worker.RunAsync("workspace_webhook_get"))); }
                catch (Exception error) { maskedInfo.Text = error.Message; }
            };
            return panel;
        }

        private static ComboBox SettingsChoice(string name, string[] choices)
        {
            var control = new ComboBox { ItemsSource = choices, MinWidth = 205, MinHeight = 38 };
            AutomationProperties.SetName(control, name);
            return control;
        }

        private CheckBox SettingsSwitch(string name)
        {
            var control = new CheckBox { Style = (Style)FindResource("SettingsSwitch"), VerticalAlignment = VerticalAlignment.Center };
            AutomationProperties.SetName(control, name);
            return control;
        }

        private static Border SettingsRow(string title, string description, FrameworkElement control)
        {
            var grid = new Grid { Margin = new Thickness(0, 15, 0, 15) };
            grid.ColumnDefinitions.Add(new ColumnDefinition());
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            var labels = new StackPanel { Margin = new Thickness(0, 0, 24, 0), VerticalAlignment = VerticalAlignment.Center };
            var name = Ui.Text(title, 14); name.FontWeight = FontWeights.SemiBold;
            labels.Children.Add(name);
            var hint = Ui.Text(description, 12, true); hint.Margin = new Thickness(0, 5, 0, 0);
            labels.Children.Add(hint);
            grid.Children.Add(labels);
            Grid.SetColumn(control, 1); control.VerticalAlignment = VerticalAlignment.Center;
            grid.Children.Add(control);
            return new Border { Child = grid, BorderBrush = Ui.Color("LineBrush"), BorderThickness = new Thickness(0, 0, 0, 1) };
        }

        private static Border SettingsSection(string title, string description, string glyph, string tone, params UIElement[] rows)
        {
            var content = new StackPanel();
            var header = new DockPanel { Margin = new Thickness(0, 0, 0, 8) };
            var icon = new TextBlock { Text = glyph, FontFamily = new FontFamily("Segoe MDL2 Assets"), FontSize = 20,
                Foreground = Ui.Color(tone + "InkBrush"), VerticalAlignment = VerticalAlignment.Center, HorizontalAlignment = HorizontalAlignment.Center };
            header.Children.Add(new Border { Child = icon, Width = 42, Height = 42, CornerRadius = new CornerRadius(10),
                Background = Ui.Color(tone + "Brush"), Margin = new Thickness(0, 0, 12, 0) });
            var labels = new StackPanel();
            var name = Ui.Text(title, 18); name.FontWeight = FontWeights.SemiBold;
            labels.Children.Add(name); labels.Children.Add(Ui.Text(description, 12, true));
            header.Children.Add(labels); content.Children.Add(header);
            foreach (var row in rows) content.Children.Add(row);
            return Ui.Card(content);
        }

        private static void SettingsTab(TabControl tabs, string title, UIElement content)
        {
            var scroll = Ui.Scroll(content); scroll.Padding = new Thickness(0, 14, 5, 0);
            var tab = new TabItem { Header = title, Content = scroll };
            AutomationProperties.SetName(tab, title); tabs.Items.Add(tab);
        }
    }
}
