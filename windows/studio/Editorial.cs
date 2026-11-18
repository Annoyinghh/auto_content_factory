using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using Ellipse = System.Windows.Shapes.Ellipse;
using Line = System.Windows.Shapes.Line;
using Microsoft.Win32;

namespace Acf.Studio
{
    internal sealed partial class WorkbenchWindow
    {
        private static readonly string[] EditorialPlatforms = { "xiaohongshu", "douyin", "bilibili" };
        private static readonly string[] EditorialPlatformNames = { "小红书", "抖音", "B站" };
        private static Brush EditorialBlue { get { return Ui.Color("AccentBrush"); } }
        private static Brush EditorialTint { get { return Ui.Color("SageBrush"); } }
        private static Brush EditorialLine { get { return Ui.Color("LineBrush"); } }
        private const string EditorialMediaFilter = "图片与视频|*.jpg;*.jpeg;*.png;*.mp4;*.mov;*.webm";

        private sealed class EditorialInput
        {
            public string Kind, Title, Path;
            public IDictionary<string, object> Report = J.Obj();
            public bool IsLink;
            public bool Available { get { return Kind != "report" || Equals(J.Get(Report, "available"), true); } }
        }

        private sealed class EditorialAssembly
        {
            public IDictionary<string, object> Anchor, Options, Saved;
            public string Platform, Day, Active, Focus, Quote, VideoStyle;
            public int Seconds;
            public bool Busy, Reading, Collected;
            public readonly Dictionary<string, EditorialInput> Records = new Dictionary<string, EditorialInput>();
            public readonly List<string> Ordered = new List<string>();
            public readonly List<string> Manual = new List<string>();
            public readonly HashSet<string> Expanded = new HashSet<string>();
        }

        private EditorialAssembly assembly;
        private string topicPlatform = "xiaohongshu";
        private string topicQuery = "", topicSelection = "";
        private int topicMode, topicBrowsePage;
        private bool topicOnlyReady;

        private void OpenTopicBrowser(string platform = null, bool reports = false)
        {
            assembly = null;
            if (Array.IndexOf(EditorialPlatforms, platform) >= 0) topicPlatform = platform;
            topicMode = reports ? 1 : 0;
            topicBrowsePage = 0;
            topicQuery = topicSelection = "";
            topicOnlyReady = false;
            Navigate("topics");
        }

        private void OpenEditorial(IDictionary<string, object> anchor, string platform = null, string day = null)
        {
            BeginAssembly(anchor, platform, day, null, 120);
        }

        private void BeginAssembly(IDictionary<string, object> anchor, string platform, string day,
                                   IDictionary<string, object> saved, int seconds, string videoStyle = "")
        {
            string target = platform ?? J.Text(anchor, "platform");
            assembly = new EditorialAssembly {
                Anchor = anchor, Platform = Array.IndexOf(EditorialPlatforms, target) < 0 ? EditorialPlatforms[0] : target,
                Day = day ?? EditorialToday(), Saved = saved, Seconds = seconds, VideoStyle = videoStyle
            };
            Navigate("topics");
        }

        private FrameworkElement BuildTopics()
        {
            if (assembly != null) return BuildAssembly(assembly);
            var page = new StackPanel();
            var heading = new DockPanel { Margin = new Thickness(0, 0, 0, 12) };
            var actions = EditorialBar();
            var quickPaste = Ui.Button("粘贴自有文案做视频", () => OpenQuickScriptDialog(initialPlatform: topicPlatform), true);
            quickPaste.Margin = new Thickness(0, 0, 6, 0);
            actions.Children.Add(quickPaste);
            actions.Children.Add(Ui.Filter("图文与多平台写稿台", () => OpenWriting(platform: topicPlatform)));
            actions.Children.Add(Ui.Button("自定主题搜资料", OpenCustomTopic));
            DockPanel.SetDock(actions, Dock.Right);
            heading.Children.Add(actions);
            var copy = new StackPanel();
            copy.Children.Add(Ui.Title("① 选题与文案"));
            copy.Children.Add(Ui.Text("选择热点或报道，点击「制作整片」自动采集资料、撰写解说并生成完整视频；也可粘贴自有文案。", 13, true));
            heading.Children.Add(copy);
            page.Children.Add(Ui.Card(heading, "SageBrush"));

            var modes = EditorialBar();
            modes.Margin = new Thickness(0, 12, 0, 4);
            page.Children.Add(modes);
            var columns = new Grid();
            columns.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.42, GridUnitType.Star) });
            columns.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star), MinWidth = 265 });
            page.Children.Add(columns);

            var left = new StackPanel();
            var filters = EditorialBar();
            var platforms = EditorialBar();
            filters.Children.Add(platforms);
            var search = Ui.Search("搜索标题或来源", 190, topicQuery);
            filters.Children.Add(search);
            left.Children.Add(filters);

            var status = Ui.Text("", 12, true);
            status.Margin = new Thickness(0, 4, 0, 6);
            left.Children.Add(status);
            var tools = EditorialBar();
            var onlyReady = new CheckBox { Content = "仅可引用正文", IsChecked = topicOnlyReady, Margin = new Thickness(0, 6, 10, 6) };
            tools.Children.Add(onlyReady);
            string boardUrl = "";
            var boardLink = Ui.Filter("榜单来源 ↗", () => OpenEditorialPath(boardUrl));
            boardLink.Visibility = Visibility.Collapsed;
            tools.Children.Add(boardLink);
            left.Children.Add(tools);
            var list = new StackPanel();
            var paging = new WrapPanel { Margin = new Thickness(0, 6, 0, 0) };
            left.Children.Add(list);
            left.Children.Add(paging);
            var listCard = Ui.Card(left);
            listCard.Margin = new Thickness(0, 0, 13, 0);
            listCard.VerticalAlignment = VerticalAlignment.Top;
            columns.Children.Add(listCard);

            var preview = new ContentControl();
            var previewCard = Ui.Card(preview);
            previewCard.VerticalAlignment = VerticalAlignment.Top;
            Grid.SetColumn(previewCard, 1);
            columns.Children.Add(previewCard);
            preview.Content = Ui.Text("选择一条热点或报道后查看出处。", 13, true);

            IDictionary<string, object> snapshot = null;
            string readError = "";
            const int pageSize = 7;
            Action draw = null;
            draw = () =>
            {
                bool rank = topicMode == 0;
                foreach (Button button in modes.Children)
                    button.Background = Ui.Color((int)button.Tag == topicMode ? "SelectedBrush" : "SurfaceBrush");
                foreach (Button button in platforms.Children)
                    button.Background = Ui.Color((string)button.Tag == topicPlatform ? "SelectedBrush" : "SurfaceBrush");
                platforms.Visibility = rank ? Visibility.Visible : Visibility.Collapsed;
                if (snapshot == null) return;

                var board = J.Map(J.Get(J.Map(J.Get(snapshot, "ranks")), topicPlatform));
                boardUrl = J.Text(board, "url");
                boardLink.Visibility = rank && IsEditorialWebLink(boardUrl) ? Visibility.Visible : Visibility.Collapsed;
                var rows = J.Rows(rank ? J.Get(board, "entries") : J.Get(snapshot, "articles"))
                    .Where(row => EditorialMatches(topicQuery, J.Text(row, "title"), J.Text(TopicReport(row, rank), "source"))
                        && (!topicOnlyReady || TopicReady(row, rank))).ToArray();
                string timestamp = rank ? EditorialBoardTime(J.Text(board, "published")) : "";
                string boardError = rank ? J.Text(board, "error") : "";
                status.Text = rows.Length + (rank ? " 条热点" : " 篇报道")
                    + (timestamp.Length == 0 ? "" : "  ·  榜单 " + timestamp)
                    + (boardError.Length == 0 ? "" : "  ·  " + boardError)
                    + (readError.Length == 0 ? "" : "  ·  " + readError);
                list.Children.Clear();
                paging.Children.Clear();
                if (rows.Length == 0)
                {
                    list.Children.Add(Ui.Text(topicQuery.Length > 0 || topicOnlyReady
                        ? "没有符合筛选条件的选题。" : "本机暂无记录，后台会自动更新。", 13, true));
                    preview.Content = Ui.Text("等待选择选题。", 13, true);
                    return;
                }

                int pageCount = (rows.Length + pageSize - 1) / pageSize;
                topicBrowsePage = Math.Min(topicBrowsePage, pageCount - 1);
                var visible = rows.Skip(topicBrowsePage * pageSize).Take(pageSize).ToArray();
                Action<IDictionary<string, object>> select = row =>
                {
                    topicSelection = TopicKey(row, rank);
                    foreach (Button button in list.Children)
                        button.Background = Ui.Color((string)button.Tag == topicSelection ? "SelectedBrush" : "SurfaceBrush");
                    preview.Content = TopicDetail(row, rank, topicPlatform);
                };
                foreach (var row in visible)
                {
                    var current = row;
                    var button = Ui.Button(J.Text(current, "title"), () => select(current));
                    button.Content = TopicListContent(current, rank);
                    button.Tag = TopicKey(current, rank);
                    button.HorizontalContentAlignment = HorizontalAlignment.Stretch;
                    button.Padding = new Thickness(8, 6, 8, 6);
                    button.Margin = new Thickness(0, 0, 0, 4);
                    System.Windows.Automation.AutomationProperties.SetName(button, J.Text(current, "title"));
                    list.Children.Add(button);
                }
                select(visible.FirstOrDefault(row => TopicKey(row, rank) == topicSelection) ?? visible[0]);
                if (pageCount <= 1) return;
                var previous = Ui.Filter("上一页", () => { topicBrowsePage--; draw(); });
                var next = Ui.Filter("下一页", () => { topicBrowsePage++; draw(); });
                previous.IsEnabled = topicBrowsePage > 0;
                next.IsEnabled = topicBrowsePage + 1 < pageCount;
                paging.Children.Add(previous);
                paging.Children.Add(next);
                var position = Ui.Text("第 " + (topicBrowsePage + 1) + " / " + pageCount + " 页", 12, true);
                position.Margin = new Thickness(5, 7, 0, 0);
                paging.Children.Add(position);
            };
            for (int i = 0; i < 2; i++)
            {
                int mode = i;
                var button = Ui.Filter(i == 0 ? "平台热点" : "采集报道", () =>
                {
                    topicMode = mode;
                    topicBrowsePage = 0;
                    draw();
                });
                button.Tag = mode;
                modes.Children.Add(button);
            }
            foreach (string key in EditorialPlatforms)
            {
                string platform = key;
                var button = Ui.Filter(Ui.Platform(platform), () =>
                {
                    topicPlatform = platform;
                    topicBrowsePage = 0;
                    draw();
                });
                button.Tag = platform;
                platforms.Children.Add(button);
            }
            Func<string, Task> load = async mode =>
            {
                try
                {
                    snapshot = J.Map(await Worker.RunAsync("workspace_collection", payload: J.Obj("refresh", mode)));
                    readError = "";
                    draw();
                }
                catch (Exception error)
                {
                    readError = error.Message;
                    if (snapshot == null) status.Text = readError;
                    else draw();
                }
            };
            var refreshBtn = Ui.Filter("采集最新热点", delegate { });
            tools.Children.Add(refreshBtn);
            refreshBtn.Click += async delegate
            {
                refreshBtn.IsEnabled = false;
                status.Text = "正在从各平台与订阅源拉取最新内容…";
                await load("force");
                refreshBtn.IsEnabled = true;
            };
            search.TextChanged += delegate { topicQuery = search.Text; topicBrowsePage = 0; draw(); };
            onlyReady.Checked += delegate { topicOnlyReady = true; topicBrowsePage = 0; draw(); };
            onlyReady.Unchecked += delegate { topicOnlyReady = false; topicBrowsePage = 0; draw(); };
            draw();
            page.Loaded += async delegate { if (snapshot == null) await load("never"); };
            return Ui.Scroll(page);
        }

        private static IDictionary<string, object> TopicReport(IDictionary<string, object> row, bool rank)
        {
            return rank ? J.Map(J.Get(row, "article")) : row;
        }

        private static bool TopicReady(IDictionary<string, object> row, bool rank)
        {
            return rank ? Equals(J.Get(row, "has_report"), true) :
                !Equals(J.Get(row, "headline_only"), true) && J.Text(row, "summary").Length >= 300;
        }

        private static string TopicKey(IDictionary<string, object> row, bool rank)
        {
            return rank ? J.Text(row, "title") + "|" + J.Text(row, "url") : J.Text(row, "id");
        }

        private static Border TopicBadge(IDictionary<string, object> row, bool rank)
        {
            if (TopicReady(row, rank)) return Ui.Badge("有可引用正文", "Sage");
            return J.Text(TopicReport(row, rank), "summary").Length == 0
                ? Ui.Badge("暂无报道正文", "Honey") : Ui.Badge("待补正文", "Blush");
        }

        private static FrameworkElement TopicListContent(IDictionary<string, object> row, bool rank)
        {
            var line = new DockPanel();
            if (rank)
            {
                var heat = Ui.Text(J.Text(row, "heat"), 12, true);
                heat.VerticalAlignment = VerticalAlignment.Center;
                heat.Margin = new Thickness(8, 0, 0, 0);
                DockPanel.SetDock(heat, Dock.Right);
                line.Children.Add(heat);
                var index = Ui.Text(J.Text(row, "rank").PadLeft(2, '0'), 12, true);
                index.Width = 27;
                index.VerticalAlignment = VerticalAlignment.Center;
                line.Children.Add(index);
            }
            var copy = new StackPanel();
            var title = EditorialStrong(J.Text(row, "title"), 13);
            title.Margin = new Thickness(0, 0, 0, 4);
            copy.Children.Add(title);
            copy.Children.Add(TopicBadge(row, rank));
            line.Children.Add(copy);
            return line;
        }

        private FrameworkElement TopicDetail(IDictionary<string, object> row, bool rank, string platform)
        {
            var detail = new StackPanel();
            detail.Children.Add(EditorialStrong("选题预览", 16));
            var meta = EditorialBar();
            meta.Margin = new Thickness(0, 8, 0, 6);
            var caption = Ui.Text(rank ? Ui.Platform(platform) + " · 榜单第 " + J.Text(row, "rank") + " 位" : "采集报道", 12, true);
            caption.Margin = new Thickness(0, 4, 8, 4);
            meta.Children.Add(caption);
            meta.Children.Add(TopicBadge(row, rank));
            detail.Children.Add(meta);
            var title = EditorialStrong(J.Text(row, "title"), 19);
            detail.Children.Add(title);
            var report = TopicReport(row, rank);
            string source = J.Text(report, "source");
            string summary = J.Text(report, "summary");
            DateTimeOffset stamp;
            string date = DateTimeOffset.TryParse(J.Text(report, "published"), out stamp) ? stamp.ToLocalTime().ToString("MM-dd HH:mm") : "";
            if (source.Length > 0 || date.Length > 0)
            {
                var sourceLine = Ui.Text(String.Join(" · ", new[] { source, date }.Where(part => part.Length > 0)), 12, true);
                sourceLine.Margin = new Thickness(0, 8, 0, 4);
                detail.Children.Add(sourceLine);
            }
            string excerpt = summary.Length == 0 ? "本机暂无可核对的正文。制作整片时会先查找同题资料，也可进入高级素材组装手动选择。" :
                EditorialTruncate(summary, 420) + (summary.Length > 420 ? "…" : "");
            var paper = Ui.Card(Ui.Text(excerpt, 13), "SkyBrush");
            paper.Padding = new Thickness(10);
            paper.Margin = new Thickness(0, 7, 0, 7);
            detail.Children.Add(paper);
            if (summary.Length > 420)
                detail.Children.Add(new Expander { Header = "展开采集正文", Content = Ui.Text(summary, 13), Margin = new Thickness(0, 0, 0, 6) });
            var actions = EditorialBar();
            var anchor = rank ? J.Obj("kind", "hotspot", "platform", platform, "title", title.Text, "url", J.Text(row, "url"))
                : J.Obj("kind", "article", "id", J.Text(row, "id"), "title", title.Text, "url", J.Text(report, "url"));
            string sourcePlatform = rank ? platform : J.Text(report, "source_platform");
            string targetPlatform = Array.IndexOf(EditorialPlatforms, sourcePlatform) >= 0 ? sourcePlatform : platform;
            var produce = Ui.Button("制作整片", () => OpenTopicProduction(anchor, targetPlatform), true);
            produce.Margin = new Thickness(0, 0, 6, 5);
            actions.Children.Add(produce);
            var use = Ui.Button("高级素材组装", () => OpenEditorial(anchor, rank ? platform : null));
            use.Margin = new Thickness(0, 0, 6, 5);
            actions.Children.Add(use);
            string reportId = J.Text(report, "id");
            var writeCopy = Ui.Button("让本地 AI 写图文／文案", () => OpenWriting(
                title.Text, platform, summary, null, rank, false,
                reportId.Length > 0 ? new[] { reportId } : null));
            writeCopy.Margin = new Thickness(0, 0, 6, 5);
            actions.Children.Add(writeCopy);
            string sourceUrl = J.Text(report, "url"), rowUrl = J.Text(row, "url");
            if (IsEditorialWebLink(sourceUrl)) actions.Children.Add(Ui.Filter("打开原文 ↗", () => OpenEditorialPath(sourceUrl)));
            if (rank && rowUrl != sourceUrl && IsEditorialWebLink(rowUrl))
                actions.Children.Add(Ui.Filter("打开热点 ↗", () => OpenEditorialPath(rowUrl)));
            detail.Children.Add(actions);
            return detail;
        }

        private void OpenTopicProduction(IDictionary<string, object> anchor, string platform)
        {
            int platformIndex = Array.IndexOf(EditorialPlatforms, platform);
            if (platformIndex < 0) platformIndex = 2;
            var dialog = new Window { Title = "制作整片", Owner = this, Width = 600, Height = 660,
                MinWidth = 510, MinHeight = 500, WindowStartupLocation = WindowStartupLocation.CenterOwner };
            var body = new StackPanel { Margin = new Thickness(22) };
            body.Children.Add(EditorialStrong(J.Text(anchor, "title"), 18));
            body.Children.Add(Ui.Text("围绕当前热点制作专题，或选择本周科技／经济周报。按所选范围采集真实视频、照片和官方页面，生成解说并对齐画面。", 13, true));
            var target = EditorialChoice(EditorialPlatformNames, platformIndex, "目标平台");
            body.Children.Add(EditorialLabeled("目标平台", target));
            string[] scopes = { "topic", "week_tech", "week_economy" };
            var scope = EditorialChoice(new[] { "当前热点专题", "本周科技", "本周经济" }, 0, "内容范围");
            body.Children.Add(EditorialLabeled("内容范围", scope));
            var duration = Ui.Text("", 13, true);
            Action updateDuration = () => duration.Text = target.SelectedIndex == 2
                ? "B站：目标 12 分钟，按 10–15 分钟完整专题组织内容。"
                : "短视频：目标 2 分钟。";
            target.SelectionChanged += delegate {
                if (target.SelectedIndex != 2 && scope.SelectedIndex > 0) scope.SelectedIndex = 0;
                updateDuration();
            };
            scope.SelectionChanged += delegate { if (scope.SelectedIndex > 0) target.SelectedIndex = 2; };
            updateDuration();
            body.Children.Add(duration);
            var referenceUrl = EditorialField("", "参考案例链接（可选）");
            referenceUrl.MaxLength = 2048;
            body.Children.Add(EditorialLabeled("参考案例链接（可选）", referenceUrl));
            var referenceText = EditorialField("", "参考文案片段或表达风格（可选）", 90);
            referenceText.MaxLength = 12000;
            body.Children.Add(EditorialLabeled("参考文案片段或表达风格（可选）", referenceText));
            body.Children.Add(Ui.Text("参考用于借鉴叙事和节奏；事实与画面以本次选题的可核对资料为准。成片保存在本机，不会自动发布。", 12, true));
            var status = Ui.Text("", 13, true);
            status.Margin = new Thickness(0, 8, 0, 8);
            body.Children.Add(status);
            var actions = EditorialBar();
            var submit = Ui.Button("开始制作整片", delegate { }, true);
            var cancel = Ui.Button("取消", () => dialog.Close());
            actions.Children.Add(submit);
            actions.Children.Add(cancel);
            body.Children.Add(actions);
            bool busy = false;
            Action updateControls = () =>
            {
                submit.IsEnabled = cancel.IsEnabled = target.IsEnabled = scope.IsEnabled =
                    referenceUrl.IsEnabled = referenceText.IsEnabled = !busy;
            };
            dialog.Closing += (sender, args) => { if (busy) args.Cancel = true; };
            submit.Click += async (sender, args) =>
            {
                if (busy || !submit.IsEnabled) return;
                string url = referenceUrl.Text.Trim();
                if (url.Length > 0 && !IsEditorialWebLink(url))
                {
                    status.Text = "参考案例链接需为 http 或 https 网址，也可以留空。";
                    referenceUrl.Focus();
                    return;
                }
                string targetPlatform = EditorialPlatforms[target.SelectedIndex];
                string day = EditorialToday();
                if (productionJobs.ContainsKey(ProductionKey(targetPlatform, day)) || dailyCompilationBusy && day == dailyCompilationDay)
                {
                    status.Text = "此平台今天的工作稿正在处理，请完成后再制作整片。";
                    return;
                }
                busy = true;
                updateControls();
                status.Text = "正在建立整片任务…";
                try
                {
                    var payload = J.Obj("anchor", anchor, "scope", scopes[scope.SelectedIndex],
                        "target_seconds", targetPlatform == "bilibili" ? 720 : 120,
                        "reference_url", url, "reference_text", referenceText.Text.Trim());
                    var item = J.Map(await Worker.RunAsync("topic_prepare", targetPlatform, payload, day));
                    string id = J.Text(item, "id");
                    if (id.Length == 0) throw new InvalidOperationException("整片任务未返回作品编号，请重试。");
                    busy = false;
                    dialog.Close();
                    OpenProduction(targetPlatform, day);
                    await RunProductionJob("topic_produce", targetPlatform, day, J.Obj("id", id),
                        "正在制作整片…", "整片已生成，尚未发布");
                }
                catch (Exception error)
                {
                    status.Text = "整片任务未完成：" + error.Message;
                    Notify(status.Text);
                }
                finally { busy = false; updateControls(); }
            };
            dialog.Content = Ui.Scroll(body);
            dialog.ShowDialog();
        }

        private FrameworkElement BuildAssembly(EditorialAssembly draft)
        {
            var page = new StackPanel();
            var back = Ui.Button("‹  返回选题", () => { assembly = null; Navigate("topics"); });
            back.HorizontalAlignment = HorizontalAlignment.Left;
            back.Margin = new Thickness(0, 0, 0, 16);
            page.Children.Add(back);
            var content = new StackPanel();
            page.Children.Add(content);
            Action draw = () =>
            {
                content.Children.Clear();
                content.Children.Add(AssemblyContents(draft));
            };
            if (draft.Options != null) draw();
            else
            {
                var status = Ui.Text("正在关联本机报道…", 14, true);
                content.Children.Add(EditorialHeading("素材组装", "只使用你接入的报道、笔记和本机素材。"));
                content.Children.Add(Ui.Card(status));
                page.Loaded += async (sender, args) =>
                {
                    if (draft.Options != null) { draw(); return; }
                    try
                    {
                        var options = J.Map(await Worker.RunAsync("editorial_sources", draft.Platform, draft.Anchor, draft.Day));
                        if (assembly != draft) return;
                        if (draft.Options == null) LoadAssembly(draft, options);
                        if (!draft.Collected && !J.Items(J.Get(options, "recommended")).Any())
                        {
                            draft.Collected = true;
                            status.Text = "正在按「" + J.Text(J.Map(J.Get(options, "anchor")), "title") + "」查找同题资料…";
                            try
                            {
                                var found = J.Map(await Worker.RunAsync("editorial_collect", draft.Platform,
                                    J.Obj("anchor", draft.Anchor), draft.Day));
                                if (assembly != draft) return;
                                LoadAssembly(draft, found);
                            }
                            catch (Exception error) { draft.Options["warning"] = "本题资料采集未完成：" + error.Message; }
                        }
                        draw();
                    }
                    catch (Exception error)
                    {
                        status.Text = error.Message;
                        var retry = Ui.Button("重新读取", () => Navigate("topics"));
                        retry.HorizontalAlignment = HorizontalAlignment.Left;
                        content.Children.Add(retry);
                    }
                };
            }
            return Ui.Scroll(page);
        }

        private static void LoadAssembly(EditorialAssembly draft, IDictionary<string, object> options)
        {
            draft.Options = options;
            foreach (var report in J.Rows(J.Get(options, "reports")))
            {
                if (J.Text(report, "source_type") == "author_notes") continue;
                string id = J.Text(report, "id");
                string url = J.Text(report, "url");
                draft.Records["report:" + id] = new EditorialInput {
                    Kind = "report", Title = J.Text(report, "title"), Report = report,
                    IsLink = IsEditorialWebLink(url) && EditorialLinkId(url) == id
                };
            }
            if (draft.Saved == null)
            {
                if (draft.Ordered.Count == 0)
                {
                    var anchor = J.Map(J.Get(options, "anchor"));
                    string anchorKey = "report:" + J.Text(anchor, "id");
                    if (draft.Records.ContainsKey(anchorKey) && draft.Records[anchorKey].Available)
                    {
                        draft.Ordered.Add(anchorKey);
                        draft.Active = anchorKey;
                    }
                    else
                    {
                        string firstRec = J.Items(J.Get(options, "recommended")).Select(v => "report:" + Convert.ToString(v))
                            .FirstOrDefault(k => draft.Records.ContainsKey(k) && draft.Records[k].Available);
                        if (firstRec != null)
                        {
                            draft.Ordered.Add(firstRec);
                            draft.Active = firstRec;
                        }
                    }
                }
                return;
            }
            var brief = J.Map(J.Get(draft.Saved, "brief"));
            int index = 0;
            foreach (var note in J.Items(J.Get(brief, "notes")))
                AddAssemblyInput(draft, "note:" + index++, new EditorialInput { Kind = "note", Title = Convert.ToString(note) });
            index = 0;
            foreach (var path in J.Items(J.Get(brief, "asset_paths")).Select(value => Convert.ToString(value)))
                AddAssemblyInput(draft, "asset:" + index++, new EditorialInput { Kind = "asset", Title = Path.GetFileName(path), Path = path });
            var sequence = J.Items(J.Get(brief, "sequence")).Select(value => Convert.ToString(value)).ToList();
            if (sequence.Count == 0)
                sequence.AddRange(J.Rows(J.Get(draft.Saved, "reports")).Select(row => "report:" + J.Text(row, "id")));
            foreach (string key in sequence.Distinct())
                if (draft.Records.ContainsKey(key) && draft.Records[key].Available) draft.Ordered.Add(key);
            draft.Active = draft.Ordered.FirstOrDefault();
            string focus = J.Text(brief, "focus_id");
            if (focus.Length > 0 && !focus.StartsWith("note:", StringComparison.Ordinal)) focus = "report:" + focus;
            if (draft.Ordered.Contains(focus))
            {
                draft.Focus = focus;
                draft.Quote = J.Text(brief, "focus_quote");
            }
            draft.Saved = null;
        }

        private FrameworkElement AssemblyContents(EditorialAssembly draft)
        {
            var page = new StackPanel();
            var anchor = J.Map(J.Get(draft.Options, "anchor"));
            page.Children.Add(EditorialHeading("选配资料并交给本地 AI", "左侧勾选参考报道（已自动选入最相关的一篇），右侧也可补充自有文案或本地视频/图片，确认后直接交给本地 AI 生成。"));
            var columns = new Grid();
            columns.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(3, GridUnitType.Star) });
            columns.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(2, GridUnitType.Star), MinWidth = 264 });
            page.Children.Add(columns);
            var left = new StackPanel { Margin = new Thickness(0, 0, 20, 0) };
            var right = new StackPanel();
            columns.Children.Add(left);
            Grid.SetColumn(right, 1);
            columns.Children.Add(right);

            var anchorPaper = new StackPanel();
            anchorPaper.Children.Add(EditorialKicker("当前选题"));
            anchorPaper.Children.Add(EditorialStrong(J.Text(anchor, "title"), 20));
            var paper = EditorialRow(anchorPaper);
            paper.Background = EditorialTint;
            paper.Padding = new Thickness(16);
            paper.Margin = new Thickness(0, 0, 0, 16);
            left.Children.Add(paper);
            left.Children.Add(EditorialStrong("本题资料", 18));
            var scope = EditorialChoice(new[] { "与本题相关", "全部采集库（手动选）" }, 0, "资料范围");
            left.Children.Add(scope);
            var search = EditorialField("", "搜索本机采集库");
            left.Children.Add(EditorialLabeled("资料关键词（留空使用选题，可自行修改）", search));
            var collect = new Button { Content = "按关键词联网找资料", HorizontalAlignment = HorizontalAlignment.Left };
            left.Children.Add(collect);
            var collectionStatus = Ui.Text(J.Text(draft.Options, "warning"), 13, true);
            left.Children.Add(collectionStatus);
            var branches = new StackPanel { Margin = new Thickness(0, 8, 0, 0) };
            left.Children.Add(new Border {
                BorderBrush = EditorialLine, BorderThickness = new Thickness(2, 0, 0, 0),
                Padding = new Thickness(8, 0, 0, 0), Margin = new Thickness(4, 0, 0, 0), Child = branches
            });

            right.Children.Add(EditorialStrong("交给 AI 的素材", 18));
            var summary = Ui.Text("", 13, true);
            summary.Margin = new Thickness(0, 3, 0, 10);
            right.Children.Add(summary);
            var queue = new ListBox { MinHeight = 86, MaxHeight = 240, BorderBrush = EditorialLine };
            ScrollViewer.SetHorizontalScrollBarVisibility(queue, ScrollBarVisibility.Disabled);
            AutomationProperties.SetName(queue, "按优先级排序的已接入素材");
            right.Children.Add(queue);
            var tools = EditorialBar();
            var up = new Button { Content = "上移" };
            var down = new Button { Content = "下移" };
            var focus = new Button { Content = "设为重点" };
            var drop = new Button { Content = "断开" };
            foreach (Button action in new[] { up, down, focus, drop })
            {
                action.Margin = new Thickness(0, 4, 4, 4);
                tools.Children.Add(action);
            }
            right.Children.Add(tools);
            var focusText = Ui.Text("", 13);
            focusText.Foreground = EditorialBlue;
            focusText.Margin = new Thickness(0, 2, 0, 12);
            right.Children.Add(focusText);

            var note = EditorialField("", "创作说明、笔记或 HTTPS 原文链接", 64);
            right.Children.Add(EditorialLabeled("创作说明、笔记或原文链接", note));
            right.Children.Add(Ui.Text("原创视频可不选报道：写清想拍什么和过程，再加入图片／视频。新闻内容需选可核对的原文。", 12, true));
            var add = new Button { Content = "添加", Margin = new Thickness(0, 0, 6, 4) };
            var files = new Button { Content = "添加本机媒体", Margin = new Thickness(0, 0, 0, 4) };
            var extras = EditorialBar();
            extras.Children.Add(add);
            extras.Children.Add(files);
            right.Children.Add(extras);
            var settings = EditorialBar();
            var platform = EditorialChoice(EditorialPlatformNames, Array.IndexOf(EditorialPlatforms, draft.Platform), "制作平台");
            var durations = EditorialChoice(new string[0], -1, "目标时长");
            var videoType = EditorialChoice(new[] { "自动判断视频类型" }, 0, "视频类型");
            settings.Children.Add(platform);
            settings.Children.Add(durations);
            settings.Children.Add(videoType);
            right.Children.Add(settings);
            var outputKind = EditorialChoice(new[] { "视频 · 本地 AI 分镜、选镜与试剪", "图文 · 小红书实拍图片与正文", "仅文案 · 多平台口播与发布配文" },
                draft.Platform == "xiaohongshu" ? 0 : 0, "制作内容");
            right.Children.Add(EditorialLabeled("要让本地 AI 生成什么", outputKind));
            var submit = new Button { Content = "交给本地 AI 生成视频分镜 →", Style = (Style)FindResource("PrimaryButton"),
                Margin = new Thickness(0, 4, 6, 4) };
            var team = Ui.Button("发给团队电脑代跑", () =>
            {
                string selectedReports = String.Join("\n\n", draft.Ordered.Where(key => draft.Records[key].Kind == "report")
                    .Select(key => draft.Records[key].Title + "\n" + EditorialTruncate(J.Text(draft.Records[key].Report, "summary"), 420)));
                if (selectedReports.Length > 0) OpenTeamCompute(selectedReports);
            });
            var actions = EditorialBar();
            actions.Children.Add(submit);
            actions.Children.Add(team);
            right.Children.Add(actions);
            var status = Ui.Text(J.Text(draft.Options, "warning"), 13, true);
            status.Margin = new Thickness(0, 8, 0, 0);
            right.Children.Add(status);

            Action updateControls = () =>
            {
                int selected = draft.Ordered.IndexOf(draft.Active);
                up.IsEnabled = !draft.Busy && selected > 0;
                down.IsEnabled = !draft.Busy && selected >= 0 && selected < draft.Ordered.Count - 1;
                focus.IsEnabled = !draft.Busy && selected >= 0 && draft.Records[draft.Active].Kind != "asset";
                drop.IsEnabled = !draft.Busy && selected >= 0;
                bool hasReports = draft.Ordered.Any(key => draft.Records[key].Kind == "report");
                bool hasNotes = draft.Ordered.Any(key => draft.Records[key].Kind == "note");
                submit.Content = outputKind.SelectedIndex == 0 ? "交给本地 AI 生成视频分镜 →"
                    : outputKind.SelectedIndex == 1 ? "交给本地 AI 生成实拍图文 →" : "交给本地 AI 生成文案稿 →";
                submit.IsEnabled = !draft.Busy && !draft.Reading && (hasReports || hasNotes || outputKind.SelectedIndex > 0);
                team.IsEnabled = !draft.Busy && hasReports;
                collect.IsEnabled = !draft.Busy && !draft.Reading;
                outputKind.IsEnabled = !draft.Busy;
                durations.Visibility = outputKind.SelectedIndex == 0 && draft.Platform != "bilibili" ? Visibility.Visible : Visibility.Collapsed;
                videoType.Visibility = outputKind.SelectedIndex == 0 && videoType.Items.Count > 1 ? Visibility.Visible : Visibility.Collapsed;
                note.IsEnabled = files.IsEnabled = platform.IsEnabled = durations.IsEnabled = videoType.IsEnabled = !draft.Busy;
                add.IsEnabled = !draft.Busy && !draft.Reading;
                branches.IsEnabled = !draft.Busy;
                summary.Text = draft.Ordered.Count == 0
                    ? "尚未选用资料：请在左侧点「+ 选用这篇」，或在下方填写你的文案说明"
                    : "已选入 " + draft.Ordered.Count + " 项资料 · 本地 AI 将按从上到下顺序引用";
                focusText.Text = "讲述重点  ·  " + (String.IsNullOrEmpty(draft.Focus) ? "尚未指定"
                    : String.IsNullOrEmpty(draft.Quote) ? draft.Records[draft.Focus].Title : draft.Quote);
            };
            Action redraw = null;
            Func<string, bool> connect = key =>
            {
                if (draft.Ordered.Contains(key)) return true;
                if (!draft.Records[key].Available) return false;
                if (draft.Ordered.Count(k => draft.Records[k].Kind == draft.Records[key].Kind) >= 8)
                {
                    status.Text = "每类素材最多接入 8 项。";
                    return false;
                }
                draft.Ordered.Add(key);
                draft.Active = key;
                return true;
            };
            Action<string> disconnect = key =>
            {
                draft.Ordered.Remove(key);
                if (draft.Focus == key) draft.Focus = draft.Quote = "";
                if (draft.Active == key) draft.Active = draft.Ordered.FirstOrDefault();
            };
            Action drawBranches = () =>
            {
                branches.Children.Clear();
                string query = search.Text.Trim();
                IEnumerable<string> keys = draft.Ordered.Concat(new[] { "report:" + J.Text(anchor, "id") })
                    .Concat(J.Items(J.Get(draft.Options, "recommended")).Select(value => "report:" + value))
                    .Concat(draft.Manual);
                keys = keys.Concat(J.Items(J.Get(draft.Options, "searched_ids")).Select(value => "report:" + value));
                if (scope.SelectedIndex == 1) keys = keys.Concat(draft.Records.Keys);
                foreach (string key in keys.Distinct().Where(k => draft.Records.ContainsKey(k)))
                {
                    var record = draft.Records[key];
                    if (!EditorialMatches(query, record.Title, J.Text(record.Report, "source"))) continue;
                    bool selected = draft.Ordered.Contains(key);
                    branches.Children.Add(AssemblyBranch(draft, key, record, selected,
                        () => { if (selected) disconnect(key); else connect(key); redraw(); },
                        quote =>
                        {
                            if (!connect(key)) return;
                            draft.Active = draft.Focus = key;
                            draft.Quote = quote;
                            redraw();
                        }, asset => {
                            connect(AssemblyExtra(draft, "asset", J.Text(asset, "title"), J.Text(asset, "path")));
                            redraw();
                        }));
                }
                if (branches.Children.Count == 0)
                    branches.Children.Add(Ui.Text("暂无匹配资料。可修改关键词联网查找、切换全部采集库，或在右侧补充文字和图片／视频。不会推荐无关报道。", 13, true));
            };
            bool filling = false;
            redraw = () =>
            {
                filling = true;
                queue.Items.Clear();
                foreach (string key in draft.Ordered)
                {
                    var row = draft.Records[key];
                    var caption = new StackPanel();
                    caption.Children.Add(EditorialKicker((queue.Items.Count + 1).ToString("00") + "  ·  " + EditorialKind(row.Kind)
                                                        + (draft.Focus == key ? "  ·  重点" : "")));
                    caption.Children.Add(Ui.Text(EditorialFirstLine(row.Title), 14));
                    queue.Items.Add(new ListBoxItem { Content = caption, Tag = key, Padding = new Thickness(8, 4, 8, 4) });
                }
                queue.SelectedIndex = draft.Ordered.IndexOf(draft.Active);
                filling = false;
                drawBranches();
                updateControls();
            };
            queue.SelectionChanged += (sender, args) =>
            {
                if (filling) return;
                var current = queue.SelectedItem as ListBoxItem;
                draft.Active = current == null ? null : (string)current.Tag;
                updateControls();
            };
            Action<int> move = delta =>
            {
                int index = draft.Ordered.IndexOf(draft.Active);
                int next = index + delta;
                if (index < 0 || next < 0 || next >= draft.Ordered.Count) return;
                draft.Ordered.RemoveAt(index);
                draft.Ordered.Insert(next, draft.Active);
                redraw();
            };
            up.Click += (sender, args) => move(-1);
            down.Click += (sender, args) => move(1);
            focus.Click += (sender, args) => { draft.Focus = draft.Active; draft.Quote = ""; redraw(); };
            drop.Click += (sender, args) => { disconnect(draft.Active); redraw(); };
            search.TextChanged += (sender, args) => drawBranches();
            scope.SelectionChanged += (sender, args) => drawBranches();
            outputKind.SelectionChanged += (sender, args) => {
                if (outputKind.SelectedIndex == 1 && draft.Platform != "xiaohongshu") {
                    outputKind.SelectedIndex = 2;
                    status.Text = "当前实拍图文制作支持小红书。此平台可制作视频或文案。";
                }
                updateControls();
            };
            extras.Children.Add(Ui.Button("搜索图片／视频", () => OpenMaterialSearch(draft.Platform, draft.Day, null,
                asset => {
                    connect(AssemblyExtra(draft, "asset", J.Text(asset, "title"), J.Text(asset, "path")));
                    redraw();
                }, J.Text(anchor, "title"))));
            collect.Click += async (sender, args) =>
            {
                draft.Reading = true;
                updateControls();
                collectionStatus.Text = "正在查找本次关键词对应的公开资料…";
                try
                {
                    var found = J.Map(await Worker.RunAsync("editorial_collect", draft.Platform,
                        J.Obj("anchor", draft.Anchor, "query", search.Text.Trim()), draft.Day));
                    LoadAssembly(draft, found);
                    collectionStatus.Text = "「" + J.Text(found, "collection_query") + "」找到 " + J.Text(found, "collected") + " 篇可读原文。";
                    search.Clear();
                }
                catch (Exception error) { collectionStatus.Text = error.Message; }
                finally { draft.Reading = false; redraw(); }
            };
            bool legacyDuration = draft.Platform == "xiaohongshu" && draft.Seconds == 720;
            var videoCatalog = J.Obj() as IDictionary<string, object>;
            string[] typeIds = { "" };
            bool syncingTypes = false;
            Action syncTypes = () =>
            {
                syncingTypes = true;
                var choices = J.Rows(J.Get(videoCatalog, draft.Platform)).ToArray();
                typeIds = new[] { "" }.Concat(choices.Select(row => J.Text(row, "id"))).ToArray();
                videoType.ItemsSource = new[] { "自动判断视频类型" }.Concat(choices.Select(row => J.Text(row, "name"))).ToArray();
                videoType.SelectedIndex = Math.Max(0, Array.IndexOf(typeIds, draft.VideoStyle));
                syncingTypes = false;
                updateControls();
            };
            syncTypes();
            page.Loaded += async (sender, args) =>
            {
                try { videoCatalog = await LoadVideoCatalog(); syncTypes(); }
                catch (Exception error) { status.Text = error.Message; }
            };
            videoType.SelectionChanged += (sender, args) =>
            {
                if (!syncingTypes && videoType.SelectedIndex >= 0) draft.VideoStyle = typeIds[videoType.SelectedIndex];
            };
            Action syncDuration = () =>
            {
                var values = legacyDuration && draft.Platform == "xiaohongshu"
                    ? new[] { "1 分钟", "2 分钟", "3 分钟", "12 分钟" } : new[] { "1 分钟", "2 分钟", "3 分钟" };
                durations.ItemsSource = values;
                durations.SelectedIndex = draft.Seconds == 720 && values.Length == 4 ? 3
                    : Math.Max(0, Math.Min(2, draft.Seconds == 720 ? 1 : draft.Seconds / 60 - 1));
                updateControls();
            };
            syncDuration();
            platform.SelectionChanged += (sender, args) =>
            {
                if (platform.SelectedIndex < 0) return;
                draft.Platform = EditorialPlatforms[platform.SelectedIndex];
                if (draft.Platform != "xiaohongshu" && outputKind.SelectedIndex == 1) outputKind.SelectedIndex = 0;
                draft.VideoStyle = "";
                syncTypes();
                syncDuration();
                draft.Seconds = durations.SelectedIndex == 3 ? 720 : (durations.SelectedIndex + 1) * 60;
            };
            durations.SelectionChanged += (sender, args) =>
            {
                if (durations.SelectedIndex >= 0)
                    draft.Seconds = durations.SelectedIndex == 3 ? 720 : (durations.SelectedIndex + 1) * 60;
            };
            add.Click += async (sender, args) =>
            {
                string value = note.Text.Trim();
                if (value.Length == 0) return;
                if (value.Length > 2000) { status.Text = "补充内容最多 2000 字。"; return; }
                if (IsEditorialWebLink(value))
                {
                    draft.Reading = true;
                    status.Text = "正在读取公开报道…";
                    updateControls();
                    try
                    {
                        var report = J.Map(await Worker.RunAsync("editorial_link", payload: J.Obj("url", value), day: draft.Day));
                        string key = "report:" + J.Text(report, "id");
                        AddAssemblyInput(draft, key, new EditorialInput {
                            Kind = "report", Title = J.Text(report, "title"), Report = report, IsLink = true
                        });
                        draft.Expanded.Add(key);
                        status.Text = "已读入候选报道。核对正文后可接入。";
                        if (note.Text.Trim() == value) note.Clear();
                    }
                    catch (Exception error) { status.Text = error.Message; }
                    finally { draft.Reading = false; }
                }
                else
                {
                    string key = AssemblyExtra(draft, "note", value, null);
                    if (connect(key)) note.Clear();
                }
                redraw();
            };
            files.Click += (sender, args) =>
            {
                var picker = new OpenFileDialog { Filter = EditorialMediaFilter, Title = "选择本机媒体", Multiselect = true };
                if (picker.ShowDialog(this) != true) return;
                foreach (string path in picker.FileNames)
                    if (!connect(AssemblyExtra(draft, "asset", Path.GetFileName(path), path))) break;
                redraw();
            };
            submit.Click += async (sender, args) =>
            {
                if (draft.Busy || !submit.IsEnabled) return;
                if (outputKind.SelectedIndex > 0)
                {
                    string facts = String.Join("\n\n", draft.Ordered.Where(key => draft.Records[key].Kind != "asset")
                        .Select(key => draft.Records[key].Kind == "report"
                            ? draft.Records[key].Title + "\n" + J.Text(draft.Records[key].Report, "summary")
                            : draft.Records[key].Title));
                    string[] paths = draft.Ordered.Where(key => draft.Records[key].Kind == "asset")
                        .Select(key => draft.Records[key].Path).ToArray();
                    string[] sources = draft.Ordered.Where(key => draft.Records[key].Kind == "report")
                        .Select(key => J.Text(draft.Records[key].Report, "id")).Where(id => id.Length > 0).ToArray();
                    OpenWriting(J.Text(anchor, "title"), draft.Platform, facts, paths,
                        J.Text(draft.Anchor, "kind") == "hotspot", outputKind.SelectedIndex == 2, sources);
                    return;
                }
                draft.Busy = true;
                status.Text = "正在保存本次选用的资料…";
                updateControls();
                try
                {
                    string target = draft.Platform;
                    var item = J.Map(await Worker.RunAsync("editorial_create", target, AssemblyPayload(draft), draft.Day));
                    if (assembly == draft)
                    {
                        assembly = null;
                        OpenProduction(target, draft.Day);
                    }
                    await RunProductionJob("editorial_preview", target, draft.Day,
                        J.Obj("id", J.Text(item, "id"), "direct", true), "正在生成开场试剪…", "开场试剪已就绪");
                }
                catch (Exception error) { status.Text = error.Message; Notify(error.Message); }
                finally { draft.Busy = false; updateControls(); }
            };
            redraw();
            return page;
        }

        private void OpenCustomTopic()
        {
            var dialog = new Window { Title = "自定选题", Owner = this, Width = 480, Height = 230,
                WindowStartupLocation = WindowStartupLocation.CenterOwner, ResizeMode = ResizeMode.NoResize };
            var body = new StackPanel { Margin = new Thickness(22) };
            var title = EditorialField("", "选题");
            title.MaxLength = 160;
            body.Children.Add(EditorialLabeled("输入你想做的主题，不限榜单", title));
            var target = EditorialChoice(EditorialPlatformNames, 0, "平台");
            body.Children.Add(target);
            body.Children.Add(Ui.Button("找素材并制作", () => {
                if (title.Text.Trim().Length == 0) { title.Focus(); return; }
                dialog.Close();
                OpenEditorial(J.Obj("kind", "custom", "title", title.Text.Trim()), EditorialPlatforms[target.SelectedIndex]);
            }, true));
            dialog.Content = body;
            dialog.ShowDialog();
        }

        private FrameworkElement AssemblyBranch(EditorialAssembly draft, string key, EditorialInput row,
                                                bool selected, Action toggle, Action<string> useQuote, Action<IDictionary<string, object>> useMedia)
        {
            var panel = new DockPanel();
            var select = Ui.Button(selected ? "✓ 已选用" : "+ 选用这篇", toggle, !selected && row.Available);
            select.IsEnabled = row.Available;
            select.VerticalAlignment = VerticalAlignment.Top;
            select.Margin = new Thickness(10, 0, 0, 0);
            DockPanel.SetDock(select, Dock.Right);
            panel.Children.Add(select);
            var header = new StackPanel();
            string kind = row.Kind == "report" && J.Text(row.Report, "content_type") == "photo_note" ? "图文笔记" : EditorialKind(row.Kind);
            header.Children.Add(EditorialKicker(kind + "  ·  " + (selected ? "已接入"
                : row.Available ? "候选" : "待补正文")
                + (Equals(J.Get(row.Report, "requires_read"), true) ? " · 摘要，生成前读取原文" : "")));
            header.Children.Add(EditorialStrong(EditorialFirstLine(row.Title), 14));
            var details = new StackPanel { Margin = new Thickness(10, 6, 0, 0) };
            if (row.Kind == "report")
            {
                string source = J.Text(row.Report, "source");
                details.Children.Add(Ui.Text(source.Length == 0 ? "来源未标注" : source, 13, true));
                if (J.Text(row.Report, "source_type") == "reference")
                {
                    string published = J.Text(row.Report, "published");
                    details.Children.Add(Ui.Text("参考资料 · " + (published.Length == 0 ? "原发布日期未标注" : "原发布日期 " + published), 12, true));
                }
                string url = J.Text(row.Report, "url");
                if (IsEditorialWebLink(url)) details.Children.Add(EditorialLink("打开原文", url));
                if (J.Items(J.Get(row.Report, "media_candidates")).Any())
                    details.Children.Add(Ui.Button("选择这篇原文的图片／视频", () => OpenMaterialSearch(
                        draft.Platform, draft.Day, null, useMedia, row.Title, J.Text(row.Report, "id"))));
                string body = J.Text(row.Report, "summary");
                var excerpts = EditorialExcerpts(body, J.Text(J.Map(J.Get(draft.Options, "anchor")), "title"));
                foreach (string quote in excerpts)
                {
                    var excerpt = new StackPanel();
                    excerpt.Children.Add(Ui.Text(quote, 14));
                    var action = Ui.Button(draft.Focus == key && draft.Quote == quote ? "已设为重点"
                        : selected ? "用这段作重点" : "接入并用这段作重点", () => useQuote(quote));
                    action.IsEnabled = row.Available && (draft.Focus != key || draft.Quote != quote);
                    action.HorizontalAlignment = HorizontalAlignment.Left;
                    excerpt.Children.Add(action);
                    details.Children.Add(new Border { Background = EditorialTint, Padding = new Thickness(10, 6, 10, 6),
                        Margin = new Thickness(0, 6, 0, 0), Child = excerpt });
                }
                if (excerpts.Count == 0) details.Children.Add(Ui.Text("暂无可用正文摘录。", 13, true));
                if (body.Length > 0)
                    details.Children.Add(new Expander { Header = "阅读采集正文", Content = Ui.Text(body, 14),
                        Margin = new Thickness(0, 8, 0, 0) });
            }
            else details.Children.Add(Ui.Text(row.Path ?? row.Title, 14));
            var disclosure = new Expander { Header = header, Content = details, IsExpanded = draft.Expanded.Contains(key) };
            disclosure.Expanded += (sender, args) => { if (args.Source == disclosure) draft.Expanded.Add(key); };
            disclosure.Collapsed += (sender, args) => { if (args.Source == disclosure) draft.Expanded.Remove(key); };
            panel.Children.Add(disclosure);
            var card = EditorialRow(panel);
            if (selected) card.BorderBrush = EditorialBlue;
            var node = new DockPanel();
            var route = new Canvas { Width = 16, IsHitTestVisible = false };
            var line = new Line { X1 = 0, X2 = 16, Y1 = 21, Y2 = 21, Stroke = selected ? EditorialBlue : Ui.Color("MutedBrush"),
                StrokeThickness = selected ? 2 : 1 };
            if (!selected) line.StrokeDashArray = new DoubleCollection { 2, 2 };
            route.Children.Add(line);
            if (selected)
            {
                var point = new Ellipse { Width = 6, Height = 6, Fill = EditorialBlue };
                Canvas.SetLeft(point, 0);
                Canvas.SetTop(point, 18);
                route.Children.Add(point);
            }
            DockPanel.SetDock(route, Dock.Left);
            node.Children.Add(route);
            node.Children.Add(card);
            return node;
        }

        private static object AssemblyPayload(EditorialAssembly draft)
        {
            var notes = new List<string>();
            var assets = new List<string>();
            var reports = new List<string>();
            var extras = new List<object>();
            var sequence = new List<string>();
            string focus = "";
            foreach (string key in draft.Ordered)
            {
                var input = draft.Records[key];
                string alias = key;
                if (input.Kind == "report")
                {
                    string id = J.Text(input.Report, "id");
                    reports.Add(id);
                    if (input.IsLink) extras.Add(J.Obj("id", id, "url", J.Text(input.Report, "url")));
                }
                else if (input.Kind == "note") { alias = "note:" + notes.Count; notes.Add(input.Title); }
                else { alias = "asset:" + assets.Count; assets.Add(input.Path); }
                sequence.Add(alias);
                if (key == draft.Focus) focus = input.Kind == "report" ? alias.Substring("report:".Length) : alias;
            }
            var brief = J.Obj("notes", notes, "asset_paths", assets, "sequence", sequence, "extra_reports", extras, "focus_id", focus);
            if (focus.Length > 0 && !String.IsNullOrEmpty(draft.Quote)) brief["focus_quote"] = draft.Quote;
            return J.Obj("anchor", J.Get(draft.Options, "anchor"), "source_ids", reports, "brief", brief,
                         "video_style", draft.VideoStyle ?? "",
                         "target_seconds", draft.Platform == "bilibili" ? 720 : draft.Seconds);
        }

        private static string AssemblyExtra(EditorialAssembly draft, string kind, string title, string path)
        {
            var existing = draft.Records.FirstOrDefault(entry => entry.Value.Kind == kind &&
                (kind == "asset" ? String.Equals(path, entry.Value.Path, StringComparison.OrdinalIgnoreCase) : entry.Value.Title == title));
            if (existing.Key != null) return existing.Key;
            string key = kind + ":" + draft.Records.Count(entry => entry.Value.Kind == kind);
            AddAssemblyInput(draft, key, new EditorialInput { Kind = kind, Title = title, Path = path });
            return key;
        }

        private static void AddAssemblyInput(EditorialAssembly draft, string key, EditorialInput input)
        {
            draft.Records[key] = input;
            if (!draft.Manual.Contains(key)) draft.Manual.Add(key);
        }

        private static string EditorialTruncate(string value, int max) { return value.Length > max ? value.Substring(0, max) : value; }

        private static List<string> EditorialExcerpts(string body, string topic)
        {
            string title = Regex.Replace(topic.ToLowerInvariant(), @"\W+", "");
            var terms = Enumerable.Range(0, Math.Max(0, title.Length - 1)).Select(index => title.Substring(index, 2)).Distinct().ToList();
            return Regex.Matches(body, @"[^。！？\r\n]+[。！？]?").Cast<Match>().Select(match => match.Value.Trim())
                .Where(value => value.Length >= 8).Select(value => EditorialTruncate(value, 280)).Distinct()
                .OrderByDescending(value => terms.Count(term => value.IndexOf(term, StringComparison.OrdinalIgnoreCase) >= 0))
                .Take(3).ToList();
        }

        private static string EditorialLinkId(string url)
        {
            int fragment = url.IndexOf('#');
            string identity = "editorial-link:" + (fragment < 0 ? url : url.Substring(0, fragment));
            using (var hash = SHA256.Create())
                return BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(identity)), 0, 10).Replace("-", "").ToLowerInvariant();
        }

        private static bool EditorialMatches(string query, string title, string source)
        {
            query = query.Trim();
            return query.Length == 0 || (title + " " + source).IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static string EditorialFirstLine(string value) { return value.Split(new[] { '\r', '\n' }, 2)[0]; }

        private static string EditorialKind(string kind) { return kind == "report" ? "报道" : kind == "note" ? "笔记" : "本机素材"; }

        private static string EditorialToday() { return DateTime.Today.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture); }

        private static string EditorialBoardTime(string timestamp)
        {
            double seconds;
            if (!Double.TryParse(timestamp, NumberStyles.Float, CultureInfo.InvariantCulture, out seconds)
                || Double.IsNaN(seconds) || seconds < 0 || seconds > 253402300799) return "";
            return new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc).AddSeconds(seconds).ToLocalTime().ToString("MM-dd HH:mm");
        }

        private static string EditorialPlatformName(string platform)
        {
            int index = Array.IndexOf(EditorialPlatforms, platform);
            return index >= 0 ? EditorialPlatformNames[index] : platform;
        }

        private static StackPanel EditorialHeading(string title, string description)
        {
            var panel = new StackPanel { Margin = new Thickness(0, 0, 0, 16) };
            panel.Children.Add(Ui.Title(title));
            panel.Children.Add(Ui.Text(description, 14, true));
            return panel;
        }

        private static TextBlock EditorialStrong(string title, double size)
        {
            var text = Ui.Text(title, size);
            text.FontWeight = FontWeights.SemiBold;
            text.TextWrapping = TextWrapping.Wrap;
            return text;
        }

        private static TextBlock EditorialKicker(string title)
        {
            var text = EditorialStrong(title, 12);
            text.Foreground = EditorialBlue;
            text.Margin = new Thickness(0, 0, 0, 4);
            return text;
        }

        private static WrapPanel EditorialBar() { return new WrapPanel { VerticalAlignment = VerticalAlignment.Center }; }

        private static Border EditorialRow(UIElement element)
        {
            return new Border { BorderBrush = EditorialLine, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(7),
                Background = Ui.Color("SurfaceBrush"), Padding = new Thickness(12, 9, 12, 9), Margin = new Thickness(0, 0, 0, 6), Child = element };
        }

        private static StackPanel EditorialLabeled(string title, Control control)
        {
            var field = new StackPanel { Margin = new Thickness(0, 5, 8, 6) };
            field.Children.Add(Ui.Text(title, 12, true));
            field.Children.Add(control);
            AutomationProperties.SetName(control, title);
            return field;
        }

        private static ComboBox EditorialChoice(IEnumerable<string> values, int index, string label)
        {
            var choice = new ComboBox { ItemsSource = values, SelectedIndex = index, MinWidth = 94, Margin = new Thickness(0, 2, 8, 2) };
            AutomationProperties.SetName(choice, label);
            return choice;
        }

        private static TextBox EditorialField(string value, string label, double minHeight = 0)
        {
            var field = new TextBox { Text = value, TextWrapping = TextWrapping.Wrap, AcceptsReturn = minHeight > 0,
                VerticalScrollBarVisibility = minHeight > 0 ? ScrollBarVisibility.Auto : ScrollBarVisibility.Disabled,
                Margin = new Thickness(0, 3, 0, 2) };
            if (minHeight > 0) field.Height = minHeight;
            AutomationProperties.SetName(field, label);
            return field;
        }

        private static bool IsEditorialWebLink(string value)
        {
            Uri uri;
            return Uri.TryCreate(value, UriKind.Absolute, out uri) && (uri.Scheme == "https" || uri.Scheme == "http");
        }

        private Button EditorialLink(string caption, string url)
        {
            var button = Ui.Button(caption + " ↗", () => OpenEditorialPath(url));
            button.Content = Ui.Text(caption + " ↗", 13);
            button.HorizontalAlignment = HorizontalAlignment.Left;
            AutomationProperties.SetName(button, caption);
            return button;
        }

        private void OpenEditorialPath(string path)
        {
            try
            {
                if (!IsEditorialWebLink(path) && !File.Exists(path) && !Directory.Exists(path))
                    throw new IOException("路径不可用。");
                Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
            }
            catch (Exception error) { Notify(error.Message); }
        }

        private static Image EditorialImage(string path, double height, int decodeWidth)
        {
            var image = new Image { Height = height, Stretch = Stretch.Uniform };
            if (!File.Exists(path)) return image;
            try
            {
                var bitmap = new BitmapImage();
                bitmap.BeginInit();
                bitmap.CacheOption = BitmapCacheOption.OnLoad;
                bitmap.DecodePixelWidth = decodeWidth;
                bitmap.UriSource = new Uri(Path.GetFullPath(path));
                bitmap.EndInit();
                bitmap.Freeze();
                image.Source = bitmap;
            }
            catch (IOException) { }
            catch (NotSupportedException) { }
            catch (FileFormatException) { }
            return image;
        }
    }
}
