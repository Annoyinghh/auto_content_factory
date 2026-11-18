using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;

namespace Acf.Studio
{
    internal sealed partial class WorkbenchWindow
    {
        private readonly Dictionary<string, string> productionFeedback = new Dictionary<string, string>();
        private readonly HashSet<string> productionBlocked = new HashSet<string>();

        private static void ProductionAvailability(Control control, bool enabled, string purpose, string reason)
        {
            control.IsEnabled = enabled;
            control.ToolTip = enabled ? purpose : reason;
            ToolTipService.SetShowOnDisabled(control, true);
            AutomationProperties.SetHelpText(control, enabled ? purpose : reason);
        }

        private void OpenProductionHistory()
        {
            var dialog = new Window { Title = "切换创作作品", Owner = this, Width = 760, Height = 620,
                MinWidth = 560, MinHeight = 420, Background = Ui.Color("PaperBrush"),
                WindowStartupLocation = WindowStartupLocation.CenterOwner, Icon = Icon };
            var body = new DockPanel { Margin = new Thickness(22) };
            var heading = new StackPanel();
            heading.Children.Add(Ui.Title("切换作品"));
            heading.Children.Add(Ui.Text("这里包含草稿和视频版本。仅有草稿的作品需先生成试剪，才能在视频书架播放。切换历史创作会保留当前工作稿。", 13, true));
            var message = Ui.Text("正在读取创作记录…", 13, true);
            message.Margin = new Thickness(0, 10, 0, 10);
            heading.Children.Add(message);
            DockPanel.SetDock(heading, Dock.Top); body.Children.Add(heading);
            var footer = new WrapPanel { Margin = new Thickness(0, 12, 0, 0) };
            var refresh = Ui.Button("刷新列表", delegate { }, glyph: "\uE72C");
            refresh.Margin = new Thickness(0, 0, 8, 0);
            footer.Children.Add(refresh);
            footer.Children.Add(Ui.Button("关闭", () => dialog.Close()));
            DockPanel.SetDock(footer, Dock.Bottom); body.Children.Add(footer);
            var content = new ContentControl(); body.Children.Add(content);
            dialog.Content = body;
            bool opening = false;
            Func<Task> load = null;
            load = async () =>
            {
                refresh.IsEnabled = false;
                try
                {
                    var result = J.Map(await Worker.RunAsync("workspace_creation_list"));
                    if (!dialog.IsVisible) return;
                    var rows = J.Rows(J.Get(result, "rows")).ToArray();
                    foreach (var row in rows)
                        if (productionJobs.ContainsKey(ProductionKey(J.Text(row, "platform"), J.Text(row, "day")))
                            || dailyCompilationBusy && J.Text(row, "day") == dailyCompilationDay)
                            row["busy"] = true;
                    var errors = String.Join(" · ", J.Items(J.Get(result, "errors")));
                    int playable = rows.Count(row => J.Text(row, "artifact_state") == "已有试剪" || J.Text(row, "artifact_state") == "已有成片");
                    message.Text = errors.Length > 0 ? errors : "共 " + rows.Length + " 份创作 · " + playable +
                        " 份有视频 · " + (rows.Length - playable) + " 份待生成或补齐视频";
                    content.Content = ProductionHistoryList(rows, async row =>
                    {
                        if (opening) return;
                        opening = true;
                        content.IsEnabled = false;
                        refresh.IsEnabled = false;
                        message.Text = "正在打开作品…";
                        try
                        {
                            string target = J.Text(row, "platform"), day = J.Text(row, "day");
                            var opened = J.Map(await Worker.RunAsync("workspace_creation_open", target, row, day));
                            if (Object.Equals(J.Get(opened, "restored"), true))
                            {
                                string key = ProductionKey(target, day);
                                productionFeedback.Remove(key);
                                productionBlocked.Remove(key);
                                Notify("");
                            }
                            if (!dialog.IsVisible) return;
                            OpenProduction(target, day);
                            dialog.Close();
                        }
                        catch (Exception error) { message.Text = error.Message + " 请刷新列表后重试。"; }
                        finally { opening = false; content.IsEnabled = true; refresh.IsEnabled = true; }
                    });
                }
                catch (Exception error) { message.Text = "创作记录读取失败：" + error.Message; }
                finally { refresh.IsEnabled = true; }
            };
            refresh.Click += async delegate { if (!opening) await load(); };
            dialog.Loaded += async delegate { await load(); };
            dialog.ShowDialog();
        }

        private static FrameworkElement ProductionHistoryList(IEnumerable<IDictionary<string, object>> records,
            Action<IDictionary<string, object>> open)
        {
            var rows = records.ToArray();
            var layout = new DockPanel();
            var search = Ui.Search("搜索作品、平台或日期", 280);
            search.Margin = new Thickness(0, 0, 0, 12);
            search.HorizontalAlignment = HorizontalAlignment.Left;
            DockPanel.SetDock(search, Dock.Top); layout.Children.Add(search);
            var items = new StackPanel();
            var scroll = Ui.Scroll(items); scroll.FocusVisualStyle = null;
            layout.Children.Add(scroll);
            Action draw = () =>
            {
                items.Children.Clear();
                string query = search.Text.Trim();
                foreach (var row in rows)
                {
                    string title = J.Text(row, "title");
                    string context = EditorialPlatformName(J.Text(row, "platform")) + " · " + J.Text(row, "day");
                    if ((title + context).IndexOf(query, StringComparison.CurrentCultureIgnoreCase) < 0) continue;
                    bool active = Object.Equals(J.Get(row, "active"), true), busy = Object.Equals(J.Get(row, "busy"), true);
                    var text = new StackPanel();
                    text.Children.Add(EditorialStrong(title, 16));
                    text.Children.Add(Ui.Text(context + " · " + J.Text(row, "state") +
                        " · " + J.Text(row, "scenes") + " 镜", 12, true));
                    string artifact = J.Text(row, "artifact_state");
                    if (artifact.Length > 0) text.Children.Add(Ui.Text(artifact, 12, true));
                    text.Children.Add(Ui.Text(busy ? active ? "后台处理中，可打开查看进度" : "同日同平台正在制作，完成后可切换"
                        : active ? "打开并继续编辑" : "恢复这份创作，当前工作稿会留在历史中", 12, true));
                    var entry = Ui.Button(title, () => open(row));
                    entry.Content = text;
                    entry.HorizontalContentAlignment = HorizontalAlignment.Stretch;
                    entry.Padding = new Thickness(14, 12, 14, 12);
                    entry.Margin = new Thickness(0, 0, 0, 8);
                    AutomationProperties.SetName(entry, "打开创作 " + title + " " + context);
                    ProductionAvailability(entry, active || !busy, "打开这份创作", "同日同平台有任务正在运行，完成后可切换；当前工作稿不会被覆盖。");
                    items.Children.Add(entry);
                }
                if (items.Children.Count == 0)
                    items.Children.Add(Ui.Text(rows.Length == 0 ? "还没有已保存的创作。可以从热点与素材新建选题。" : "没有匹配的创作，试试其他标题或日期。", 14, true));
            };
            search.TextChanged += delegate { draw(); }; draw();
            return layout;
        }
    }
}
