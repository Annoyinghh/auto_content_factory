using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;
using Microsoft.Win32;

namespace Acf.Studio
{
    internal sealed partial class WorkbenchWindow
    {
        private string productionPlatform = "xiaohongshu", productionDay;
        private readonly Dictionary<string, string> productionJobs = new Dictionary<string, string>();
        private event Action<string, string> ProductionChanged;
        private bool dailyCompilationBusy;
        private string dailyCompilationStatus = "", dailyCompilationDay;
        private event Action DailyCompilationChanged;

        private void OpenProduction(string platform, string day = null)
        {
            if (Array.IndexOf(EditorialPlatforms, platform) >= 0) productionPlatform = platform;
            productionDay = day ?? EditorialToday();
            Navigate("production");
        }

        private FrameworkElement BuildProduction()
        {
            var layout = new DockPanel();
            var pinned = new StackPanel { Margin = new Thickness(0, 0, 0, 12) };
            var navigation = new DockPanel();
            var switchWork = Ui.Button("切换作品", OpenProductionHistory, glyph: "\uE8F1");
            switchWork.Margin = new Thickness(0, 0, 12, 0);
            DockPanel.SetDock(switchWork, Dock.Left); navigation.Children.Add(switchWork);
            var library = Ui.Button("作品库", () => Navigate("works"));
            DockPanel.SetDock(library, Dock.Right); navigation.Children.Add(library);
            var currentTitle = Ui.Text("当前创作", 14);
            AutomationProperties.SetName(currentTitle, "当前创作作品");
            currentTitle.VerticalAlignment = VerticalAlignment.Center;
            currentTitle.TextWrapping = TextWrapping.NoWrap;
            currentTitle.TextTrimming = TextTrimming.CharacterEllipsis;
            navigation.Children.Add(currentTitle); pinned.Children.Add(navigation);
            DockPanel.SetDock(pinned, Dock.Top); layout.Children.Add(pinned);
            var page = new StackPanel();
            var tools = EditorialBar();
            var platform = EditorialChoice(EditorialPlatformNames, Array.IndexOf(EditorialPlatforms, productionPlatform), "制作平台");
            DateTime selected;
            if (!DateTime.TryParseExact(productionDay, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out selected))
                selected = DateTime.Today;
            var date = new DatePicker { SelectedDate = selected, MinWidth = 156, Margin = new Thickness(0, 2, 8, 2) };
            System.Windows.Automation.AutomationProperties.SetName(date, "选择创作日期");
            IDictionary<string, object> materialTarget = null;
            var heading = new StackPanel();
            var introduction = Ui.Text("按三步完成制作：① 先通过画面素材审核 → ② 让本地 AI 生成并修改每镜口播 → ③ 拼试剪并导出成片（自动质检）。", 13, true);
            introduction.Margin = new Thickness(0, 0, 0, 8);
            heading.Children.Add(introduction);
            var launchers = EditorialBar();
            launchers.Children.Add(Ui.Filter("粘贴文案新建视频", () => OpenQuickScriptDialog(initialPlatform: productionPlatform)));
            var find = Ui.Filter("搜索公共素材", () => OpenMaterialSearch(EditorialPlatforms[Math.Max(0, platform.SelectedIndex)],
                (date.SelectedDate ?? DateTime.Today).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture), materialTarget));
            launchers.Children.Add(find);
            launchers.Children.Add(Ui.Filter("图文写稿台", () => OpenWriting(platform: productionPlatform)));
            launchers.Children.Add(Ui.Filter("爆款参考库", () => OpenVideoCatalog(productionPlatform)));
            heading.Children.Add(launchers);
            var header = Ui.Card(heading, "SkyBrush");
            header.Margin = new Thickness(0, 0, 0, 12);
            page.Children.Add(header);
            tools.Children.Add(platform);
            tools.Children.Add(date);
            var compute = EditorialChoice(new[] { "显卡优先", "CPU" }, Worker.CpuOnly ? 1 : 0, "计算方式");
            compute.SelectionChanged += (sender, args) =>
            {
                bool cpuOnly = compute.SelectedIndex == 1;
                if (cpuOnly == Worker.CpuOnly) return;
                try
                {
                    Worker.SetCpuOnly(cpuOnly);
                    Notify(cpuOnly ? "后续任务使用 CPU" : "后续任务优先使用显卡");
                }
                catch (Exception error)
                {
                    compute.SelectedIndex = Worker.CpuOnly ? 1 : 0;
                    Notify(error.Message);
                }
            };
            tools.Children.Add(compute);
            var refresh = Ui.Button("刷新", delegate { }, glyph: "\uE72C");
            refresh.Margin = new Thickness(0, 2, 8, 2);
            tools.Children.Add(refresh);
            page.Children.Add(tools);
            var status = Ui.Text("正在读取工作稿…", 13, true);
            AutomationProperties.SetName(status, "创作任务状态");
            status.Margin = new Thickness(0, 8, 0, 10);
            pinned.Children.Add(status);
            Action<string> setStatus = text =>
            {
                status.Text = text;
                status.Visibility = text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
            };
            var detail = new StackPanel();
            detail.Margin = new Thickness(0, 12, 0, 0);
            page.Children.Add(detail);
            int request = 0;
            bool shownBusy = false;
            string shownVersion = "";
            bool autoSwitchedOnce = productionDay != null;
            Func<bool, Task> read = null;
            read = async progressOnly =>
            {
                if (platform.SelectedIndex < 0 || !date.SelectedDate.HasValue) return;
                string target = productionPlatform = EditorialPlatforms[platform.SelectedIndex];
                string day = productionDay = date.SelectedDate.Value.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
                int token = progressOnly ? request : ++request;
                try
                {
                    string job;
                    if (!progressOnly)
                        setStatus(productionJobs.TryGetValue(ProductionKey(target, day), out job) ? job
                            : dailyCompilationBusy && day == dailyCompilationDay ? dailyCompilationStatus : "正在读取工作稿…");
                    var state = J.Map(await Worker.RunAsync("workspace_creation_state", target, day: day));
                    if (request != token) return;
                    var item = J.Map(J.Get(state, "item"));
                    var recentRows = J.Rows(J.Get(state, "recent")).ToArray();
                    if (!autoSwitchedOnce && item.Count == 0 && recentRows.Length > 0)
                    {
                        autoSwitchedOnce = true;
                        var latest = recentRows[0];
                        DateTime latestDate;
                        int latestIdx = Array.IndexOf(EditorialPlatforms, J.Text(latest, "platform"));
                        if (latestIdx >= 0 && DateTime.TryParseExact(J.Text(latest, "day"), "yyyy-MM-dd",
                            CultureInfo.InvariantCulture, DateTimeStyles.None, out latestDate))
                        {
                            platform.SelectedIndex = latestIdx;
                            date.SelectedDate = latestDate;
                            Notify("已自动定位到你最近编辑的作品：" + J.Text(latest, "title"));
                            return;
                        }
                    }
                    autoSwitchedOnce = true;
                    detail.IsEnabled = true;
                    var inspection = J.Map(J.Get(state, "inspection"));
                    bool busy = productionJobs.TryGetValue(ProductionKey(target, day), out job);
                    if (!busy && dailyCompilationBusy && day == dailyCompilationDay)
                    {
                        busy = true;
                        job = dailyCompilationStatus;
                    }
                    if (!busy && Object.Equals(J.Get(state, "busy"), true))
                    {
                        busy = true;
                        job = J.Text(state, "message");
                    }
                    string stage = J.Text(item, "editorial_progress");
                    if (!busy && productionBlocked.Remove(ProductionKey(target, day)))
                    {
                        productionFeedback[ProductionKey(target, day)] = "后台已空闲，可以继续操作。";
                        Notify("后台已空闲，可以继续操作。");
                    }
                    string feedback;
                    productionFeedback.TryGetValue(ProductionKey(target, day), out feedback);
                    setStatus(busy ? job + (stage.Length == 0 ? "" : "  ·  " + stage)
                        : !String.IsNullOrEmpty(feedback) ? feedback : shownBusy ? "后台任务已结束，可以继续操作。" : "");
                    string storyTitle = J.Text(J.Map(J.Get(item, "story")), "title");
                    currentTitle.Text = EditorialPlatformName(target) + " · " + day + " · " +
                        (storyTitle.Length > 0 ? storyTitle : "当天尚无工作稿");
                    currentTitle.ToolTip = currentTitle.Text;
                    string version = String.Join("/", new[] { "id", "edit_revision", "suggestion_revision", "full_preview_revision", "package_revision",
                        J.Text(J.Map(J.Get(inspection, "review")), "updated_at"), J.Text(item, "held_video") }
                        .Select(field => field ?? ""));
                    if (progressOnly && busy == shownBusy && version == shownVersion) return;
                    shownBusy = busy;
                    shownVersion = version;
                    materialTarget = item;
                    detail.Children.Clear();
                    detail.Children.Add(ProductionItem(item, target, day, busy, inspection, recentRows, () => read(false)));
                }
                catch (Exception error)
                {
                    if (request != token) return;
                    detail.IsEnabled = false;
                    setStatus("暂时无法确认作品状态：" + error.Message + " 可点刷新重试，或切换其他作品。");
                }
            };
            Action<string, string> update = async (target, day) =>
            {
                if (productionPlatform == target && productionDay == day) await read(false);
            };
            var progress = new DispatcherTimer { Interval = TimeSpan.FromSeconds(8) };
            bool polling = false;
            progress.Tick += async (sender, args) =>
            {
                if (polling || !page.IsLoaded || (!shownBusy && !preferences.AutoRefresh)) return;
                polling = true;
                try { await read(true); }
                finally { polling = false; }
            };
            page.Loaded += async (sender, args) => { ProductionChanged += update; progress.Start(); await read(false); };
            page.Unloaded += (sender, args) => { ProductionChanged -= update; progress.Stop(); request++; };
            platform.SelectionChanged += async (sender, args) => { materialTarget = null; detail.Children.Clear(); await read(false); };
            date.SelectedDateChanged += async (sender, args) => { materialTarget = null; detail.Children.Clear(); await read(false); };
            refresh.Click += async (sender, args) => await read(false);
            var automatic = Ui.Button("自动合辑", () => OpenProductionAutomation(async () =>
            {
                if (!page.IsLoaded) return;
                if (date.SelectedDate != DateTime.Today) date.SelectedDate = DateTime.Today;
                else await read(false);
            }));
            automatic.Margin = new Thickness(0, 2, 0, 2);
            tools.Children.Add(automatic);
            var scroll = Ui.Scroll(page);
            scroll.FocusVisualStyle = null;
            layout.Children.Add(scroll);
            return layout;
        }

        private async Task<IDictionary<string, object>> LoadVideoCatalog()
        {
            return J.Map(await Worker.RunAsync("cases_video"));
        }

        private async void OpenVideoCatalog(string initial)
        {
            var dialog = new Window { Title = "创作参考库", Owner = this, Width = 740, Height = 680,
                MinWidth = 540, MinHeight = 450, WindowStartupLocation = WindowStartupLocation.CenterOwner,
                Background = Brushes.White };
            var body = new StackPanel { Margin = new Thickness(22) };
            dialog.Content = Ui.Scroll(body);
            body.Children.Add(EditorialHeading("创作参考库", "按平台查看案例、制作方法和所需素材。"));
            var tabs = new TabControl();
            var status = Ui.Text("正在读取创作类型…", 13, true);
            body.Children.Add(status);
            body.Children.Add(tabs);
            dialog.Show();
            try
            {
                var catalog = await LoadVideoCatalog();
                foreach (string target in new[] { "douyin", "bilibili", "xiaohongshu_notes" })
                {
                    var list = new StackPanel { Margin = new Thickness(2, 8, 2, 0) };
                    foreach (var style in J.Rows(J.Get(catalog, target)))
                    {
                        var card = new StackPanel();
                        var reference = J.Map(J.Get(style, "reference"));
                        card.Children.Add(EditorialStrong(J.Text(style, "name"), 18));
                        foreach (var field in new[] {
                            new[] { "素材", "materials" }, new[] { "开场", "opening" },
                            new[] { "中段", "body" }, new[] { "结尾", "closing" }
                        })
                            card.Children.Add(Ui.Text(field[0] + " · " + J.Text(style, field[1]), 14));
                        card.Children.Add(Ui.Text(J.Text(reference, "evidence"), 13, true));
                        card.Children.Add(Ui.Text("可借鉴 · " + J.Text(reference, "adapt"), 13, true));
                        card.Children.Add(target == "xiaohongshu_notes"
                            ? EditorialLink("核对官方专题", J.Text(reference, "url"))
                            : VideoReferenceLink(reference));
                        list.Children.Add(EditorialRow(card));
                    }
                    tabs.Items.Add(new TabItem { Header = target == "xiaohongshu_notes" ? "小红书图文" : EditorialPlatformName(target),
                        Content = list });
                }
                tabs.SelectedIndex = initial == "xiaohongshu" ? 2 : initial == "bilibili" ? 1 : 0;
                status.Visibility = Visibility.Collapsed;
            }
            catch (Exception error) { status.Text = error.Message; }
        }

        private Button VideoReferenceLink(IDictionary<string, object> reference)
        {
            string url = J.Text(reference, "url");
            string at = J.Text(reference, "at");
            if (url.StartsWith("https://www.bilibili.com/video/", StringComparison.Ordinal) && at != "0")
                url += "?t=" + at;
            return EditorialLink("原作 · " + J.Text(reference, "title"), url);
        }

        private void OpenProductionAutomation(Func<Task> showToday)
        {
            var dialog = new Window { Title = "自动合辑", Owner = this, Width = 555, SizeToContent = SizeToContent.Height,
                ResizeMode = ResizeMode.NoResize, WindowStartupLocation = WindowStartupLocation.CenterOwner, Background = Brushes.White };
            var body = new StackPanel { Margin = new Thickness(22) };
            dialog.Content = body;
            body.Children.Add(EditorialHeading("自动合辑", "按各平台热点制作合辑，成片进入作品库等待审片。"));

            var current = new StackPanel();
            current.Children.Add(EditorialStrong("今日合辑", 17));
            current.Children.Add(Ui.Text("每个平台至少需要 3 条有正文的热点报道。", 13, true));
            var make = new Button { Content = "制作今日合辑", Style = (Style)FindResource("PrimaryButton"),
                HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 8, 0, 0) };
            current.Children.Add(make);
            var compilation = Ui.Text("", 13, true);
            compilation.Margin = new Thickness(0, 6, 0, 0);
            current.Children.Add(compilation);
            body.Children.Add(EditorialRow(current));

            var daily = new StackPanel();
            daily.Children.Add(EditorialStrong("每日 08:30", 17));
            daily.Children.Add(Ui.Text("Windows 在本机时间 08:30、当前用户已登录时运行制作任务。", 13, true));
            var state = Ui.Text("正在检查 Windows 计划…", 13, true);
            state.Margin = new Thickness(0, 6, 0, 3);
            daily.Children.Add(state);
            var actions = EditorialBar();
            var toggle = new Button { Content = "每日制作设置", IsEnabled = false, Margin = new Thickness(0, 0, 6, 0) };
            var refresh = new Button { Content = "刷新状态" };
            actions.Children.Add(toggle);
            actions.Children.Add(refresh);
            daily.Children.Add(actions);
            body.Children.Add(EditorialRow(daily));
            bool enabled = false;
            Action<IDictionary<string, object>> display = result =>
            {
                enabled = Equals(J.Get(result, "enabled"), true);
                bool exists = Equals(J.Get(result, "exists"), true);
                state.Text = enabled ? "每日制作已开启。" : exists ? "Windows 任务已暂停。" : "每日制作未开启。";
                toggle.Content = enabled ? "关闭每日制作" : "开启每日制作";
                toggle.IsEnabled = true;
            };
            Func<Task> check = async () =>
            {
                refresh.IsEnabled = toggle.IsEnabled = false;
                try { display(J.Map(await Worker.RunAsync("schedule_status"))); }
                catch (Exception error) { state.Text = error.Message; }
                finally { refresh.IsEnabled = true; }
            };
            toggle.Click += async (sender, args) =>
            {
                bool target = !enabled;
                string title = target ? "开启每日制作" : "关闭每日制作";
                string prompt = target ? "启用 Windows 每日 08:30 自动制作？成片将保留在本机等待审片。"
                    : "移除 Windows 每日 08:30 制作任务？";
                if (MessageBox.Show(dialog, prompt, title, MessageBoxButton.YesNo, MessageBoxImage.Question, MessageBoxResult.No)
                    != MessageBoxResult.Yes) return;
                toggle.IsEnabled = refresh.IsEnabled = false;
                state.Text = "正在更新 Windows 计划…";
                string failure = null;
                try { display(J.Map(await Worker.RunAsync("schedule_set", payload: J.Obj("enabled", target)))); }
                catch (Exception error) { failure = error.Message; }
                if (failure != null)
                {
                    await check();
                    state.Text = failure;
                }
                refresh.IsEnabled = true;
            };
            Action update = () =>
            {
                make.IsEnabled = !dailyCompilationBusy;
                compilation.Text = dailyCompilationStatus;
                compilation.Visibility = dailyCompilationStatus.Length == 0 ? Visibility.Collapsed : Visibility.Visible;
            };
            make.Click += async (sender, args) =>
            {
                if (dailyCompilationBusy) return;
                if (productionJobs.Keys.Any(key => key.StartsWith(EditorialToday() + "/", StringComparison.Ordinal)))
                {
                    compilation.Text = "今天有工作稿正在处理，完成后可启动合辑。";
                    compilation.Visibility = Visibility.Visible;
                    return;
                }
                dialog.Close();
                await ProduceToday(showToday);
            };
            DailyCompilationChanged += update;
            dialog.Closed += (sender, args) => DailyCompilationChanged -= update;
            dialog.Loaded += async (sender, args) => await check();
            refresh.Click += async (sender, args) => await check();
            update();
            dialog.ShowDialog();
        }

        private async Task ProduceToday(Func<Task> showToday)
        {
            string day = dailyCompilationDay = EditorialToday();
            dailyCompilationBusy = true;
            dailyCompilationStatus = "正在采集热点、生成分镜并制作今日合辑…";
            Notify(dailyCompilationStatus);
            if (DailyCompilationChanged != null) DailyCompilationChanged();
            try
            {
                await showToday();
                var result = J.Map(await Worker.RunAsync("schedule_run_today"));
                string finishedDay = J.Text(result, "date");
                if (finishedDay.Length > 0) day = finishedDay;
                dailyCompilationStatus = J.Text(result, "total") == "0" ? "今日尚无足够报道制作合辑。"
                    : "今日合辑  ·  " + J.Text(result, "ready") + "/" + J.Text(result, "total") + " 集已成片";
                string errors = String.Join("  ·  ", J.Items(J.Get(result, "errors")));
                if (errors.Length > 0) dailyCompilationStatus += "  ·  " + errors;
            }
            catch (Exception error) { dailyCompilationStatus = "今日合辑未完成  ·  " + error.Message; }
            finally
            {
                dailyCompilationBusy = false;
                Notify(dailyCompilationStatus);
                if (DailyCompilationChanged != null) DailyCompilationChanged();
                if (ProductionChanged != null)
                    foreach (string target in EditorialPlatforms) ProductionChanged(target, day);
            }
        }

        private FrameworkElement ProductionItem(IDictionary<string, object> item, string platform, string day, bool busy,
            IDictionary<string, object> inspection, IDictionary<string, object>[] recentRows, Func<Task> refreshHost)
        {
            var body = new StackPanel();
            var tools = EditorialBar();
            if (item.Count == 0)
            {
                body.Children.Add(EditorialStrong("所选日期（" + day + " · " + EditorialPlatformName(platform) + "）暂无工作稿", 20));
                var desc = Ui.Text("你可以从热点/报道开始新选题，或者直接把写好的文案贴给本地 AI 分镜。", 14, true);
                desc.Margin = new Thickness(0, 6, 0, 10);
                body.Children.Add(desc);
                var goTopic = Ui.Button("去挑热点或报道 →", () => { assembly = null; Navigate("topics"); }, true);
                goTopic.Margin = new Thickness(0, 0, 8, 6);
                tools.Children.Add(goTopic);
                var pasteBtn = Ui.Button("粘贴自有文案做视频", () => OpenQuickScriptDialog(initialPlatform: platform));
                pasteBtn.Margin = new Thickness(0, 0, 8, 6);
                tools.Children.Add(pasteBtn);
                var manual = Ui.Button("手动空白建镜", () => OpenCutEditor(platform, day, item, J.Obj("scenes", new[] {
                    J.Obj("title", "开场", "text", "", "media", "", "zoom", false),
                    J.Obj("title", "正文", "text", "", "media", "", "zoom", false)
                }), 0, false, true));
                manual.IsEnabled = !busy;
                manual.Margin = new Thickness(0, 0, 8, 6);
                tools.Children.Add(manual);
                body.Children.Add(tools);
                if (recentRows != null && recentRows.Length > 0)
                {
                    var recentHeader = EditorialStrong("或者直接继续你最近保存的作品：", 15);
                    recentHeader.Margin = new Thickness(0, 14, 0, 6);
                    body.Children.Add(recentHeader);
                    var recentBar = new WrapPanel();
                    foreach (var r in recentRows)
                    {
                        string rPlatform = J.Text(r, "platform");
                        string rDay = J.Text(r, "day");
                        var chip = Ui.Filter(EditorialPlatformName(rPlatform) + " · " + rDay + " · " + J.Text(r, "title"),
                            () => OpenProduction(rPlatform, rDay));
                        chip.Margin = new Thickness(0, 0, 6, 6);
                        recentBar.Children.Add(chip);
                    }
                    body.Children.Add(recentBar);
                }
                return Ui.Card(body);
            }

            var editorial = J.Map(J.Get(item, "editorial"));
            var edit = J.Map(J.Get(item, "edit"));
            var suggestion = J.Map(J.Get(item, "suggestion"));
            var draft = edit.Count > 0 ? edit : suggestion;
            string title = J.Text(J.Map(J.Get(item, "story")), "title");
            var package = J.Map(J.Get(item, "package"));
            string packageVideo = J.Text(package, "video");
            bool hasVideoFile = packageVideo.Length > 0 && File.Exists(packageVideo);
            string editRevision = J.Text(item, "edit_revision");
            bool currentPackage = hasVideoFile && J.Text(item, "package_revision") == editRevision;
            bool multi = J.Rows(J.Get(item, "chapters")).Any();
            bool isEditorial = editorial.Count > 0;
            var topicJob = J.Map(J.Get(item, "topic_job"));
            bool isTopicJob = topicJob.Count > 0;
            bool topicComplete = J.Text(topicJob, "stage") == "complete";
            string topicAction = topicComplete ? "重新制作整片" : J.Text(topicJob, "error").Length > 0 ? "重试制作整片" : "继续制作整片";
            var itemId = J.Obj("id", J.Get(item, "id"));
            int seconds;
            if (!Int32.TryParse(J.Text(item, "target_seconds"), out seconds)) seconds = isEditorial || multi ? 720 : 120;

            var summaryCard = new StackPanel();
            var topBadges = new WrapPanel();
            var platBadge = Ui.Badge(EditorialPlatformName(platform) + " · " + day, "Sky");
            platBadge.Margin = new Thickness(0, 0, 8, 4);
            topBadges.Children.Add(platBadge);
            var draftState = Ui.Badge(currentPackage ? "成片已导出" : edit.Count > 0 ? "分镜草稿已保存" :
                draft.Count > 0 ? "AI 候选分镜待保存" : "待生成分镜", currentPackage ? "Sage" : "Honey");
            draftState.Margin = new Thickness(0, 0, 8, 4);
            topBadges.Children.Add(draftState);
            var videoStyle = J.Map(J.Get(item, "video_style"));
            if (videoStyle.Count > 0)
            {
                var styleBadge = Ui.Badge("风格 · " + J.Text(videoStyle, "name"), "Sage");
                styleBadge.Margin = new Thickness(0, 0, 8, 4);
                topBadges.Children.Add(styleBadge);
            }
            summaryCard.Children.Add(topBadges);
            summaryCard.Children.Add(EditorialStrong(title, 22));
            string progress = J.Text(item, "editorial_progress");
            if (!busy && progress.Length > 0) summaryCard.Children.Add(Ui.Text("上次任务：" + progress, 13, true));
            string errorText = J.Text(item, "error");
            if (errorText.Length == 0 && isTopicJob && !topicComplete) errorText = J.Text(topicJob, "error");
            if (!busy && errorText.Length > 0)
            {
                var errLine = Ui.Text("提示：" + errorText, 13);
                errLine.Foreground = Ui.Color("BlushInkBrush");
                summaryCard.Children.Add(errLine);
            }
            var warnings = J.Items(J.Get(item, "warnings")).Concat(J.Items(J.Get(topicJob, "warnings")))
                .Select(value => Convert.ToString(value)).Where(value => !String.IsNullOrWhiteSpace(value)).Distinct().ToList();
            if (!busy && warnings.Count > 0) summaryCard.Children.Add(Ui.Text(String.Join(" · ", warnings), 13, true));
            var writingReference = J.Map(J.Get(topicJob, "reference"));
            if (writingReference.Count == 0)
                writingReference = J.Rows(J.Get(draft, "chapter_starts")).Select(row => J.Map(J.Get(row, "style_reference")))
                    .FirstOrDefault(row => row.Count > 0) ?? writingReference;
            if (writingReference.Count > 0)
            {
                summaryCard.Children.Add(Ui.Text(J.Text(writingReference, "match_kind") + " · " + J.Text(writingReference, "title"), 14));
                summaryCard.Children.Add(Ui.Text(J.Text(writingReference, "evidence"), 12, true));
                summaryCard.Children.Add(Ui.Text("借鉴写法 · " + J.Text(writingReference, "notes"), 13, true));
                if (J.Text(writingReference, "url").Length > 0)
                    summaryCard.Children.Add(EditorialLink("查看参考原作", J.Text(writingReference, "url")));
            }
            var headCard = Ui.Card(summaryCard);
            headCard.Margin = new Thickness(0, 0, 0, 12);
            body.Children.Add(headCard);

            // STEP 1: Media Review (placed FIRST so users approve assets before AI directing!)
            body.Children.Add(ProductionMedia(item, platform, day, busy, multi, isEditorial, refreshHost));

            // STEP 2: AI Storyboard & Shot-by-Shot Script Editing
            var step2 = new StackPanel();
            step2.Children.Add(EditorialStrong(isTopicJob ? "第二步 · 制作整片与逐镜修改" : "第二步 · 本地 AI 编排分镜与逐镜修改", 17));
            step2.Children.Add(Ui.Text(isTopicJob
                ? "继续本次选题任务，读取原文画面、编写解说并生成完整视频。重试沿用当前选题与资料；完成后可逐镜修改。"
                : "先让本地 AI 生成口播与画面安排；再点任意一镜修改文字或更换画面，最后自动配齐画面试剪。", 13, true));

            var actions = EditorialBar();
            actions.Margin = new Thickness(0, 10, 0, 6);
            Action<string, string, Action, bool, bool> button = (label, description, click, enabled, primary) =>
            {
                var action = Ui.Button(label, click, primary);
                ProductionAvailability(action, enabled, description, busy ? "这份作品正在制作，完成后可编辑。" : "请先生成分镜草稿后再使用此操作。");
                action.Margin = new Thickness(0, 0, 8, 6);
                actions.Children.Add(action);
            };
            if (isTopicJob)
            {
                button(topicAction, "继续当前选题的整片任务，完成后保存在本机，不会自动发布。", async () =>
                    await RunProductionJob("topic_produce", platform, day, itemId,
                        "正在制作整片…", "整片已生成，尚未发布"), !busy, !currentPackage);
            }
            else if (isEditorial)
            {
                button(draft.Count == 0 ? "① 让本地 AI 生成分镜与开场" : "重新生成开场试剪",
                    "调用本地 AI 根据当前资料编写口播分镜并渲染开场试剪。", async () =>
                    await RunProductionJob("editorial_preview", platform, day, J.Obj("id", J.Get(item, "id"), "direct", true),
                        draft.Count == 0 ? "本地 AI 正在编写分镜与开场…" : "正在重新生成开场试剪…", "分镜与开场试剪已就绪"),
                    !busy, draft.Count == 0);
            }
            else
            {
                button("① 让本地 AI 生成候选分镜", "根据当前选题编写口播分镜草稿。", async () =>
                    await RunProductionJob("ai", platform, day, null, "本地 AI 正在生成候选分镜…", "候选分镜已就绪"),
                    !busy, draft.Count == 0);
                if (multi)
                    button("生成开场试剪", "使用已保存分镜生成开场视频。", async () =>
                        await RunProductionJob("preview", platform, day, null, "正在渲染开场试剪…", "开场试剪已就绪"),
                        !busy && edit.Count > 0, false);
            }

            var autoExport = new CheckBox
            {
                Content = "剪完顺便正式导出", IsEnabled = !busy,
                VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(4, 0, 10, 6)
            };
            var autoCut = Ui.Button("AI 自动配画面并拼全片试剪", async () =>
            {
                bool exportWhenReady = autoExport.IsChecked == true;
                await RunProductionJob("auto_cut", platform, day,
                    J.Obj("id", J.Get(item, "id"), "revision", J.Get(item, "edit_revision"),
                          "suggestion_revision", J.Get(item, "suggestion_revision"), "export", exportWhenReady),
                    "本地 AI 正在选镜并剪辑全片…", exportWhenReady ? "成片已导出并通过质检流程" : "全片试剪已就绪，请到下方第三步审看");
            }, draft.Count > 0);
            ProductionAvailability(autoCut, !busy, "自动为未锁定的分镜匹配已过审素材并合成全片试剪。", "这份作品正在制作，完成后可再次剪辑。");
            autoCut.Margin = new Thickness(0, 0, 8, 6);
            if (!isTopicJob) actions.Children.Add(autoCut);

            button("逐镜精修口播与画面", "打开分镜编辑器，逐句修改口播词、切换画面素材或锁定满意镜头。", async () =>
            {
                try
                {
                    if (draft.Count > 0) OpenCutEditor(platform, day, item, draft, 0, edit.Count == 0);
                    else
                    {
                        var result = J.Map(await Worker.RunAsync("draft", platform, J.Obj("with_revision", true), day));
                        var current = new Dictionary<string, object>(item);
                        current["id"] = J.Get(result, "episode_id");
                        current["edit_revision"] = J.Get(result, "edit_revision");
                        current["suggestion_revision"] = J.Get(result, "suggestion_revision");
                        OpenCutEditor(platform, day, current, J.Map(J.Get(result, "draft")), 0, false);
                    }
                }
                catch (Exception error) { Notify(error.Message); }
            }, !busy && ((!isEditorial && !isTopicJob) || draft.Count > 0), false);

            var fillMissing = Ui.Button("只补缺画面的图卡镜", async () =>
                await RunProductionJob("auto_cut", platform, day,
                    J.Obj("id", J.Get(item, "id"), "revision", J.Get(item, "edit_revision"),
                          "suggestion_revision", J.Get(item, "suggestion_revision"), "only_missing", true, "export", false),
                    "正在为缺少实拍画面的镜头补配素材…", "补镜完成，请审看试剪"));
            ProductionAvailability(fillMissing, !busy && edit.Count > 0, "保留已有实拍镜头，只为纯文字图卡分镜重新匹配素材。",
                busy ? "这份作品正在制作，请等待完成。" : "先保存一版分镜后，可单独补齐缺画面镜头。");
            fillMissing.Margin = new Thickness(0, 0, 8, 6);
            if (!isTopicJob)
            {
                actions.Children.Add(fillMissing);
                actions.Children.Add(autoExport);
            }

            if (isEditorial && !isTopicJob)
                button("增删参考报道／原文", "返回素材选配页增删报道或创作说明。", () => BeginAssembly(J.Map(J.Get(editorial, "anchor")), platform, day, editorial,
                    seconds, J.Text(item, "video_style_request")), !busy, false);

            step2.Children.Add(actions);

            if (edit.Count > 0 && suggestion.Count > 0 && J.Text(item, "suggestion_revision") != editRevision)
            {
                var candidate = Ui.Button("发现新生成的 AI 候选分镜 · 点击对比审看", () => OpenCutEditor(platform, day, item, suggestion, 0, true));
                ProductionAvailability(candidate, !busy, "查看 AI 新生成的候选分镜，保存后替换当前稿。", "这份作品正在制作，完成后可审看。");
                candidate.HorizontalAlignment = HorizontalAlignment.Left;
                candidate.Margin = new Thickness(0, 0, 0, 8);
                step2.Children.Add(candidate);
            }

            Action<int> openShot = busy ? (Action<int>)null : index => OpenCutEditor(platform, day, item, draft, index, edit.Count == 0);
            if (draft.Count > 0)
            {
                step2.Children.Add(ProductionTimeline(draft, seconds, edit.Count > 0, openShot));
                step2.Children.Add(ProductionShotList(item, draft, edit.Count > 0, openShot));
            }
            else
            {
                var emptyDraft = Ui.Text(isTopicJob
                    ? "尚未生成完整分镜。点「" + topicAction + "」继续当前任务；采集或生成失败时会保留选题供重试。"
                    : "尚未生成分镜。请先在上方第一步确认好可用素材，再点「① 让本地 AI 生成分镜与开场」。", 14, true);
                emptyDraft.Margin = new Thickness(0, 6, 0, 2);
                step2.Children.Add(emptyDraft);
            }
            var step2Card = Ui.Card(step2);
            step2Card.Margin = new Thickness(0, 0, 0, 12);
            body.Children.Add(step2Card);

            // STEP 3: Preview Monitor, Export & Embedded Quality Gate
            var preview = J.Map(J.Get(item, "preview"));
            string previewRevision = J.Text(item, "preview_revision");
            bool currentPreview = previewRevision.Length > 0 && new[] { "edit_revision", "suggestion_revision", "opening_revision" }
                .Any(key => J.Text(item, key) == previewRevision);
            var fullPreview = J.Map(J.Get(item, "full_preview"));
            bool currentFullPreview = editRevision.Length > 0 && J.Text(item, "full_preview_revision") == editRevision
                && File.Exists(J.Text(fullPreview, "video"));

            var monitor = new StackPanel();
            monitor.Children.Add(EditorialStrong("第三步 · 试剪审看、自动质检与导出成片", 17));
            monitor.Children.Add(Ui.Text(isTopicJob
                ? "整片制作完成后，在这里检查解说、字幕和画面。修改分镜后可更新预览并重新导出；不会自动发布。"
                : "先拼「全片试剪」核对配音、字幕与画面；满意后点「正式导出成片」。导出后自动核验四项质量指标，不合适可一键退回并通知群／团队。", 13, true));
            var reviewModes = new TabControl { Margin = new Thickness(0, 8, 0, 8) };
            if ((!isTopicJob && (isEditorial || multi)) || preview.Count > 0)
            {
                var opening = new StackPanel { Margin = new Thickness(10) };
                if (currentPreview) opening.Children.Add(ProductionVideo(preview, "播放开场试剪"));
                else opening.Children.Add(Ui.Text(preview.Count > 0 ? "分镜已改动，开场试剪可重新生成。"
                    : "生成分镜与开场后，可在这里预览片头效果。", 14, true));
                reviewModes.Items.Add(new TabItem { Header = "开场试剪", Content = opening });
            }
            var full = new StackPanel { Margin = new Thickness(10) };
            if (currentFullPreview) full.Children.Add(ProductionVideo(fullPreview, "播放全片试剪"));
            else full.Children.Add(Ui.Text(isTopicJob && !topicComplete ? "整片任务尚未完成，请点上方「" + topicAction + "」继续。"
                : fullPreview.Count > 0 ? "分镜已修改，点下方「拼全片试剪预览」更新预览。"
                : "保存分镜草稿后，点下方「拼全片试剪预览」即可从头到尾试看整条视频。", 14, true));
            reviewModes.Items.Add(new TabItem { Header = "全片试剪", Content = full });
            if (hasVideoFile)
            {
                var exportedTab = new StackPanel { Margin = new Thickness(10) };
                exportedTab.Children.Add(ProductionVideo(package, currentPackage ? "播放当前正式成片" : "播放上一版已导出成片"));
                reviewModes.Items.Add(new TabItem { Header = currentPackage ? "正式成片（当前版）" : "上一版成片", Content = exportedTab });
            }
            reviewModes.SelectedIndex = hasVideoFile && currentPackage ? reviewModes.Items.Count - 1
                : currentFullPreview || reviewModes.Items.Count == 1 ? reviewModes.Items.Count - 1 : 0;
            monitor.Children.Add(reviewModes);

            var finish = EditorialBar();
            if (!currentFullPreview && (!isTopicJob || topicComplete))
            {
                var makePreview = Ui.Button("拼全片试剪预览", async () =>
                    await RunProductionJob("full_preview", platform, day, J.Obj("id", J.Get(item, "id"), "revision", editRevision),
                        "正在合成配音字幕并拼接全片试剪…", "全片试剪已就绪"));
                ProductionAvailability(makePreview, !busy && edit.Count > 0 && editRevision.Length > 0,
                    "将已保存的分镜拼成全片试剪看效果，暂不导出正式成片。", busy ? "这份作品正在制作，请等待完成。" : "请先点「逐镜精修口播与画面 → 保存分镜草稿」或运行「AI 自动配画面」，再生成全片试剪。");
                makePreview.Margin = new Thickness(0, 0, 8, 6);
                finish.Children.Add(makePreview);
            }
            var export = Ui.Button("正式导出成片（自动质检）", async () =>
                await RunProductionJob(isEditorial ? "editorial_export" : "render", platform, day,
                    isEditorial ? J.Obj("id", J.Get(item, "id"), "revision", editRevision) : null,
                    "正在渲染正式成片并执行质量检测…", "成片导出完成，请查看下方质检报告"), true);
            ProductionAvailability(export, !busy && (edit.Count > 0 || (!isEditorial && !multi)),
                "导出正式 MP4 成片与发布文案，并自动执行时长、画面和文件完整性检测。", busy ? "这份作品正在制作，请等待完成。" : "请先保存分镜草稿后再导出正式成片。");
            export.Margin = new Thickness(0, 0, 8, 6);
            if (!isTopicJob || topicComplete) finish.Children.Add(export);
            if (hasVideoFile)
            {
                finish.Children.Add(EditorialLink("打开成片文件夹", Path.GetDirectoryName(packageVideo)));
            }
            monitor.Children.Add(finish);
            var step3Card = Ui.Card(monitor);
            step3Card.Margin = new Thickness(0, 0, 0, 12);
            body.Children.Add(step3Card);

            if (inspection != null && inspection.Count > 0)
            {
                var gate = BuildQualityGateCard(inspection, platform, day, title, hasVideoFile, refreshHost, true);
                gate.Margin = new Thickness(0, 0, 0, 12);
                body.Children.Add(gate);
            }

            if (isEditorial) body.Children.Add(ProductionSources(editorial));
            if (isEditorial && !isTopicJob && draft.Count > 0)
            {
                var alternatives = EditorialBar();
                alternatives.Margin = new Thickness(14, 8, 0, 0);
                var rewrite = Ui.Button("按爆款案例重写分镜", async () =>
                    await ProductionAlternative("editorial_preview", platform, day, J.Obj("id", J.Get(item, "id"),
                        "rewrite", true, "direct", true), "正在按案例重写分镜…"));
                rewrite.Margin = new Thickness(0, 0, 8, 0);
                var direct = Ui.Button("多模态看图重新选镜", async () =>
                    await ProductionAlternative("visual_direct", platform, day, itemId, "正在看图重新选镜…"));
                rewrite.IsEnabled = direct.IsEnabled = !busy;
                alternatives.Children.Add(rewrite);
                alternatives.Children.Add(direct);
                body.Children.Add(EditorialRow(new Expander { Header = EditorialStrong("进阶 AI 重写与视觉选镜工具", 15), Content = alternatives }));
            }
            return body;
        }

        private FrameworkElement ProductionShotList(IDictionary<string, object> item, IDictionary<string, object> draft,
            bool saved, Action<int> openShot)
        {
            var scenes = J.Rows(J.Get(draft, "scenes")).ToArray();
            var confirmed = ConfirmedCut(draft, saved, scenes.Count());
            var approvedAssets = J.Rows(J.Get(item, "media"))
                .Where(a => ProductionMediaState(item, a) == "approved")
                .ToDictionary(a => J.Text(a, "page_url"), a => a);

            var list = new StackPanel { Margin = new Thickness(0, 8, 0, 0) };
            int cardShots = 0;
            for (int i = 0; i < scenes.Length; i++)
            {
                int idx = i;
                var shot = scenes[i];
                string mediaKey = J.Text(shot, "media");
                var visual = J.Map(J.Get(shot, "visual"));
                string layout = J.Text(visual, "layout");
                IDictionary<string, object> asset = null;
                bool hasAsset = mediaKey.Length > 0 && approvedAssets.TryGetValue(mediaKey, out asset);
                string shotType;
                string shotTone;
                if (hasAsset)
                {
                    bool isVid = J.Text(asset, "kind") == "video";
                    shotType = (isVid ? "实拍视频 · " : "配图 · ") + EditorialTruncate(J.Text(asset, "title"), 22);
                    shotTone = "Sage";
                }
                else if (new[] { "steps", "flow", "timeline", "comparison", "depth", "drilling_flow", "chip_grid", "percent_bars", "parallel_flow", "result_split" }.Contains(layout))
                {
                    shotType = "解释动画 · 非实拍";
                    shotTone = "Sky";
                }
                else
                {
                    shotType = "纯文字图卡（建议配素材）";
                    shotTone = "Honey";
                    cardShots++;
                }

                var row = new DockPanel { Margin = new Thickness(0, 2, 0, 2) };
                if (openShot != null)
                {
                    var editBtn = Ui.Filter("改这镜", () => openShot(idx));
                    editBtn.VerticalAlignment = VerticalAlignment.Top;
                    editBtn.Margin = new Thickness(10, 0, 0, 0);
                    DockPanel.SetDock(editBtn, Dock.Right);
                    row.Children.Add(editBtn);
                }
                var content = new StackPanel();
                var meta = new WrapPanel { Margin = new Thickness(0, 0, 0, 3) };
                var num = EditorialStrong("第 " + (i + 1).ToString("00") + " 镜 · " + J.Text(shot, "title"), 14);
                num.Margin = new Thickness(0, 0, 8, 0);
                meta.Children.Add(num);
                var vBadge = Ui.Badge(shotType, shotTone);
                vBadge.Margin = new Thickness(0, 0, 6, 0);
                meta.Children.Add(vBadge);
                if (confirmed.Contains(i))
                    meta.Children.Add(Ui.Badge("已锁定", "Sky"));
                content.Children.Add(meta);
                content.Children.Add(Ui.Text(J.Text(shot, "text"), 13));
                row.Children.Add(content);
                list.Children.Add(new Border
                {
                    Child = row, BorderBrush = Ui.Color("LineBrush"),
                    BorderThickness = new Thickness(0, 1, 0, 0), Padding = new Thickness(4, 7, 4, 7)
                });
            }
            var expander = new Expander
            {
                Header = EditorialStrong("逐镜口播文案与画面对照清单（共 " + scenes.Length + " 镜"
                    + (cardShots > 0 ? " · 其中 " + cardShots + " 镜为文字图卡" : " · 全部已配画面") + "）", 15),
                Content = list,
                IsExpanded = scenes.Length <= 12 || cardShots * 2 > scenes.Length,
                Margin = new Thickness(0, 8, 0, 0)
            };
            return expander;
        }

        private async Task ProductionAlternative(string stage, string platform, string day, object payload, string progress)
        {
            if (!await RunProductionJob(stage, platform, day, payload, progress, "候选分镜已就绪")) return;
            if (currentPage != "production" || productionPlatform != platform || productionDay != day) return;
            try
            {
                var edition = J.Map(await Worker.RunAsync("workspace_edition", day: day));
                var current = J.Map(J.Get(J.Map(J.Get(edition, "items")), platform));
                var suggestion = J.Map(J.Get(current, "suggestion"));
                if (suggestion.Count > 0 && currentPage == "production" && productionPlatform == platform && productionDay == day)
                    OpenCutEditor(platform, day, current, suggestion, 0, true);
            }
            catch (Exception error) { Notify(error.Message); }
        }

        private static string ProductionKey(string platform, string day) { return day + "/" + platform; }

        private async Task<bool> RunProductionJob(string stage, string platform, string day, object payload, string waiting, string success)
        {
            string key = ProductionKey(platform, day);
            if (productionJobs.ContainsKey(key) || dailyCompilationBusy && day == dailyCompilationDay)
            {
                Notify("此平台的工作稿正在处理。");
                return false;
            }
            productionJobs.Add(key, waiting);
            productionFeedback[key] = "";
            productionBlocked.Remove(key);
            Notify(waiting);
            if (ProductionChanged != null) ProductionChanged(platform, day);
            try
            {
                await Worker.RunAsync(stage, platform, payload, day);
                productionFeedback[key] = success;
                Notify(success);
                return true;
            }
            catch (Exception error)
            {
                bool blocked = error.Message.Contains("这个环节正在运行");
                if (blocked) productionBlocked.Add(key);
                productionFeedback[key] = blocked
                    ? "这份作品正在由后台处理。当前草稿已保留，任务结束后会自动恢复操作。"
                    : "本次操作未完成：" + error.Message;
                Notify(productionFeedback[key]); return false;
            }
            finally
            {
                productionJobs.Remove(key);
                if (ProductionChanged != null) ProductionChanged(platform, day);
            }
        }

        private FrameworkElement ProductionSources(IDictionary<string, object> editorial)
        {
            var reports = J.Rows(J.Get(editorial, "reports")).ToList();
            var brief = J.Map(J.Get(editorial, "brief"));
            var entries = reports.ToDictionary(row => "report:" + J.Text(row, "id"), row => J.Text(row, "title"));
            int index = 0;
            foreach (object note in J.Items(J.Get(brief, "notes"))) entries["note:" + index++] = Convert.ToString(note);
            index = 0;
            foreach (object path in J.Items(J.Get(brief, "asset_paths"))) entries["asset:" + index++] = Path.GetFileName(Convert.ToString(path));
            var body = new StackPanel { Margin = new Thickness(14, 8, 0, 0) };
            string focus = J.Text(brief, "focus_id");
            string focusKey = focus.StartsWith("note:", StringComparison.Ordinal) ? focus : "report:" + focus;
            if (entries.ContainsKey(focusKey))
            {
                string quote = J.Text(brief, "focus_quote");
                body.Children.Add(EditorialStrong("讲述重点  ·  " + (quote.Length > 0 ? quote : entries[focusKey]), 14));
            }
            var order = J.Items(J.Get(brief, "sequence")).Select(value => Convert.ToString(value)).Where(entries.ContainsKey).ToList();
            for (int i = 0; i < order.Count; i++)
                body.Children.Add(Ui.Text((i + 1).ToString("00") + "  " + entries[order[i]], 14));
            foreach (var row in reports)
            {
                string url = J.Text(row, "url");
                if (IsEditorialWebLink(url)) body.Children.Add(EditorialLink(J.Text(row, "source") + "  ·  " + J.Text(row, "title"), url));
            }
            string sourceLabel = J.Text(editorial, "mode") == "original" ? "原创提纲 · 非新闻报道" : reports.Count + " 篇报道";
            return EditorialRow(new Expander { Header = EditorialStrong("已确认素材  ·  " + sourceLabel, 16), Content = body });
        }

        private FrameworkElement ProductionMedia(IDictionary<string, object> item, string platform, string day, bool busy,
            bool multi, bool editorial, Func<Task> refreshHost)
        {
            bool isTopicJob = J.Map(J.Get(item, "topic_job")).Count > 0;
            var media = J.Rows(J.Get(item, "media")).ToList();
            var visibleMedia = media.Where(asset => ProductionMediaState(item, asset) != "removed").ToList();
            int pendingCount = visibleMedia.Count(asset => ProductionMediaState(item, asset) == "pending");
            int approvedCount = visibleMedia.Count(asset => ProductionMediaState(item, asset) == "approved");
            var reports = J.Rows(J.Get(J.Map(J.Get(item, "editorial")), "reports")).ToList();

            var wrapper = new StackPanel();
            var topLine = new DockPanel { Margin = new Thickness(0, 0, 0, 6) };
            topLine.Children.Add(EditorialStrong(isTopicJob ? "第一步 · 本题原文画面" : "第一步 · 审核并补充画面素材", 17));
            var countBadge = Ui.Badge(
                pendingCount > 0 ? "待你审核 " + pendingCount + " 项 · 已通过 " + approvedCount + " 项"
                    : approvedCount > 0 ? "✓ 已通过可用素材 " + approvedCount + " 项" : isTopicJob ? "原文画面待采集" : "暂无画面素材（建议先补充）",
                pendingCount > 0 || approvedCount == 0 ? "Honey" : "Sage");
            DockPanel.SetDock(countBadge, Dock.Right);
            topLine.Children.Add(countBadge);
            wrapper.Children.Add(topLine);
            wrapper.Children.Add(Ui.Text(isTopicJob
                ? "按本题原文读取视频、照片和官方截图。若没有可用画面，任务会停止并保留选题；可在下方重试整片制作。"
                : "先查找报道原图和视频；缺少实拍的数据或流程会尝试制作解释动画，并标注为非实拍。你移除的素材不会再次接入。", 13, true));

            var actions = EditorialBar();
            actions.Margin = new Thickness(0, 8, 0, 6);
            if (pendingCount > 0)
            {
                var approveAll = Ui.Button("✓ 一键通过全部待审素材（" + pendingCount + " 项）", async () =>
                {
                    try
                    {
                        await Worker.RunAsync("workspace_approve_all_media", platform, null, day);
                        if (refreshHost != null) await refreshHost();
                        Notify("已全部通过，现在可以让 AI 自动配画面试剪了。");
                    }
                    catch (Exception error) { Notify(error.Message); }
                }, true);
                approveAll.IsEnabled = !busy;
                approveAll.Margin = new Thickness(0, 0, 8, 6);
                actions.Children.Add(approveAll);
            }
            var import = Ui.Button("+ 导入本机视频／图片", async () =>
            {
                var picker = new OpenFileDialog { Filter = EditorialMediaFilter, Title = "选择可用于发布的本机素材" };
                if (picker.ShowDialog(this) == true)
                    await RunProductionJob("import_media", platform, day,
                        editorial ? (object)J.Obj("id", J.Get(item, "id"), "filename", picker.FileName) : picker.FileName,
                        "正在导入媒体…", "本机素材已导入并自动通过审核");
            }, approvedCount == 0 && pendingCount == 0);
            import.IsEnabled = !busy;
            import.Margin = new Thickness(0, 0, 8, 6);
            actions.Children.Add(import);

            var openSearch = Ui.Button("搜索公共图片／视频", () => OpenMaterialSearch(platform, day, item));
            openSearch.IsEnabled = !busy;
            openSearch.Margin = new Thickness(0, 0, 8, 6);
            if (!isTopicJob) actions.Children.Add(openSearch);

            if (!isTopicJob && (!editorial || multi))
            {
                var search = Ui.Button("自动抓取原文素材", async () =>
                    await RunProductionJob("media", platform, day, null, "正在查找相关素材…", "素材已更新"));
                search.IsEnabled = !busy;
                search.Margin = new Thickness(0, 0, 8, 6);
                actions.Children.Add(search);
            }

            var sources = visibleMedia.Where(asset => ProductionMediaState(item, asset) == "approved"
                && J.Map(J.Get(asset, "comparison")).Count == 0 && File.Exists(J.Text(asset, "path"))).ToList();
            if (sources.GroupBy(asset => J.Text(asset, "kind")).Any(group => group.Count() >= 2))
            {
                var compare = Ui.Button("制作双画面同屏对照", () => OpenComparison(platform, day, item, sources));
                compare.IsEnabled = !busy;
                compare.Margin = new Thickness(0, 0, 8, 6);
                actions.Children.Add(compare);
            }
            wrapper.Children.Add(actions);

            if (visibleMedia.Count > 0)
            {
                var listBody = new StackPanel { Margin = new Thickness(4, 4, 0, 0) };
                foreach (var asset in visibleMedia)
                {
                    string url = J.Text(asset, "page_url");
                    string state = ProductionMediaState(item, asset);
                    var row = new StackPanel { Margin = new Thickness(0, 4, 0, 5) };
                    string name = J.Text(asset, "title");
                    var titleBar = new WrapPanel();
                    var strongName = EditorialStrong(name, 14);
                    strongName.Margin = new Thickness(0, 0, 8, 0);
                    titleBar.Children.Add(strongName);
                    titleBar.Children.Add(Ui.Badge(state == "approved" ? "✓ 已通过" : "待审核", state == "approved" ? "Sage" : "Honey"));
                    row.Children.Add(titleBar);
                    var controls = EditorialBar();
                    controls.Margin = new Thickness(0, 4, 0, 0);
                    string path = J.Text(asset, "path");
                    string thumbnail = J.Text(asset, "preview");
                    if (File.Exists(path))
                    {
                        if (J.Text(asset, "kind") == "video")
                        {
                            var play = new Button { Content = "预览视频", Margin = new Thickness(0, 2, 6, 2) };
                            play.Click += async (sender, args) =>
                            {
                                play.IsEnabled = false;
                                try
                                {
                                    Notify("正在准备素材预览…");
                                    string preview = Convert.ToString(await Worker.RunAsync("preview_asset", platform,
                                        J.Obj("id", J.Get(item, "id"), "page_url", url), day));
                                    if (play.IsLoaded)
                                        ShowProductionVideo(J.Obj("video", preview, "cover", thumbnail), name);
                                }
                                catch (Exception error) { Notify(error.Message); }
                                finally { play.IsEnabled = true; }
                            };
                            controls.Children.Add(play);
                        }
                        else controls.Children.Add(EditorialLink("查看原图", path));
                    }
                    if (IsEditorialWebLink(url)) controls.Children.Add(EditorialLink("素材出处", url));
                    var comparison = J.Map(J.Get(asset, "comparison"));
                    if (comparison.Count > 0)
                    {
                        row.Children.Add(Ui.Text("共同条件 · " + J.Text(comparison, "condition"), 13, true));
                        foreach (var source in J.Rows(J.Get(comparison, "sources")))
                            if (IsEditorialWebLink(J.Text(source, "page_url")))
                                controls.Children.Add(EditorialLink(J.Text(source, "title"), J.Text(source, "page_url")));
                    }
                    if (reports.Count > 0 && J.Text(asset, "kind") == "image" && comparison.Count == 0)
                    {
                        var linked = reports.FirstOrDefault(report => J.Text(report, "id") == J.Text(asset, "verified_report_id"));
                        if (linked != null) row.Children.Add(Ui.Text("已绑定报道 · " + J.Text(linked, "title"), 13, true));
                        var link = Ui.Button("绑定对应报道", () => BindReportImage(platform, day, item, asset, reports));
                        link.IsEnabled = !busy;
                        link.Margin = new Thickness(0, 2, 6, 2);
                        controls.Children.Add(link);
                    }
                    if (state != "approved")
                    {
                        var approve = Ui.Button("✓ 通过", async () =>
                            await RunProductionJob("review_media", platform, day,
                                J.Obj("id", J.Get(item, "id"), "page_url", url, "state", "approved"), "正在更新审核…", "素材已通过审核"), true);
                        approve.IsEnabled = !busy;
                        approve.Margin = new Thickness(0, 2, 6, 2);
                        controls.Children.Add(approve);
                    }
                    var remove = Ui.Button("移出不用", async () =>
                        await RunProductionJob("review_media", platform, day,
                            J.Obj("id", J.Get(item, "id"), "page_url", url, "state", "removed"), "正在更新审核…", "素材已移出"));
                    remove.IsEnabled = !busy;
                    remove.Margin = new Thickness(0, 2, 6, 2);
                    controls.Children.Add(remove);
                    row.Children.Add(controls);
                    var entry = new DockPanel();
                    if (File.Exists(thumbnail))
                    {
                        var image = EditorialImage(thumbnail, 64, 200);
                        image.Width = 100;
                        image.Margin = new Thickness(0, 3, 10, 3);
                        DockPanel.SetDock(image, Dock.Left);
                        entry.Children.Add(image);
                    }
                    entry.Children.Add(row);
                    listBody.Children.Add(new Border
                    {
                        Child = entry, BorderBrush = Ui.Color("LineBrush"),
                        BorderThickness = new Thickness(0, 1, 0, 0), Padding = new Thickness(0, 6, 0, 4)
                    });
                }
                wrapper.Children.Add(new Expander
                {
                    Header = EditorialStrong("展开查看全部素材明细（" + visibleMedia.Count + " 项）", 14),
                    Content = listBody,
                    IsExpanded = pendingCount > 0 || visibleMedia.Count <= 4
                });
            }
            var card = Ui.Card(wrapper, pendingCount > 0 ? "HoneyBrush" : "SurfaceBrush");
            card.Margin = new Thickness(0, 0, 0, 12);
            return card;
        }

        private void BindReportImage(string platform, string day, IDictionary<string, object> item, IDictionary<string, object> asset,
                                     List<IDictionary<string, object>> reports)
        {
            var dialog = new Window { Title = "核对报道配图", Owner = this, Width = 520, SizeToContent = SizeToContent.Height,
                Background = Brushes.White, WindowStartupLocation = WindowStartupLocation.CenterOwner };
            var form = new StackPanel { Margin = new Thickness(24) };
            dialog.Content = form;
            form.Children.Add(EditorialHeading("核对报道配图", "确认照片对应所选报道后，可以在换到这条资讯时使用。"));
            string[] labels = new[] { "仅作资料" }.Concat(reports.Select(report => J.Text(report, "title"))).ToArray();
            string[] ids = new[] { "" }.Concat(reports.Select(report => J.Text(report, "id"))).ToArray();
            var choice = EditorialChoice(labels, Math.Max(0, Array.IndexOf(ids, J.Text(asset, "verified_report_id"))), "对应报道");
            form.Children.Add(EditorialLabeled("对应报道", choice));
            var confirm = new Button { Content = "保存核对结果", Style = (Style)FindResource("PrimaryButton"),
                HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 12, 0, 0) };
            form.Children.Add(confirm);
            confirm.Click += async (sender, args) =>
            {
                if (choice.SelectedIndex < 0) return;
                confirm.IsEnabled = false;
                bool ok = await RunProductionJob("review_media", platform, day,
                    J.Obj("id", J.Get(item, "id"), "page_url", J.Text(asset, "page_url"), "state", "approved",
                          "report_id", ids[choice.SelectedIndex]), "正在保存配图关系…", "已保存报道配图");
                if (ok && dialog.IsVisible) dialog.Close();
                else confirm.IsEnabled = true;
            };
            dialog.ShowDialog();
        }

        private void OpenComparison(string platform, string day, IDictionary<string, object> item,
                                    List<IDictionary<string, object>> sources)
        {
            var dialog = new Window { Title = "制作同屏对照", Owner = this, Width = 550, Height = 660,
                Background = Brushes.White, WindowStartupLocation = WindowStartupLocation.CenterOwner };
            var form = new StackPanel { Margin = new Thickness(24) };
            dialog.Content = Ui.Scroll(form);
            form.Children.Add(EditorialHeading("制作同屏对照", "两张照片或两段视频。口播同时提到两个样本名和共同条件时，AI 可以自动选用这组画面。"));
            var choices = sources.Select(asset => J.Text(asset, "title") + " · " +
                (J.Text(asset, "kind") == "video" ? "视频" : "照片")).ToArray();
            var first = EditorialChoice(choices, 0, "样本 A 素材");
            var second = EditorialChoice(choices, 1, "样本 B 素材");
            var nameA = EditorialField("", "样本 A 名称");
            var nameB = EditorialField("", "样本 B 名称");
            nameA.MaxLength = nameB.MaxLength = 18;
            var timeA = EditorialField("0", "样本 A 入点");
            var timeB = EditorialField("0", "样本 B 入点");
            var timeRowA = EditorialLabeled("视频 A 入点（秒）", timeA);
            var timeRowB = EditorialLabeled("视频 B 入点（秒）", timeB);
            var condition = EditorialField("", "共同条件");
            condition.MaxLength = 32;
            var region = EditorialChoice(new[] { "完整画面", "中心放大", "左上放大", "右上放大", "左下放大", "右下放大" }, 0, "照片同区域放大");
            string[] regions = { "", "center", "top-left", "top-right", "bottom-left", "bottom-right" };
            var regionRow = EditorialLabeled("画面范围 · 两边取同一区域", region);
            form.Children.Add(EditorialLabeled("样本 A 素材", first));
            form.Children.Add(EditorialLabeled("样本 A 名称", nameA));
            form.Children.Add(timeRowA);
            form.Children.Add(EditorialLabeled("样本 B 素材", second));
            form.Children.Add(EditorialLabeled("样本 B 名称", nameB));
            form.Children.Add(timeRowB);
            form.Children.Add(EditorialLabeled("共同条件，如同机位自然光", condition));
            form.Children.Add(regionRow);
            Action updateTimes = () =>
            {
                timeRowA.Visibility = first.SelectedIndex >= 0 && J.Text(sources[first.SelectedIndex], "kind") == "video"
                    ? Visibility.Visible : Visibility.Collapsed;
                timeRowB.Visibility = second.SelectedIndex >= 0 && J.Text(sources[second.SelectedIndex], "kind") == "video"
                    ? Visibility.Visible : Visibility.Collapsed;
                regionRow.Visibility = first.SelectedIndex >= 0 && second.SelectedIndex >= 0 &&
                    timeRowA.Visibility == Visibility.Collapsed && timeRowB.Visibility == Visibility.Collapsed
                    ? Visibility.Visible : Visibility.Collapsed;
            };
            first.SelectionChanged += (sender, args) => updateTimes();
            second.SelectionChanged += (sender, args) => updateTimes();
            updateTimes();
            var notice = Ui.Text("", 13, true);
            var submit = new Button { Content = "保存为同屏素材", Style = (Style)FindResource("PrimaryButton"),
                HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 12, 0, 4) };
            form.Children.Add(submit);
            form.Children.Add(notice);
            submit.Click += async (sender, args) =>
            {
                if (first.SelectedIndex < 0 || second.SelectedIndex < 0 || first.SelectedIndex == second.SelectedIndex
                    || J.Text(sources[first.SelectedIndex], "kind") != J.Text(sources[second.SelectedIndex], "kind")
                    || new[] { nameA.Text, nameB.Text, condition.Text }.Any(String.IsNullOrWhiteSpace))
                { notice.Text = "请选两份不同的同类素材，并填写样本名和共同条件。"; return; }
                double a = 0, b = 0;
                if (timeRowA.Visibility == Visibility.Visible &&
                    (!Double.TryParse(timeA.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out a) ||
                     !Double.TryParse(timeB.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out b) ||
                     a < 0 || b < 0 || Double.IsNaN(a) || Double.IsNaN(b) || Double.IsInfinity(a) || Double.IsInfinity(b)))
                { notice.Text = "请输入有效的视频起点（秒）。"; return; }
                submit.IsEnabled = false;
                var pair = J.Obj("sources", new[] { J.Text(sources[first.SelectedIndex], "page_url"),
                    J.Text(sources[second.SelectedIndex], "page_url") }, "labels", new[] { nameA.Text.Trim(), nameB.Text.Trim() },
                    "condition", condition.Text.Trim(), "starts", new[] { a, b },
                    "region", regionRow.Visibility == Visibility.Visible ? regions[Math.Max(0, region.SelectedIndex)] : "");
                bool ok = await RunProductionJob("import_media", platform, day,
                    J.Obj("id", J.Get(item, "id"), "comparison", pair), "正在生成同屏素材…", "同屏素材已加入素材库");
                if (ok && dialog.IsVisible) dialog.Close();
                else submit.IsEnabled = true;
            };
            dialog.ShowDialog();
        }

        private static string ProductionMediaState(IDictionary<string, object> item, IDictionary<string, object> asset)
        {
            string state = J.Text(J.Map(J.Get(item, "media_review")), J.Text(asset, "page_url"));
            return state.Length > 0 ? state : item.ContainsKey("editorial") && !Equals(J.Get(asset, "local"), true) ? "pending" : "approved";
        }

        private FrameworkElement ProductionVideo(IDictionary<string, object> package, string playLabel)
        {
            var box = new StackPanel();
            string path = J.Text(package, "video");
            string cover = J.Text(package, "cover");
            if (!File.Exists(path))
            {
                box.Children.Add(Ui.Text("视频文件不可用。", 13, true));
                return box;
            }
            var display = new Grid { Background = Ui.Color("MonitorBrush"), Height = 260 };
            var poster = EditorialImage(cover, 260, 640);
            display.Children.Add(poster);
            var media = new MediaElement { LoadedBehavior = MediaState.Manual, UnloadedBehavior = MediaState.Close,
                Stretch = Stretch.Uniform, Visibility = Visibility.Hidden, Source = new Uri(Path.GetFullPath(path)) };
            AutomationProperties.SetName(media, playLabel);
            display.Children.Add(media);
            box.Children.Add(display);
            var tools = EditorialBar();
            var scrub = new Slider { Width = 180, Minimum = 0, Maximum = 1, Margin = new Thickness(8, 0, 8, 0),
                VerticalAlignment = VerticalAlignment.Center, IsEnabled = false };
            AutomationProperties.SetName(scrub, "视频播放进度");
            bool updating = false;
            var timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(300) };
            timer.Tick += (sender, args) =>
            {
                if (!media.NaturalDuration.HasTimeSpan || scrub.IsMouseCaptureWithin) return;
                updating = true;
                scrub.Value = media.Position.TotalSeconds;
                updating = false;
            };
            scrub.ValueChanged += (sender, args) =>
            {
                if (!updating && media.NaturalDuration.HasTimeSpan) media.Position = TimeSpan.FromSeconds(args.NewValue);
            };
            media.MediaOpened += (sender, args) =>
            {
                if (!media.NaturalDuration.HasTimeSpan) return;
                scrub.Maximum = Math.Max(0.01, media.NaturalDuration.TimeSpan.TotalSeconds);
                scrub.IsEnabled = true;
            };
            media.MediaFailed += (sender, args) =>
            {
                media.Visibility = Visibility.Hidden;
                poster.Visibility = Visibility.Visible;
                timer.Stop();
                Notify("内置播放器无法打开此视频。可使用系统播放器。");
            };
            media.MediaEnded += (sender, args) => { media.Stop(); media.Position = TimeSpan.Zero; timer.Stop(); scrub.Value = 0; };
            box.Unloaded += (sender, args) => { timer.Stop(); media.Close(); };
            tools.Children.Add(Ui.Button(playLabel, () =>
            {
                poster.Visibility = Visibility.Hidden;
                media.Visibility = Visibility.Visible;
                media.Play();
                timer.Start();
            }, true));
            tools.Children.Add(Ui.Button("暂停", () => { media.Pause(); timer.Stop(); }));
            tools.Children.Add(scrub);
            tools.Children.Add(EditorialLink("系统播放器", path));
            box.Children.Add(tools);
            return box;
        }

        private void ShowProductionVideo(IDictionary<string, object> package, string title, string playLabel = "播放成片",
                                         Window owner = null)
        {
            var dialog = new Window { Title = title, Owner = owner ?? this, Width = 770, SizeToContent = SizeToContent.Height,
                WindowStartupLocation = WindowStartupLocation.CenterOwner, Icon = Icon };
            dialog.Content = new Border { Padding = new Thickness(20), Background = Ui.Color("SurfaceBrush"),
                Child = ProductionVideo(package, playLabel) };
            dialog.ShowDialog();
        }
    }
}
