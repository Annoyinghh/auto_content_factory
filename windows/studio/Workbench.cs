using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Threading;

namespace Acf.Studio
{
    internal static class Program
    {
        [STAThread]
        internal static int Main(string[] args)
        {
            bool settingsPreview = args.Length > 0 && args[0] == "--settings-preview";
            if (settingsPreview)
                Environment.SetEnvironmentVariable("LOCALAPPDATA", Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "settings-preview-profile"));
            var worker = new WorkerBridge();
            bool check = args.Length > 0 && args[0] == "--check";
            bool probe = args.Length == 2 && args[0] == "--probe";
            try
            {
                if (args.Length > 0 && new[] { "--daily", "--intake", "--worker" }.Contains(args[0]))
                    return worker.RunCommand(args);

                var application = new Application();
                StudioTypography.Apply(100);
                foreach (string resource in new[] { "Studio.Theme.xaml", "Studio.Brand.xaml" })
                    using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(resource))
                        application.Resources.MergedDictionaries.Add((ResourceDictionary)XamlReader.Load(stream));
                var window = new WorkbenchWindow(worker, check || probe || settingsPreview);
                if (settingsPreview || (args.Length > 0 && args[0] == "--settings")) window.OpenSettings();
                if (check || probe)
                {
                    window.ContentRendered += async delegate
                    {
                        try
                        {
                            if (probe)
                                File.WriteAllText(args[1], "WPF window rendered");
                            else
                                await window.VerifyAsync();
                            application.Shutdown(0);
                        }
                        catch (Exception error)
                        {
                            Directory.CreateDirectory(worker.DataRoot);
                            File.WriteAllText(Path.Combine(worker.DataRoot, "desktop-check-error.txt"), error.ToString());
                            application.Shutdown(1);
                        }
                    };
                }
                return application.Run(window);
            }
            catch (Exception error)
            {
                if (check || probe)
                {
                    Directory.CreateDirectory(worker.DataRoot);
                    File.WriteAllText(Path.Combine(worker.DataRoot, "desktop-check-error.txt"), error.ToString());
                }
                else
                    MessageBox.Show(error.Message, "无法打开热采放映室", MessageBoxButton.OK, MessageBoxImage.Error);
                return 1;
            }
        }
    }

    internal sealed partial class WorkbenchWindow : Window
    {
        private static readonly string[] PlatformKeys = { "douyin", "bilibili", "xiaohongshu" };
        private readonly WorkerBridge Worker;
        private readonly ContentControl pageHost = new ContentControl();
        private readonly Dictionary<string, Button> navigation = new Dictionary<string, Button>();
        private readonly Dictionary<string, Button> stepButtons = new Dictionary<string, Button>();
        private readonly TextBlock pageLabel;
        private readonly TextBlock flowStep;
        private readonly TextBlock status;
        private readonly bool verification;
        private string currentPage;
        private int settingsInitialTab;
        private IDictionary<string, object> overviewSnapshot;
        private bool collecting;
        private readonly DispatcherTimer collectionTimer;

        internal WorkbenchWindow(WorkerBridge worker, bool verification)
        {
            Worker = worker;
            this.verification = verification;
            Title = "热采放映室 · 内容工作台";
            Icon = (ImageSource)Application.Current.FindResource("WorkbenchMark");
            Width = Math.Min(1280, SystemParameters.WorkArea.Width - 24);
            Height = Math.Min(860, SystemParameters.WorkArea.Height - 24);
            MinWidth = 960;
            MinHeight = 620;
            WindowStartupLocation = WindowStartupLocation.CenterScreen;
            Background = Ui.Color("PaperBrush");
            FontFamily = new FontFamily("Microsoft YaHei UI, Segoe UI");
            FontSize = 14;
            UseLayoutRounding = true;
            TextOptions.SetTextFormattingMode(this, TextFormattingMode.Display);
            AutomationPropertiesForWindow();

            var layout = new Grid { Background = Ui.Color("PaperBrush") };
            layout.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(192) });
            layout.ColumnDefinitions.Add(new ColumnDefinition());
            layout.Children.Add(BuildSidebar());

            var body = new Grid();
            body.RowDefinitions.Add(new RowDefinition { Height = new GridLength(52) });
            body.RowDefinitions.Add(new RowDefinition());
            body.RowDefinitions.Add(new RowDefinition { Height = new GridLength(34) });
            Grid.SetColumn(body, 1);
            layout.Children.Add(body);
            var path = new DockPanel { Margin = new Thickness(24, 0, 24, 0) };
            var stepper = BuildTopStepper();
            DockPanel.SetDock(stepper, Dock.Right);
            path.Children.Add(stepper);
            var workspace = Ui.Text("创作空间", 13, true);
            workspace.Margin = new Thickness(0, 0, 10, 0);
            workspace.VerticalAlignment = VerticalAlignment.Center;
            path.Children.Add(workspace);
            pageLabel = Ui.Text("", 14);
            pageLabel.FontWeight = FontWeights.SemiBold;
            pageLabel.VerticalAlignment = VerticalAlignment.Center;
            path.Children.Add(pageLabel);
            flowStep = Ui.Text("", 12, true);
            flowStep.VerticalAlignment = VerticalAlignment.Center;
            flowStep.Margin = new Thickness(12, 0, 0, 0);
            path.Children.Add(flowStep);
            body.Children.Add(new Border
            {
                Child = path, BorderBrush = Ui.Color("LineBrush"), BorderThickness = new Thickness(0, 0, 0, 1)
            });
            pageHost.Margin = new Thickness(24, 16, 24, 8);
            Grid.SetRow(pageHost, 1);
            body.Children.Add(pageHost);
            status = Ui.Text("", 12, true);
            status.VerticalAlignment = VerticalAlignment.Center;
            status.TextWrapping = TextWrapping.NoWrap;
            status.TextTrimming = TextTrimming.CharacterEllipsis;
            status.Margin = new Thickness(24, 0, 24, 0);
            var statusBar = new Border
            {
                Child = status, BorderBrush = Ui.Color("LineBrush"), BorderThickness = new Thickness(0, 1, 0, 0)
            };
            Grid.SetRow(statusBar, 2);
            body.Children.Add(statusBar);
            Content = layout;
            shell = layout;
            collectionTimer = new DispatcherTimer { Interval = TimeSpan.FromMinutes(10) };
            collectionTimer.Tick += async delegate { await ReadOverviewAsync("due"); };
            InitializePreferences();
            Navigate("flow");
            ContentRendered += async delegate
            {
                if (verification) return;
                await LoadFlowAsync(false);
                await ReadOverviewAsync("never");
                if (preferences.AutoRefresh)
                {
                    collectionTimer.Start();
                    await ReadOverviewAsync("due");
                }
            };
            Closed += delegate { collectionTimer.Stop(); };
        }

        private FrameworkElement BuildTopStepper()
        {
            var bar = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
            var steps = new[]
            {
                new[] { "topics", "① 选题与文案" },
                new[] { "production", "② 审素材与剪辑" },
                new[] { "publishing", "③ 投稿" }
            };
            for (int i = 0; i < steps.Length; i++)
            {
                string key = steps[i][0];
                string label = steps[i][1];
                var btn = Ui.Filter(label, () => Navigate(key));
                btn.Margin = new Thickness(0, 0, 0, 0);
                btn.Padding = new Thickness(9, 4, 9, 4);
                stepButtons[key] = btn;
                bar.Children.Add(btn);
                if (i < steps.Length - 1)
                {
                    var arrow = Ui.Text("→", 12, true);
                    arrow.VerticalAlignment = VerticalAlignment.Center;
                    arrow.Margin = new Thickness(5, 0, 5, 0);
                    bar.Children.Add(arrow);
                }
            }
            return bar;
        }

        private void AutomationPropertiesForWindow()
        {
            System.Windows.Automation.AutomationProperties.SetName(this, "热采放映室工作台");
        }

        private FrameworkElement BuildSidebar()
        {
            var stack = new StackPanel { Margin = new Thickness(12, 16, 12, 10) };
            var brand = new DockPanel { Margin = new Thickness(4, 0, 4, 18) };
            var mark = new Image
            {
                Source = Icon, Width = 34, Height = 34, Margin = new Thickness(0, 0, 9, 0),
                SnapsToDevicePixels = true
            };
            brand.Children.Add(mark);
            var name = Ui.Text("热采放映室", 16);
            name.FontWeight = FontWeights.SemiBold;
            name.VerticalAlignment = VerticalAlignment.Center;
            brand.Children.Add(name);
            stack.Children.Add(brand);

            NavSectionLabel(stack, "三步制作主线");
            NavItem(stack, "flow", "工作台总览", "\uE8FD");
            NavItem(stack, "topics", "① 选题与文案", "\uE8BC");
            NavItem(stack, "production", "② 审素材与剪辑", "\uE945");
            NavItem(stack, "publishing", "③ 投稿", "\uE724");

            var divider = new Border { Height = 1, Margin = new Thickness(7, 12, 7, 10), Background = Ui.Color("LineBrush") };
            stack.Children.Add(divider);
            NavSectionLabel(stack, "成品与管理");
            NavItem(stack, "works", "作品书架", "\uE8F1");
            NavItem(stack, "overview", "热点看板", "\uE9D9");
            NavItem(stack, "accounts", "矩阵账号", "\uE77B");
            NavItem(stack, "team", "团队协作", "\uE716");

            var rail = new DockPanel();
            var bottom = new StackPanel { Margin = new Thickness(12, 6, 12, 14) };
            NavItem(bottom, "settings", "设置与通知", "\uE713");
            DockPanel.SetDock(bottom, Dock.Bottom);
            rail.Children.Add(bottom);
            rail.Children.Add(stack);
            return new Border
            {
                Background = Ui.Color("RailBrush"), BorderBrush = Ui.Color("LineBrush"),
                BorderThickness = new Thickness(0, 0, 1, 0), Child = rail
            };
        }

        private static void NavSectionLabel(Panel parent, string text)
        {
            var label = Ui.Text(text, 11, true);
            label.Margin = new Thickness(8, 0, 0, 5);
            parent.Children.Add(label);
        }

        private void NavItem(Panel parent, string key, string label, string glyph)
        {
            var item = Ui.Button(label, () => Navigate(key));
            var parts = new StackPanel { Orientation = Orientation.Horizontal };
            var image = new TextBlock
            {
                Text = glyph, FontFamily = new FontFamily("Segoe MDL2 Assets"),
                VerticalAlignment = VerticalAlignment.Center, HorizontalAlignment = HorizontalAlignment.Center, FontSize = 14
            };
            var swatch = key == "flow" || key == "overview" || key == "works" ? "Honey" :
                key == "topics" || key == "team" ? "Sage" :
                key == "production" || key == "accounts" ? "Sky" : "Blush";
            image.Foreground = Ui.Color(swatch + "InkBrush");
            parts.Children.Add(new Border
            {
                Child = image, Width = 25, Height = 25, CornerRadius = new CornerRadius(6),
                Background = Ui.Color(swatch + "Brush"), Margin = new Thickness(0, 0, 8, 0)
            });
            parts.Children.Add(new TextBlock { Text = label, VerticalAlignment = VerticalAlignment.Center });
            item.Content = parts;
            item.Tag = label;
            System.Windows.Automation.AutomationProperties.SetName(item, label);
            navigation.Add(key, item);
            parent.Children.Add(item);
        }

        private void Navigate(string key)
        {
            currentPage = key;
            pageLabel.Text = Convert.ToString(navigation[key].Tag);
            flowStep.Text = FlowStepHint(key);
            foreach (var item in navigation)
                item.Value.Style = (Style)FindResource(item.Key == key ? "NavActiveButton" : "NavButton");
            foreach (var step in stepButtons)
            {
                bool active = step.Key == key;
                step.Value.Background = Ui.Color(active ? "SelectedBrush" : "SurfaceBrush");
                step.Value.FontWeight = active ? FontWeights.SemiBold : FontWeights.Normal;
            }
            if (key == "flow" && !verification) { var task = LoadFlowAsync(false); }
            switch (key)
            {
                case "flow": pageHost.Content = BuildFlow(); break;
                case "settings": pageHost.Content = BuildSettings(); break;
                case "topics": pageHost.Content = BuildTopics(); break;
                case "production": pageHost.Content = BuildProduction(); break;
                case "works": pageHost.Content = BuildWorks(); break;
                case "team": pageHost.Content = BuildTeam(); break;
                case "publishing": pageHost.Content = BuildPublishing(); break;
                case "accounts": pageHost.Content = BuildAccounts(); break;
                default: pageHost.Content = BuildOverview(); break;
            }
        }

        private static string FlowStepHint(string key)
        {
            switch (key)
            {
                case "flow": return "选入口开始 → 本地 AI 分镜剪辑 → 自动质检与免 API 直投";
                case "topics": return "选热点/报道或贴现成文案，交给本地 AI";
                case "production": return "先通过素材审核，再改口播分镜、拼试剪与导出";
                case "publishing": return "每条成片一个按钮，点一下投到平台";
                case "works": return "全部图文、文案、试剪与成片归档";
                default: return "";
            }
        }

        internal void OpenSettings(int initialTab = 0)
        {
            settingsInitialTab = initialTab;
            Navigate("settings");
        }

        private void Notify(string text)
        {
            status.Text = text ?? "";
            status.ToolTip = text;
        }

        internal async Task VerifyAsync()
        {
            foreach (var key in navigation.Keys.ToArray())
            {
                Navigate(key);
                await Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
                if (pageHost.Content == null)
                    throw new InvalidOperationException("未显示页面：" + key);
            }
            Navigate("overview");
            await ReadOverviewAsync("never");
            var result = J.Map(await Worker.RunAsync("workspace_verify"));
            var ffmpeg = J.Map(J.Get(result, "ffmpeg"));
            if (overviewSnapshot == null || !Object.Equals(J.Get(ffmpeg, "ok"), true))
                throw new InvalidOperationException("无法读取今日面板或后台 FFmpeg。");
            File.WriteAllText(Path.Combine(Worker.DataRoot, "desktop-check.txt"),
                "Native WPF, " + navigation.Count + " workbench pages, local snapshot and bundled FFmpeg OK");
        }
    }
}
