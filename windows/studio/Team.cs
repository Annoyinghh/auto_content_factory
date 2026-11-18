using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Threading;

namespace Acf.Studio
{
    internal sealed partial class WorkbenchWindow
    {
        private static readonly string[] TeamSections = { "发布进度", "算力队列", "团队审片" };
        private static readonly string[] TeamPlatforms = { "douyin", "bilibili", "xiaohongshu" };
        private static readonly string[] TeamPostStates = { "planned", "prepared", "submitted", "published", "failed" };
        private static readonly string[] TeamReviewStates = { "", "pending", "approved", "changes_requested" };
        private static readonly string[] TeamJobKinds = { "copy", "storyboard" };
        private static readonly Dictionary<string, string> TeamLabels = new Dictionary<string, string>
        {
            { "planned", "待准备" }, { "prepared", "已准备投稿页" }, { "submitted", "已提交平台" },
            { "published", "已确认上线" }, { "failed", "失败" }, { "pending", "待审片" },
            { "approved", "已通过" }, { "changes_requested", "需修改" }, { "comment", "留言" },
            { "copy", "发布文案" }, { "storyboard", "制作分镜" }, { "queued", "排队中" },
            { "running", "处理中" }, { "completed", "已完成" }, { "", "全部状态" }
        };

        private int teamSection = 1;
        private bool teamBusy, teamRunnerEnabled, teamRunnerBusy, teamPolling, teamOpsRead, teamReviewsRead;
        private string teamSearch = "", teamFilter = "", teamReviewId = "", teamPostId = "", teamJobId = "";
        private string teamNextCursor = "", teamSummary = "", teamKind = "copy", teamDraftId = "";
        private string teamRunnerMessage = "开启后，本机使用已安装的 Ollama 模型领取同事的任务。";
        private string teamSharePlatform, teamShareDay;
        private readonly List<string> teamCursors = new List<string> { "" };
        private readonly Dictionary<string, string> teamFeedbackDrafts = new Dictionary<string, string>();
        private IDictionary<string, object> teamOps = J.Map(null), teamReviews = J.Map(null);
        private IDictionary<string, object> teamReview = J.Map(null), teamJob = J.Map(null), teamShare = J.Map(null);
        private IDictionary<string, object> teamReviewBase = J.Map(null);
        private StackPanel teamSurface, teamBody, teamServices;
        private WrapPanel teamTabs;
        private TextBlock teamNotice, teamRunnerStatus;
        private DispatcherTimer teamTimer;

        private FrameworkElement BuildTeam()
        {
            teamSurface = new StackPanel { IsEnabled = !teamBusy };
            var header = new StackPanel();
            header.Children.Add(Ui.Title("团队空间"));
            TeamLine(header, "同步投稿进度、交接文本任务、审看片子。共享运营与团队审片各自连接。");
            teamServices = new StackPanel();
            header.Children.Add(teamServices);
            teamSurface.Children.Add(Ui.Card(header, "SageBrush"));
            var tools = new DockPanel { Margin = new Thickness(0, 14, 0, 10) };
            var actions = new WrapPanel { HorizontalAlignment = HorizontalAlignment.Right };
            DockPanel.SetDock(actions, Dock.Right);
            actions.Children.Add(TeamButton("刷新", async () => await RefreshTeam()));
            tools.Children.Add(actions);
            teamTabs = new WrapPanel();
            tools.Children.Add(teamTabs);
            teamSurface.Children.Add(tools);
            teamNotice = TeamLine(teamSurface, "");
            teamNotice.Margin = new Thickness(0, 0, 0, 10);
            teamBody = new StackPanel();
            teamSurface.Children.Add(teamBody);
            RenderTeam();
            teamSurface.Loaded += async delegate
            {
                await RefreshTeam();
                if (teamSection == 3 && teamSharePlatform != null && teamShare.Count == 0)
                    await LoadTeamShare();
            };
            if (teamTimer == null)
            {
                teamTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(15) };
                teamTimer.Tick += async delegate { await PollTeam(); };
                teamTimer.Start();
                Closed += delegate { teamTimer.Stop(); };
            }
            return Ui.Scroll(teamSurface);
        }

        private void OpenTeamReview(string platform, string day)
        {
            if (teamBusy) { Notify("团队操作进行中，请稍后重试。"); return; }
            teamSection = 3;
            teamSharePlatform = platform;
            teamShareDay = day;
            teamShare = J.Map(null);
            if (J.Text(teamReviewBase, "platform") != platform)
                teamReviewBase = J.Map(null);
            Navigate("team");
        }

        private void OpenTeamCompute(string summary, string kind = "storyboard")
        {
            if (teamBusy) { Notify("团队操作进行中，请稍后重试。"); return; }
            if (teamSummary.Length > 0 && teamSummary != summary && MessageBox.Show(this,
                "用本次选中的摘要替换尚未提交的草稿？", "替换计算草稿",
                MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes)
                return;
            if (teamSummary != summary || teamKind != kind) teamDraftId = "";
            teamSummary = summary ?? "";
            teamKind = TeamJobKinds.Contains(kind) ? kind : "storyboard";
            teamSection = 2;
            Navigate("team");
        }

        private async Task RecordTeamPrepared(string platform, string accountLabel, string title)
        {
            try
            {
                var reply = J.Map(await Worker.RunAsync("team_publication_prepared", platform,
                    J.Obj("account_label", accountLabel, "title", title)));
                if (Object.Equals(J.Get(reply, "changed"), true)) Notify("团队发布进度已同步为已准备投稿页。");
            }
            catch (Exception error)
            {
                Notify("投稿页已准备。团队进度同步失败，可在团队页更新。 " + error.Message);
            }
        }

        private static string TeamLabel(string value)
        {
            string label;
            return TeamLabels.TryGetValue(value, out label) ? label : value;
        }

        private static string TeamTime(string value)
        {
            DateTimeOffset parsed;
            return DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed)
                ? parsed.LocalDateTime.ToString("MM-dd HH:mm", CultureInfo.InvariantCulture) : value;
        }

        private static TextBlock TeamLine(Panel parent, string text)
        {
            var label = Ui.Text(text, 13, true);
            label.Margin = new Thickness(0, 4, 0, 8);
            parent.Children.Add(label);
            return label;
        }

        private static void TeamHeading(Panel parent, string text)
        {
            var label = Ui.Text(text, 17);
            label.FontWeight = FontWeights.SemiBold;
            parent.Children.Add(label);
        }

        private static Button TeamButton(string text, Action click, bool primary = false)
        {
            var button = Ui.Button(text, click, primary);
            button.Margin = new Thickness(0, 0, 6, 5);
            return button;
        }

        private static TextBox TeamBox(string label, string value = "", int max = 300, double width = 200)
        {
            var box = new TextBox { Text = value, MaxLength = max, MinWidth = width, Margin = new Thickness(0, 0, 6, 5) };
            AutomationProperties.SetName(box, label);
            return box;
        }

        private static TextBox TeamMemo(string label, string value, int limit, double height, bool readOnly = false)
        {
            var box = TeamBox(label, value, limit, 0);
            box.Height = height;
            box.TextWrapping = TextWrapping.Wrap;
            box.AcceptsReturn = true;
            box.IsReadOnly = readOnly;
            box.VerticalScrollBarVisibility = ScrollBarVisibility.Auto;
            return box;
        }

        private static ComboBox TeamChoice(string label, IEnumerable<string> values, int selected = 0)
        {
            var combo = new ComboBox { ItemsSource = values.ToArray(), SelectedIndex = selected,
                MinWidth = 110, Margin = new Thickness(0, 0, 6, 5) };
            AutomationProperties.SetName(combo, label);
            return combo;
        }

        private static Border TeamState(string state, string text = null)
        {
            string tone;
            switch (state)
            {
                case "published": case "approved": case "completed": tone = "Sage"; break;
                case "running": case "prepared": case "submitted": tone = "Sky"; break;
                case "failed": case "changes_requested": tone = "Blush"; break;
                default: tone = "Honey"; break;
            }
            return Ui.Badge(text ?? TeamLabel(state), tone);
        }

        private static string TeamMetadata(params string[] fields)
        {
            return String.Join(" · ", fields.Where(value => !String.IsNullOrWhiteSpace(value)));
        }

        private static ListBox TeamList(IEnumerable<IDictionary<string, object>> rows, string label, string selected,
            string titleKey, string stateKey, Func<IDictionary<string, object>, string> metadata)
        {
            var list = new ListBox { SelectionMode = SelectionMode.Single, MaxHeight = 360,
                Margin = new Thickness(0, 4, 0, 8) };
            AutomationProperties.SetName(list, label);
            foreach (var row in rows)
            {
                string title = J.Text(row, titleKey), state = J.Text(row, stateKey), meta = metadata(row);
                if (titleKey == "kind") title = TeamLabel(title);
                string status = Object.Equals(J.Get(row, "archived"), true) ? "已保存到本机" : TeamLabel(state);
                var body = new StackPanel { Margin = new Thickness(5, 6, 5, 6) };
                var heading = Ui.Text(title, 14);
                heading.FontWeight = FontWeights.SemiBold;
                body.Children.Add(heading);
                var line = Ui.Text(meta, 12, true);
                line.Margin = new Thickness(0, 2, 0, 5);
                body.Children.Add(line);
                body.Children.Add(TeamState(state, status));
                var entry = new ListBoxItem { Content = body, Tag = row };
                AutomationProperties.SetName(entry, TeamMetadata(title, meta, status));
                list.Items.Add(entry);
                if (J.Text(row, "id") == selected) list.SelectedItem = entry;
            }
            return list;
        }

        private static IDictionary<string, object> TeamSelection(ListBox list)
        {
            var entry = list.SelectedItem as ListBoxItem;
            return J.Map(entry == null ? null : entry.Tag);
        }

        private void AddTeamDesk(UIElement inbox, UIElement detail)
        {
            var desk = new Grid();
            desk.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(2, GridUnitType.Star) });
            desk.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(3, GridUnitType.Star) });
            var inboxCard = Ui.Card(inbox);
            inboxCard.Margin = new Thickness(0, 0, 10, 0);
            desk.Children.Add(inboxCard);
            var detailCard = Ui.Card(detail);
            Grid.SetColumn(detailCard, 1);
            desk.Children.Add(detailCard);
            teamBody.Children.Add(desk);
        }

        private void TeamStatus(string text)
        {
            if (teamNotice == null) return;
            teamNotice.Text = text;
            teamNotice.Visibility = String.IsNullOrWhiteSpace(text) ? Visibility.Collapsed : Visibility.Visible;
        }

        private async Task<bool> TeamAction(Func<Task> action, string waiting, string success = null)
        {
            if (teamBusy) return false;
            teamBusy = true;
            if (teamSurface != null) teamSurface.IsEnabled = false;
            TeamStatus(waiting);
            try
            {
                await action();
                if (success != null) { TeamStatus(success); Notify(success); }
                return true;
            }
            catch (Exception error)
            {
                TeamStatus(error.Message);
                Notify(error.Message);
                return false;
            }
            finally
            {
                teamBusy = false;
                if (teamSurface != null) teamSurface.IsEnabled = true;
            }
        }

        private void RenderTeam()
        {
            teamServices.Children.Clear();
            var connections = new WrapPanel();
            connections.Children.Add(TeamService("共享运营", "发布进度与算力队列", teamOpsRead, teamOps, false));
            connections.Children.Add(TeamService("团队审片", "共享成片与版本反馈", teamReviewsRead, teamReviews, true));
            teamServices.Children.Add(connections);
            teamTabs.Children.Clear();
            for (int i = 0; i < TeamSections.Length; i++)
            {
                int section = i + 1;
                var tab = Ui.Filter(TeamSections[i], async () =>
                {
                    if (teamSection == section) return;
                    teamSection = section;
                    RenderTeam();
                    await RefreshTeam();
                });
                if (section == teamSection)
                {
                    tab.Background = tab.BorderBrush = Ui.Color("SelectedBrush");
                    tab.FontWeight = FontWeights.SemiBold;
                    AutomationProperties.SetHelpText(tab, "当前工作区");
                }
                teamTabs.Children.Add(tab);
            }
            teamBody.Children.Clear();
            bool review = teamSection == 3;
            string address = J.Text(review ? teamReviews : teamOps, "address");
            bool read = review ? teamReviewsRead : teamOpsRead;
            TeamStatus(!read ? "等待检查当前工作区的连接。" : address.Length == 0 ?
                "填写对应服务的地址和个人连接码后，即可同步记录。" : "");
            switch (teamSection)
            {
                case 1: BuildTeamPublications(address.Length > 0); break;
                case 2: BuildTeamCompute(address.Length > 0); break;
                case 3: BuildTeamReviews(address.Length > 0); break;
            }
        }

        private FrameworkElement TeamService(string name, string scope, bool read,
            IDictionary<string, object> service, bool review)
        {
            bool connected = J.Text(service, "address").Length > 0;
            var tile = new StackPanel { MinWidth = 214 };
            var heading = new WrapPanel();
            var title = Ui.Text(name, 14);
            title.FontWeight = FontWeights.SemiBold;
            title.VerticalAlignment = VerticalAlignment.Center;
            title.Margin = new Thickness(0, 0, 9, 0);
            heading.Children.Add(title);
            heading.Children.Add(Ui.Badge(connected ? "已连接" : read ? "未连接" : "待检查", connected ? "Sage" : "Honey"));
            tile.Children.Add(heading);
            TeamLine(tile, scope);
            tile.Children.Add(Ui.Filter("设置" + name + "连接", async () => await TeamConnection(review)));
            var card = Ui.Card(tile, review ? "SkyBrush" : "SurfaceBrush");
            card.Padding = new Thickness(12, 9, 12, 5);
            card.Margin = new Thickness(0, 4, 9, 0);
            return card;
        }

        private async Task<bool> RefreshTeam()
        {
            return await TeamAction(async () =>
            {
                string detailError = "";
                if (teamSection != 3)
                {
                    teamOps = J.Map(await Worker.RunAsync("team_ops"));
                    teamOpsRead = true;
                }
                else
                {
                    teamReviews = J.Map(await Worker.RunAsync("team_reviews", null,
                        J.Obj("search", teamSearch, "status", teamFilter, "before", teamCursors.Last())));
                    teamReviewsRead = true;
                    teamNextCursor = J.Text(teamReviews, "next_cursor");
                    if (teamReviewId.Length > 0 && J.Text(teamReviews, "address").Length > 0)
                    {
                        try
                        {
                            teamReview = J.Map(await Worker.RunAsync("team_review", null, J.Obj("id", teamReviewId)));
                        }
                        catch (Exception error)
                        {
                            teamReview = J.Map(null);
                            teamReviewId = "";
                            detailError = error.Message;
                        }
                    }
                }
                RenderTeam();
                if (detailError.Length > 0) TeamStatus(detailError);
            }, "正在读取团队工作区…");
        }

        private async Task BrowseTeamReviews(string search, string filter, string[] cursors)
        {
            string oldSearch = teamSearch, oldFilter = teamFilter;
            var oldCursors = teamCursors.ToArray();
            teamSearch = search;
            teamFilter = filter;
            teamCursors.Clear();
            teamCursors.AddRange(cursors);
            if (await RefreshTeam()) return;
            teamSearch = oldSearch;
            teamFilter = oldFilter;
            teamCursors.Clear();
            teamCursors.AddRange(oldCursors);
        }

        private async Task TeamChange(string stage, object payload, string success, string platform = null)
        {
            // Show write success even if the subsequent refresh fails.
            bool written = await TeamAction(async () => { await Worker.RunAsync(stage, platform, payload); },
                "正在保存团队记录…", success);
            if (written) await RefreshTeam();
        }

        private Window TeamDialog(string title, StackPanel body, double width = 480)
        {
            body.Margin = new Thickness(20);
            return new Window
            {
                Owner = this, Title = title, Width = width, SizeToContent = SizeToContent.Height,
                ResizeMode = ResizeMode.NoResize, WindowStartupLocation = WindowStartupLocation.CenterOwner,
                ShowInTaskbar = false, Content = body, FontFamily = FontFamily, Background = Ui.Color("SurfaceBrush")
            };
        }

        private async Task TeamConnection(bool review, bool fromMatrix = false)
        {
            var body = new StackPanel();
            var dialog = TeamDialog(review ? "连接团队审片" : "连接共享运营", body);
            TeamLine(body, "服务地址");
            var address = TeamBox("服务地址", J.Text(review ? teamReviews : teamOps, "address"), 500);
            body.Children.Add(address);
            TeamLine(body, "管理员发放的个人连接码");
            var token = new PasswordBox { MaxLength = 128, Margin = new Thickness(0, 0, 0, 8) };
            AutomationProperties.SetName(token, "个人连接码");
            body.Children.Add(token);
            string sealedToken = null;
            var warning = TeamLine(body, "");
            var connect = TeamButton("验证并连接", () =>
            {
                if (String.IsNullOrWhiteSpace(address.Text) || String.IsNullOrWhiteSpace(token.Password))
                {
                    warning.Text = "请输入服务地址和个人连接码。";
                    return;
                }
                try
                {
                    sealedToken = WorkerBridge.Seal(J.Obj("token", token.Password));
                    token.Clear();
                    dialog.DialogResult = true;
                }
                catch (Exception)
                {
                    warning.Text = "Windows 无法保护本次输入的连接码。";
                }
            }, true);
            connect.IsDefault = true;
            body.Children.Add(connect);
            if (dialog.ShowDialog() != true) return;
            string desk = review ? "review" : "operations";
            bool connected = await TeamAction(async () =>
            {
                var result = J.Map(await Worker.RunAsync("team_connect", null,
                    J.Obj("desk", desk, "address", address.Text.Trim(), "sealed", sealedToken)));
                if (review)
                {
                    teamReviews = result;
                    teamReviewsRead = true;
                    teamNextCursor = "";
                    teamReview = J.Map(null);
                    teamReviewBase = J.Map(null);
                    teamReviewId = teamSearch = teamFilter = "";
                    teamFeedbackDrafts.Clear();
                    teamCursors.Clear();
                    teamCursors.Add("");
                }
                else
                {
                    teamOps = result;
                    teamOpsRead = true;
                    teamJob = J.Map(null);
                    teamJobId = teamPostId = teamDraftId = "";
                    teamRunnerEnabled = false;
                    SetTeamRunnerStatus("开启后，本机使用已安装的 Ollama 模型领取同事的任务。");
                }
                if (!fromMatrix) RenderTeam();
            }, "正在验证团队连接…", "团队连接已保存。");
            if (connected && !fromMatrix && review == (teamSection == 3)) await RefreshTeam();
        }

        private void BuildTeamPublications(bool connected)
        {
            var inbox = new StackPanel();
            TeamHeading(inbox, "发布任务");
            string member = J.Text(J.Map(J.Get(teamOps, "member")), "name");
            var rows = J.Rows(J.Get(teamOps, "publications")).ToArray();
            TeamLine(inbox, connected ? rows.Length + " 项" + (member.Length > 0 ? " · " + member : "") :
                "连接共享运营后查看任务。");
            var list = TeamList(rows, "发布任务清单", teamPostId, "title", "status", row =>
                TeamMetadata(Ui.Platform(J.Text(row, "platform")), J.Text(row, "owner")));
            if (rows.Length > 0)
            {
                if (list.SelectedIndex < 0) list.SelectedIndex = 0;
                inbox.Children.Add(list);
            }
            else if (connected) TeamLine(inbox, "团队还没有发布任务。");

            var mine = J.Rows(J.Get(teamOps, "accounts")).Where(row => J.Text(row, "owner") == member).ToArray();
            if (connected && mine.Length > 0)
            {
                var form = new StackPanel { Margin = new Thickness(10, 2, 0, 0) };
                TeamLine(form, "我的共享账号");
                var account = TeamChoice("我的共享账号", mine.Select(row =>
                    Ui.Platform(J.Text(row, "platform")) + " · " + J.Text(row, "label")));
                var title = TeamBox("任务标题", width: 0);
                form.Children.Add(account);
                TeamLine(form, "任务标题");
                form.Children.Add(title);
                var create = TeamButton("创建发布任务", async () =>
                {
                    if (account.SelectedIndex < 0 || String.IsNullOrWhiteSpace(title.Text)) return;
                    await TeamChange("team_publication_add",
                        J.Obj("account_id", J.Text(mine[account.SelectedIndex], "id"), "title", title.Text.Trim()), "已创建发布任务。");
                }, true);
                create.IsEnabled = false;
                title.TextChanged += delegate { create.IsEnabled = !String.IsNullOrWhiteSpace(title.Text); };
                form.Children.Add(create);
                inbox.Children.Add(new Expander { Header = "新建发布任务", Content = form });
            }
            else if (connected) inbox.Children.Add(TeamButton("去矩阵账号登记", () => Navigate("accounts")));

            var detail = new StackPanel();
            Action select = () =>
            {
                var row = TeamSelection(list);
                string id = J.Text(row, "id"), state = J.Text(row, "status");
                teamPostId = id;
                detail.Children.Clear();
                TeamHeading(detail, "任务详情");
                if (id.Length == 0)
                {
                    TeamLine(detail, "任务的账号、负责人和当前进度显示在这里。");
                    return;
                }
                var statusBadge = TeamState(state);
                statusBadge.Margin = new Thickness(0, 11, 0, 5);
                detail.Children.Add(statusBadge);
                TeamHeading(detail, J.Text(row, "title"));
                TeamLine(detail, TeamMetadata(Ui.Platform(J.Text(row, "platform")), J.Text(row, "account_label"),
                    J.Text(row, "owner")));
                TeamLine(detail, "准备投稿页、提交平台和确认上线分别记录。");
                if (J.Text(row, "owner") != member)
                {
                    TeamLine(detail, "由任务负责人更新进度。");
                    return;
                }
                var controls = new WrapPanel();
                var status = TeamChoice("更新进度", TeamPostStates.Select(TeamLabel), Array.IndexOf(TeamPostStates, state));
                var save = TeamButton("保存进度", async () =>
                {
                    if (status.SelectedIndex < 0) return;
                    string next = TeamPostStates[status.SelectedIndex];
                    if (next == state) return;
                    await TeamChange("team_publication_update", J.Obj("id", id,
                        "revision", J.Get(row, "revision"), "status", next), "已更新发布进度。");
                }, true);
                save.IsEnabled = false;
                status.SelectionChanged += delegate { save.IsEnabled = status.SelectedIndex >= 0 && TeamPostStates[status.SelectedIndex] != state; };
                controls.Children.Add(status);
                controls.Children.Add(save);
                detail.Children.Add(controls);
            };
            list.SelectionChanged += delegate { select(); };
            select();
            AddTeamDesk(inbox, detail);
        }

        private void BuildTeamCompute(bool connected)
        {
            var inbox = new StackPanel();
            TeamHeading(inbox, "我的计算任务");
            TeamLine(inbox, "提交选题摘要后，由团队生成文案或分镜。");
            var tools = new WrapPanel();
            tools.Children.Add(TeamButton(teamSummary.Length == 0 ? "新建计算任务" : "编辑待发送摘要", async () =>
                await ComposeTeamJob(connected), true));
            var enable = new CheckBox { Content = "允许本机接单", IsChecked = teamRunnerEnabled,
                IsEnabled = connected, Margin = new Thickness(0, 7, 0, 6) };
            enable.Checked += async delegate
            {
                teamRunnerEnabled = true;
                SetTeamRunnerStatus("接单已开启。工作台运行时会检查团队队列。");
                await PollTeam();
            };
            enable.Unchecked += delegate
            {
                teamRunnerEnabled = false;
                SetTeamRunnerStatus(teamRunnerBusy ? "接单已关闭，进行中的任务会继续完成。" : "本机接单已关闭。");
            };
            tools.Children.Add(enable);
            inbox.Children.Add(tools);
            teamRunnerStatus = TeamLine(inbox, teamRunnerMessage);
            var jobs = J.Rows(J.Get(teamOps, "jobs")).ToArray();
            var result = new ContentControl();
            if (jobs.Length == 0)
            {
                TeamLine(inbox, connected ? "你还没有计算任务。" : "连接后可以提交任务。");
                RenderTeamJob(result, J.Map(null));
            }
            else
            {
                var list = TeamList(jobs, "我的计算任务", teamJobId, "kind", "state", row =>
                    TeamMetadata(TeamTime(J.Text(row, "created_at")), J.Text(row, "runner")));
                inbox.Children.Add(list);
                list.SelectionChanged += async delegate
                {
                    var row = TeamSelection(list);
                    teamJobId = J.Text(row, "id");
                    teamJob = J.Map(null);
                    RenderTeamJob(result, row);
                    if (teamJobId.Length > 0) await ReadTeamJob(result);
                };
                RenderTeamJob(result, TeamSelection(list));
            }
            AddTeamDesk(inbox, result);
        }

        private async Task ComposeTeamJob(bool connected)
        {
            var body = new StackPanel();
            var dialog = TeamDialog("提交文本计算任务", body, 560);
            const string editHint = "编辑本次要提交的选题摘要。素材文件、账号登录资料和本机路径留在本机。";
            var hint = TeamLine(body, editHint);
            var kind = TeamChoice("任务类型", TeamJobKinds.Select(TeamLabel), Array.IndexOf(TeamJobKinds, teamKind));
            body.Children.Add(kind);
            var summary = TeamMemo("选题摘要", teamSummary, 6000, 190);
            body.Children.Add(summary);
            var buttons = new WrapPanel { HorizontalAlignment = HorizontalAlignment.Right };
            body.Children.Add(buttons);
            bool preview = false;
            Button next = null;
            var edit = TeamButton("返回编辑", () =>
            {
                preview = false;
                summary.IsReadOnly = false;
                kind.IsEnabled = true;
                hint.Text = editHint;
                next.Content = "预览将发送的摘要";
                next.IsEnabled = true;
                summary.Focus();
            });
            edit.Visibility = Visibility.Collapsed;
            var close = TeamButton("关闭", () => { dialog.DialogResult = false; });
            close.IsCancel = true;
            buttons.Children.Add(close);
            buttons.Children.Add(edit);
            next = TeamButton("预览将发送的摘要", () =>
            {
                if (String.IsNullOrWhiteSpace(summary.Text)) { hint.Text = "请输入选题摘要。"; return; }
                if (preview) { dialog.DialogResult = true; return; }
                preview = true;
                summary.Text = summary.Text.Trim();
                summary.IsReadOnly = true;
                kind.IsEnabled = false;
                edit.Visibility = Visibility.Visible;
                hint.Text = connected ? "以下文字将发送给已开启接单的团队电脑。" : "摘要已预览。连接共享运营后可以发送。";
                next.Content = "确认发送摘要";
                next.IsEnabled = connected;
            }, true);
            buttons.Children.Add(next);
            bool send = dialog.ShowDialog() == true;
            string text = summary.Text.Trim(), chosen = TeamJobKinds[kind.SelectedIndex];
            if (teamSummary != text || teamKind != chosen) teamDraftId = "";
            teamSummary = text;
            teamKind = chosen;
            if (!send) { RenderTeam(); return; }
            if (teamDraftId.Length == 0) teamDraftId = Guid.NewGuid().ToString("N");
            bool saved = await TeamAction(async () =>
            {
                await Worker.RunAsync("team_compute_submit", null,
                    J.Obj("id", teamDraftId, "kind", teamKind, "summary", teamSummary));
                teamJobId = teamDraftId;
                teamJob = J.Map(null);
                teamDraftId = teamSummary = "";
            }, "正在提交已确认的摘要…", "计算任务已提交。");
            if (saved) await RefreshTeam();
        }

        private void RenderTeamJob(ContentControl host, IDictionary<string, object> selected)
        {
            var detail = new StackPanel();
            host.Content = detail;
            TeamHeading(detail, "任务详情");
            if (selected.Count == 0) { TeamLine(detail, "选择任务查看结果。"); return; }
            var state = TeamState(J.Text(selected, "state"));
            state.Margin = new Thickness(0, 10, 0, 5);
            detail.Children.Add(state);
            TeamHeading(detail, TeamLabel(J.Text(selected, "kind")));
            TeamLine(detail, TeamMetadata(TeamTime(J.Text(selected, "created_at")), J.Text(selected, "runner")));
            string result = J.Text(teamJob, "result");
            string error = J.Text(teamJob.Count == 0 ? selected : teamJob, "error");
            if (result.Length > 0)
            {
                string archive = J.Text(teamJob, "archive_error");
                TeamLine(detail, archive.Length > 0 ? archive : "计算结果已保存到本机。");
                detail.Children.Add(TeamMemo("计算结果", result, 0, 150, true));
                var resultActions = new WrapPanel();
                resultActions.Children.Add(TeamButton("复制结果", () =>
                {
                    try { Clipboard.SetText(result); Notify("结果已复制。"); }
                    catch (ExternalException) { Notify("剪贴板暂时忙碌，请重试。"); }
                }));
                resultActions.Children.Add(TeamButton("用这份结果新建视频分镜 →", () =>
                    OpenQuickScriptDialog(TeamLabel(J.Text(selected, "kind")), result), true));
                detail.Children.Add(resultActions);
            }
            else if (error.Length > 0) TeamLine(detail, error);
            detail.Children.Add(TeamButton("读取选中任务", async () => await ReadTeamJob(host)));
        }

        private async Task ReadTeamJob(ContentControl host)
        {
            string id = teamJobId;
            if (id.Length == 0) return;
            await TeamAction(async () =>
            {
                teamJob = J.Map(await Worker.RunAsync("team_compute_job", null, J.Obj("id", id)));
                RenderTeamJob(host, teamJob);
                TeamStatus("已读取计算任务。");
            }, "正在读取计算任务…");
        }

        private void SetTeamRunnerStatus(string message)
        {
            teamRunnerMessage = message;
            if (teamRunnerStatus != null) teamRunnerStatus.Text = message;
        }

        private async Task PollTeam()
        {
            if (teamRunnerEnabled && !teamRunnerBusy)
            {
                teamRunnerBusy = true;
                try
                {
                    SetTeamRunnerStatus("正在检查队列并处理可领取的任务。");
                    await Worker.RunAsync("team_compute_once", null, J.Obj("enabled", true));
                    SetTeamRunnerStatus(teamRunnerEnabled ? "接单已开启。等待符合本机模型的任务。" : "本机接单已关闭。");
                }
                catch (Exception)
                {
                    SetTeamRunnerStatus(teamRunnerEnabled ? "本机暂时无法接单，稍后重试。" : "本机接单已关闭。");
                }
                finally { teamRunnerBusy = false; }
            }
            if (teamBusy || teamPolling) return;
            var jobs = J.Rows(J.Get(teamOps, "jobs")).ToArray();
            if (!jobs.Any(row => new[] { "queued", "running" }.Contains(J.Text(row, "state")) ||
                (J.Text(row, "state") == "completed" && !Object.Equals(J.Get(row, "archived"), true)))) return;
            teamPolling = true;
            var before = teamOps;
            try
            {
                var snapshot = J.Map(await Worker.RunAsync("team_ops"));
                var completed = J.Rows(J.Get(snapshot, "jobs")).FirstOrDefault(row =>
                    J.Text(row, "state") == "completed" && !Object.Equals(J.Get(row, "archived"), true));
                IDictionary<string, object> result = null;
                if (completed != null && !teamBusy && Object.ReferenceEquals(before, teamOps))
                {
                    result = J.Map(await Worker.RunAsync("team_compute_job", null, J.Obj("id", J.Text(completed, "id"))));
                    completed["archived"] = Object.Equals(J.Get(result, "archived"), true);
                }
                if (teamBusy || !Object.ReferenceEquals(before, teamOps)) return;
                teamOps = snapshot;
                if (result != null && J.Text(result, "id") == teamJobId) teamJob = result;
                if (teamSection == 2 && teamSurface.IsVisible) RenderTeam();
            }
            catch (Exception) { }
            finally { teamPolling = false; }
        }

        private async Task LoadTeamShare()
        {
            teamShare = J.Map(null);
            await TeamAction(async () =>
            {
                try
                {
                    teamShare = J.Map(await Worker.RunAsync("team_share_preview", teamSharePlatform, null, teamShareDay));
                }
                finally { RenderTeam(); }
            }, "正在确认本地成片版本…");
        }

        private void BuildTeamReviews(bool connected)
        {
            if (teamShare.Count > 0)
            {
                var share = new StackPanel();
                TeamHeading(share, "送审确认 · " + J.Text(teamShare, "title"));
                string baseId = J.Text(teamReviewBase, "id");
                double mb;
                Double.TryParse(J.Text(teamShare, "bytes"), NumberStyles.Number, CultureInfo.InvariantCulture, out mb);
                TeamLine(share, Ui.Platform(teamSharePlatform) + " · MP4 " + (mb / 1048576).ToString("0.0") +
                    " MB · 将上传这份成片及以下配文。");
                if (baseId.Length > 0) TeamLine(share, "关联上一版 · " + J.Text(teamReviewBase, "title"));
                share.Children.Add(TeamMemo("随成片发送的配文", J.Text(teamShare, "caption"), 0, 64, true));
                var actions = new WrapPanel();
                var send = TeamButton("确认上传送审", async () =>
                {
                    bool saved = await TeamAction(async () =>
                    {
                        var result = J.Map(await Worker.RunAsync("team_share", teamSharePlatform,
                            J.Obj("version", J.Text(teamShare, "version"), "previous_id", baseId.Length > 0 ? baseId : null),
                            teamShareDay));
                        teamReviewId = J.Text(result, "id");
                        teamReview = J.Map(null);
                        teamShare = teamReviewBase = J.Map(null);
                        teamSharePlatform = teamShareDay = null;
                        teamSearch = teamFilter = "";
                        teamCursors.Clear();
                        teamCursors.Add("");
                        RenderTeam();
                    }, "正在上传成片。中断后可重试同一版本…", "成片已进入团队审片清单。");
                    if (saved) await RefreshTeam();
                }, true);
                send.IsEnabled = connected;
                actions.Children.Add(send);
                actions.Children.Add(TeamButton("重新读取本地成片", async () => await LoadTeamShare()));
                if (baseId.Length > 0) actions.Children.Add(TeamButton("取消关联", () =>
                {
                    teamReviewBase = J.Map(null);
                    RenderTeam();
                }));
                actions.Children.Add(TeamButton("取消送审", ClearTeamShare));
                share.Children.Add(actions);
                var shell = Ui.Card(share);
                shell.Margin = new Thickness(0, 0, 0, 10);
                teamBody.Children.Add(shell);
                return;
            }
            else if (teamSharePlatform != null)
            {
                var actions = new WrapPanel();
                actions.Children.Add(TeamButton("重新读取本地成片", async () => await LoadTeamShare()));
                actions.Children.Add(TeamButton("取消送审", ClearTeamShare));
                teamBody.Children.Add(actions);
            }
            var inbox = new StackPanel();
            TeamHeading(inbox, "审片清单");
            TeamLine(inbox, "按标题或提交人搜索。");
            var filters = new WrapPanel { IsEnabled = connected };
            var search = TeamBox("标题或提交人", teamSearch, 120, 118);
            var status = TeamChoice("审片状态", TeamReviewStates.Select(TeamLabel), Array.IndexOf(TeamReviewStates, teamFilter));
            Func<Task> apply = () => BrowseTeamReviews(search.Text.Trim(), TeamReviewStates[status.SelectedIndex],
                new[] { "" });
            search.KeyDown += async (sender, args) => { if (args.Key == Key.Enter) await apply(); };
            filters.Children.Add(search);
            filters.Children.Add(status);
            filters.Children.Add(TeamButton("搜索", async () => await apply()));
            inbox.Children.Add(filters);
            var rows = J.Rows(J.Get(teamReviews, "items")).ToArray();
            var detail = new ContentControl();
            if (rows.Length == 0) TeamLine(inbox, connected ? "没有符合条件的成片。" : "连接后查看团队共享成片。");
            else
            {
                var list = TeamList(rows, "成片审片清单", teamReviewId, "title", "status", row =>
                    TeamMetadata(Ui.Platform(J.Text(row, "platform")), J.Text(row, "author"), TeamTime(J.Text(row, "created_at"))));
                inbox.Children.Add(list);
                list.SelectionChanged += async delegate
                {
                    string id = J.Text(TeamSelection(list), "id");
                    if (id.Length > 0) await LoadTeamReview(id);
                };
            }
            var paging = new WrapPanel();
            var previous = TeamButton("上一页", async () =>
            {
                await BrowseTeamReviews(teamSearch, teamFilter, teamCursors.Take(teamCursors.Count - 1).ToArray());
            });
            previous.IsEnabled = teamCursors.Count > 1;
            paging.Children.Add(previous);
            var next = TeamButton("下一页", async () =>
                await BrowseTeamReviews(teamSearch, teamFilter, teamCursors.Concat(new[] { teamNextCursor }).ToArray()));
            next.IsEnabled = teamNextCursor.Length > 0;
            paging.Children.Add(next);
            inbox.Children.Add(paging);
            AddTeamDesk(inbox, detail);
            RenderTeamReview(detail);
        }

        private void ClearTeamShare()
        {
            teamShare = teamReviewBase = J.Map(null);
            teamSharePlatform = teamShareDay = null;
            RenderTeam();
        }

        private async Task LoadTeamReview(string id)
        {
            if (id.Length == 0) return;
            await TeamAction(async () =>
            {
                teamReview = J.Map(await Worker.RunAsync("team_review", null, J.Obj("id", id)));
                teamReviewId = id;
                RenderTeam();
            }, "正在读取审片记录…");
        }

        private void RenderTeamReview(ContentControl host)
        {
            var detail = new StackPanel();
            host.Content = detail;
            TeamHeading(detail, "成片审看");
            var item = teamReview;
            if (item.Count == 0) { TeamLine(detail, "从审片清单选择一版成片。"); return; }
            string id = J.Text(item, "id");
            var state = TeamState(J.Text(item, "status"));
            state.Margin = new Thickness(0, 10, 0, 5);
            detail.Children.Add(state);
            TeamHeading(detail, J.Text(item, "title"));
            TeamLine(detail, TeamMetadata(Ui.Platform(J.Text(item, "platform")), J.Text(item, "author"),
                TeamTime(J.Text(item, "created_at"))));
            var tools = new WrapPanel();
            tools.Children.Add(TeamButton("播放这版成片", async () => await TeamAction(async () =>
            {
                string path = (string)await Worker.RunAsync("team_review_video", null, J.Obj("id", id));
                Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
                TeamStatus("已在系统播放器打开成片。");
            }, "正在下载这版成片…"), true));
            tools.Children.Add(TeamButton("送审新版", () =>
            {
                teamReviewBase = item;
                Notify("在作品库选择同平台的新成片，再送团队审片。");
                Navigate("works");
            }));
            detail.Children.Add(tools);
            var versions = J.Rows(J.Get(item, "versions")).ToArray();
            if (versions.Length > 0)
            {
                var versionButtons = new WrapPanel();
                foreach (var version in versions)
                {
                    string label = J.Text(version, "relation") == "previous" ? "上一版" : "后续版本";
                    string versionId = J.Text(version, "id");
                    versionButtons.Children.Add(TeamButton(label + " · " + TeamTime(J.Text(version, "created_at")),
                        async () => await LoadTeamReview(versionId)));
                }
                detail.Children.Add(versionButtons);
            }
            var text = J.Text(item, "caption");
            var feedback = J.Rows(J.Get(item, "feedback")).Select(row =>
                J.Text(row, "author") + " · " + TeamLabel(J.Text(row, "decision")) + " · " +
                TeamTime(J.Text(row, "created_at")) + "\n" + J.Text(row, "text")).ToArray();
            detail.Children.Add(new Expander
            {
                Header = "发布配文与反馈 · " + feedback.Length + " 条",
                Margin = new Thickness(0, 5, 0, 5),
                Content = TeamMemo("发布配文与反馈记录",
                    (text.Length == 0 ? "这版未附配文。" : text) + "\n\n" +
                    (feedback.Length > 0 ? String.Join("\n\n", feedback) : "还没有反馈。"), 0, 135, true)
            });
            string draft;
            teamFeedbackDrafts.TryGetValue(id, out draft);
            var comment = TeamMemo("审片意见", draft ?? "", 2000, 65);
            comment.TextChanged += delegate { teamFeedbackDrafts[id] = comment.Text; };
            TeamLine(detail, "审片意见");
            detail.Children.Add(comment);
            var decisions = new WrapPanel();
            foreach (string option in new[] { "comment", "changes_requested", "approved" })
            {
                string decision = option;
                string label = decision == "changes_requested" ? "退回修改" : decision == "approved" ? "通过" : "留言";
                decisions.Children.Add(TeamButton(label, async () =>
                {
                    bool saved = await TeamAction(async () =>
                    {
                        teamReview = J.Map(await Worker.RunAsync("team_feedback", null, J.Obj("id", id,
                            "revision", J.Get(item, "revision"), "decision", decision, "text", comment.Text)));
                        teamFeedbackDrafts.Remove(id);
                        RenderTeam();
                    }, "正在保存审片反馈…", "审片反馈已保存。");
                    if (saved) await RefreshTeam();
                }, decision == "approved"));
            }
            detail.Children.Add(decisions);
        }
    }
}
