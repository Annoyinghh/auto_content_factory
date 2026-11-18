using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using Microsoft.Win32;

namespace Acf.Studio
{
    internal sealed partial class WorkbenchWindow
    {
        private string reportCategory = "全部";
        private int reportPage;
        private string overviewProblem = "";
        private static BitmapSource dashboardArt;

        private FrameworkElement BuildOverview()
        {
            var panel = new StackPanel();
            var head = new DockPanel { Margin = new Thickness(0, 0, 0, 13) };
            var name = new StackPanel();
            name.Children.Add(Ui.Title("今天的报道与热点"));
            var date = DateTime.Today.ToString("yyyy 年 M 月 d 日 · dddd", CultureInfo.GetCultureInfo("zh-CN"));
            var checkedAt = EditorialBoardTime(J.Text(overviewSnapshot ?? J.Obj(), "checked"));
            name.Children.Add(Ui.Text(date + (checkedAt.Length == 0 ? "" : "  ·  最近采集检查 " + checkedAt), 13, true));
            head.Children.Add(name);
            panel.Children.Add(head);

            var today = J.Map(J.Get(overviewSnapshot ?? J.Obj(), "today"));
            var reports = J.Rows(J.Get(today, "articles")).ToArray();
            var platformCounts = J.Map(J.Get(today, "rank_counts"));
            int total = PlatformKeys.Sum(key => CountValue(platformCounts, key));
            if (overviewProblem.Length > 0)
            {
                var warning = Ui.Text(overviewProblem, 13);
                warning.Foreground = Ui.Color("BlushInkBrush");
                warning.Margin = new Thickness(0, 0, 0, 9);
                panel.Children.Add(warning);
            }
            panel.Children.Add(DashboardTotals(reports.Length, total));

            var desk = new Grid();
            desk.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.68, GridUnitType.Star) });
            desk.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star), MinWidth = 234 });
            var feed = DashboardReports(reports);
            feed.Margin = new Thickness(0, 0, 14, 0);
            feed.VerticalAlignment = VerticalAlignment.Top;
            desk.Children.Add(feed);
            var side = new StackPanel();
            var platforms = DashboardPlatforms(platformCounts);
            platforms.Margin = new Thickness(0, 0, 0, 12);
            side.Children.Add(platforms);
            side.Children.Add(DashboardAccounts(J.Rows(J.Get(overviewSnapshot ?? J.Obj(), "accounts")).ToArray()));
            Grid.SetColumn(side, 1);
            desk.Children.Add(side);
            panel.Children.Add(desk);
            return Ui.Scroll(panel);
        }

        private async Task ReadOverviewAsync(string refresh)
        {
            if (collecting) return;
            collecting = true;
            bool explicitRequest = refresh == "force";
            bool intakeRefreshed = false;
            if (explicitRequest) Notify("正在更新报道和平台热点…");
            try
            {
                var result = await Worker.RunAsync(refresh == "never" ? "workspace_snapshot" : "workspace_collection",
                    payload: J.Obj("summary_only", true, "refresh", refresh));
                overviewSnapshot = J.Map(result);
                overviewProblem = J.Items(J.Get(overviewSnapshot, "errors")).Any() ? "有报道来源暂未更新，以下为当前本机记录。" : "";
                intakeRefreshed = Object.Equals(J.Get(overviewSnapshot, "refreshed"), true);
                if (currentPage == "overview")
                {
                    RefreshOverviewSurface();
                    if (explicitRequest) Notify(overviewProblem.Length == 0 ? "今日数据已更新。" : overviewProblem);
                }
            }
            catch (Exception error)
            {
                if (currentPage == "overview")
                {
                    overviewProblem = "今日数据暂时无法读取。 " + error.Message;
                    RefreshOverviewSurface();
                    Notify(overviewProblem);
                }
            }
            finally { collecting = false; }
            if (intakeRefreshed)
            {
                try { await Worker.RunAsync("refresh_writing_library"); }
                catch (Exception error) { Trace.TraceWarning("Writing library refresh: " + error.GetType().Name); }
            }
        }

        private void RefreshOverviewSurface()
        {
            var previous = pageHost.Content as ScrollViewer;
            double offset = previous == null ? 0 : previous.VerticalOffset;
            var current = (ScrollViewer)BuildOverview();
            if (offset > 0) current.Loaded += delegate { current.ScrollToVerticalOffset(offset); };
            pageHost.Content = current;
        }

        private FrameworkElement DashboardTotals(int reports, int hotspots)
        {
            var grid = new Grid();
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(0.86, GridUnitType.Star) });
            grid.Children.Add(DashboardMetric("今日采集报道", reports, "本机首次入库 · 已去重"));
            var second = DashboardMetric("今日热点观测", hotspots, "各平台分别去重后合计");
            Grid.SetColumn(second, 1);
            grid.Children.Add(second);
            if (dashboardArt == null)
            {
                using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("Studio.DashboardIllustration.jpg"))
                    dashboardArt = BitmapFrame.Create(stream, BitmapCreateOptions.None, BitmapCacheOption.OnLoad);
                dashboardArt.Freeze();
            }
            var art = new Border
            {
                Height = 89, CornerRadius = new CornerRadius(6),
                Background = new ImageBrush(dashboardArt) { Stretch = Stretch.UniformToFill, AlignmentX = AlignmentX.Right },
                OpacityMask = new LinearGradientBrush(Colors.Transparent, Colors.Black, 0)
            };
            Grid.SetColumn(art, 2);
            grid.Children.Add(art);
            var card = Ui.Card(grid, "HoneyBrush");
            card.Margin = new Thickness(0, 0, 0, 14);
            return card;
        }

        private FrameworkElement DashboardPlatforms(IDictionary<string, object> counts)
        {
            var chart = new StackPanel();
            chart.Children.Add(DashboardHeading("平台热点"));
            int maximum = Math.Max(1, PlatformKeys.Max(key => CountValue(counts, key)));
            var palette = new[] { "SageInkBrush", "SkyInkBrush", "BlushInkBrush" };
            for (int i = 0; i < PlatformKeys.Length; i++)
            {
                string key = PlatformKeys[i];
                var value = CountValue(counts, key);
                var bar = new Grid();
                bar.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(56) });
                bar.ColumnDefinitions.Add(new ColumnDefinition());
                bar.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(49) });
                var label = Ui.Text(Ui.Platform(key), 12);
                bar.Children.Add(label);
                var track = new Grid { Height = 6, VerticalAlignment = VerticalAlignment.Center };
                track.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(value / (double)maximum, GridUnitType.Star) });
                track.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1 - value / (double)maximum, GridUnitType.Star) });
                if (value > 0)
                    track.Children.Add(new Border { Background = Ui.Color(palette[i]), CornerRadius = new CornerRadius(3) });
                Grid.SetColumn(track, 1);
                bar.Children.Add(track);
                var count = Ui.Text((overviewSnapshot == null ? "—" : value.ToString("N0")) + "  ›", 13);
                count.TextAlignment = TextAlignment.Right;
                Grid.SetColumn(count, 2);
                bar.Children.Add(count);
                var link = Ui.Button(Ui.Platform(key), () => OpenTopicBrowser(key));
                link.Content = bar;
                link.HorizontalContentAlignment = HorizontalAlignment.Stretch;
                link.Margin = new Thickness(0, 0, 0, 5);
                System.Windows.Automation.AutomationProperties.SetName(link, "查看" + Ui.Platform(key) + "热点");
                chart.Children.Add(link);
            }
            return Ui.Card(chart, "SageBrush");
        }

        private StackPanel DashboardMetric(string title, int value, string note)
        {
            var panel = new StackPanel();
            panel.Children.Add(Ui.Text(title, 13, true));
            var large = Ui.Text(overviewSnapshot == null ? "—" : value.ToString("N0"), 35);
            large.FontFamily = new FontFamily("Segoe UI");
            large.FontWeight = FontWeights.SemiBold;
            large.Margin = new Thickness(0, 2, 0, 3);
            panel.Children.Add(large);
            panel.Children.Add(Ui.Text(note, 12, true));
            return panel;
        }

        private FrameworkElement DashboardReports(IDictionary<string, object>[] reports)
        {
            var panel = new StackPanel();
            panel.Children.Add(DashboardHeading("今日入库"));
            var filters = new WrapPanel { Margin = new Thickness(0, 0, 0, 5) };
            var rows = new StackPanel();
            var pages = new WrapPanel { Margin = new Thickness(0, 7, 0, 0) };
            panel.Children.Add(filters);
            panel.Children.Add(rows);
            panel.Children.Add(pages);
            var groups = reports.GroupBy(DashboardCategory).OrderByDescending(group => group.Count()).ToArray();
            if (reportCategory != "全部" && !groups.Any(group => group.Key == reportCategory))
            {
                reportCategory = "全部";
                reportPage = 0;
            }
            const int pageSize = 6;
            Action draw = null;
            draw = () =>
            {
                foreach (Button filter in filters.Children)
                    filter.Background = Ui.Color((string)filter.Tag == reportCategory ? "SelectedBrush" : "SurfaceBrush");
                rows.Children.Clear();
                pages.Children.Clear();
                var selected = reports.Where(row => reportCategory == "全部" || DashboardCategory(row) == reportCategory).ToArray();
                if (selected.Length == 0)
                {
                    rows.Children.Add(Ui.Text(overviewSnapshot == null
                        ? overviewProblem.Length == 0 ? "本机尚无采集记录，后台会自动更新。" : "后台稍后会自动检查。"
                        : "今天还没有该分类的报道。", 13, true));
                    return;
                }
                int count = (selected.Length + pageSize - 1) / pageSize;
                reportPage = Math.Min(reportPage, count - 1);
                foreach (var row in selected.Skip(reportPage * pageSize).Take(pageSize))
                    rows.Children.Add(DashboardReportRow(row));
                if (count > 1)
                {
                    var previous = Ui.Filter("上一页", () => { reportPage--; draw(); });
                    previous.IsEnabled = reportPage > 0;
                    var next = Ui.Filter("下一页", () => { reportPage++; draw(); });
                    next.IsEnabled = reportPage + 1 < count;
                    pages.Children.Add(previous);
                    pages.Children.Add(next);
                    var position = Ui.Text("第 " + (reportPage + 1) + " / " + count + " 页 · " + selected.Length + " 篇", 12, true);
                    position.Margin = new Thickness(4, 6, 0, 0);
                    pages.Children.Add(position);
                }
            };
            Action<string, int> addFilter = (category, count) =>
            {
                var filter = Ui.Filter(category + " " + count.ToString("N0"), () =>
                {
                    reportCategory = category;
                    reportPage = 0;
                    draw();
                });
                filter.Tag = category;
                filters.Children.Add(filter);
            };
            addFilter("全部", reports.Length);
            foreach (var group in groups) addFilter(group.Key, group.Count());
            draw();
            return Ui.Card(panel);
        }

        private static string DashboardCategory(IDictionary<string, object> row)
        {
            var category = J.Text(row, "category");
            return category.Length == 0 ? "未分类" : category;
        }

        private FrameworkElement DashboardReportRow(IDictionary<string, object> row)
        {
            var line = new DockPanel();
            var actions = new WrapPanel { VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(8, 0, 0, 0) };
            var original = Ui.Filter("原文", () => OpenPublicUrl(J.Text(row, "url")));
            original.IsEnabled = IsEditorialWebLink(J.Text(row, "url"));
            var use = Ui.Filter("用来选题", () => OpenEditorial(J.Obj("kind", "article", "id", J.Text(row, "id"), "title", J.Text(row, "title"))));
            use.Foreground = Ui.Color("AccentBrush");
            actions.Children.Add(original);
            actions.Children.Add(use);
            DockPanel.SetDock(actions, Dock.Right);
            line.Children.Add(actions);
            var copy = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
            copy.Children.Add(EditorialStrong(J.Text(row, "title"), 14));
            var time = EditorialBoardTime(J.Text(row, "collected_at"));
            copy.Children.Add(Ui.Text(J.Text(row, "source") + " · " + DashboardCategory(row) +
                (time.Length == 0 ? "" : " · " + time), 12, true));
            line.Children.Add(copy);
            return new Border { Child = line, BorderBrush = Ui.Color("LineBrush"),
                BorderThickness = new Thickness(0, 1, 0, 0), Padding = new Thickness(0, 8, 0, 6) };
        }

        private FrameworkElement DashboardAccounts(IDictionary<string, object>[] accounts)
        {
            var panel = new StackPanel();
            panel.Children.Add(DashboardHeading("我的账号数据"));
            var snapshot = overviewSnapshot ?? J.Obj();
            var withMetrics = accounts.Where(account => J.Items(J.Get(account, "metrics")).Any(value => value != null)).ToArray();
            string summary = J.Text(snapshot, "accounts_error");
            if (summary.Length == 0)
            {
                if (overviewSnapshot == null) summary = "等待读取本机记录";
                else if (Equals(J.Get(snapshot, "metrics_error"), true)) summary = "今日账号指标暂时无法读取";
                else if (accounts.Length == 0) summary = "连接账号后可导入今日指标";
                else summary = accounts.Length + " 个账号 · " + (withMetrics.Length == 0 ? "今日暂无指标" : withMetrics.Length + " 个有今日指标");
            }
            panel.Children.Add(Ui.Text(summary, 13, true));
            var labels = new[] { "发布", "播放／阅读", "点赞", "评论", "净增关注" };
            if (withMetrics.Length > 0)
            {
                foreach (var account in accounts)
                {
                    var entry = new StackPanel { Margin = new Thickness(0, 8, 0, 0) };
                    entry.Children.Add(EditorialStrong(J.Text(account, "platform_name") + " · " + J.Text(account, "label"), 13));
                    var values = J.Items(J.Get(account, "metrics")).Take(labels.Length).ToArray();
                    var metrics = new WrapPanel();
                    for (int i = 0; i < labels.Length; i++)
                    {
                        var value = i < values.Length ? values[i] : null;
                        var label = Ui.Text(labels[i] + " " + (value == null ? "—" : Convert.ToInt64(value, CultureInfo.InvariantCulture).ToString("N0")), 12, true);
                        label.Margin = new Thickness(0, 3, 10, 1);
                        metrics.Children.Add(label);
                    }
                    entry.Children.Add(metrics);
                    panel.Children.Add(entry);
                }
            }
            var actions = new WrapPanel { Margin = new Thickness(0, 9, 0, 0) };
            var import = Ui.Filter("导入 CSV", async () => await DashboardCsvAsync(false));
            import.IsEnabled = accounts.Length > 0;
            actions.Children.Add(import);
            actions.Children.Add(Ui.Filter("CSV 模板", async () => await DashboardCsvAsync(true)));
            panel.Children.Add(actions);
            return Ui.Card(panel, "SkyBrush");
        }

        private async Task DashboardCsvAsync(bool template)
        {
            FileDialog dialog = template ? (FileDialog)new SaveFileDialog { FileName = "账号今日指标.csv", DefaultExt = ".csv" }
                : new OpenFileDialog { CheckFileExists = true };
            dialog.Filter = "CSV 文件 (*.csv)|*.csv";
            if (dialog.ShowDialog(this) != true) return;
            try
            {
                var result = J.Map(await Worker.RunAsync(template ? "workspace_metrics_template" : "workspace_metrics_import",
                    payload: J.Obj("path", dialog.FileName)));
                if (!template)
                    await ReadOverviewAsync("never");
                Notify(template ? "CSV 模板已保存。" : "已导入 " + J.Text(result, "imported") + " 个账号的今日指标。");
            }
            catch (Exception error) { Notify(error.Message); }
        }

        private static TextBlock DashboardHeading(string text)
        {
            var label = Ui.Text(text, 16);
            label.FontWeight = FontWeights.SemiBold;
            label.Margin = new Thickness(0, 0, 0, 8);
            return label;
        }

        private static int CountValue(IDictionary<string, object> values, string key)
        {
            int value;
            return Int32.TryParse(J.Text(values, key), out value) ? Math.Max(0, value) : 0;
        }

        private void OpenPublicUrl(string url)
        {
            Uri address;
            if (!Uri.TryCreate(url, UriKind.Absolute, out address) ||
                (address.Scheme != Uri.UriSchemeHttps && address.Scheme != Uri.UriSchemeHttp) ||
                !String.IsNullOrEmpty(address.UserInfo)) return;
            try { Process.Start(new ProcessStartInfo(address.AbsoluteUri) { UseShellExecute = true }); }
            catch (Exception error) { Notify(error.Message); }
        }
    }
}
