using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Interop;
using System.Windows.Media;

namespace Acf.Studio
{
    internal sealed partial class WorkbenchWindow
    {
        private DateTime publishingDate = DateTime.Today;
        private string publishingPlatform;
        private ShelfWork pendingPostingWork;
        private readonly Dictionary<string, string> postingProfiles = new Dictionary<string, string>();
        private bool postingBusy;

        private static bool IsPersonalCreator(string platform)
        {
            return platform == "douyin" || platform == "xiaohongshu";
        }

        private ComboBox PersonalAccountPicker(string platform, IDictionary<string, object> profiles, string preferred)
        {
            var accounts = new ComboBox { Width = 250, HorizontalAlignment = HorizontalAlignment.Left };
            System.Windows.Automation.AutomationProperties.SetName(accounts, Ui.Platform(platform) + "投稿账号");
            foreach (var profile in profiles)
                accounts.Items.Add(new ComboBoxItem { Content = Convert.ToString(profile.Value), Tag = profile.Key });
            string last;
            if (!postingProfiles.TryGetValue(platform, out last)) last = preferred;
            accounts.SelectedItem = accounts.Items.OfType<ComboBoxItem>()
                .FirstOrDefault(item => Convert.ToString(item.Tag) == last);
            if (accounts.SelectedIndex < 0 && accounts.Items.Count == 1) accounts.SelectedIndex = 0;
            accounts.SelectionChanged += delegate
            {
                var picked = accounts.SelectedItem as ComboBoxItem;
                if (picked != null) postingProfiles[platform] = Convert.ToString(picked.Tag);
            };
            return accounts;
        }

        private void OpenPublishing(ShelfWork work)
        {
            DateTime parsed;
            if (DateTime.TryParseExact(work.Day, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed))
                publishingDate = parsed;
            publishingPlatform = work.Platform;
            pendingPostingWork = work;
            Navigate("publishing");
        }

        private FrameworkElement BuildPublishing()
        {
            var handoff = pendingPostingWork;
            pendingPostingWork = null;
            bool launchOnce = handoff != null && IsPersonalCreator(handoff.Platform);
            string focusDay = DateKey(publishingDate), focusPlatform = publishingPlatform ?? "";
            string handoffNote = handoff != null && handoff.Kind == "image" ? handoff.Id : "";
            string handoffVideo = handoff == null ? "" : J.Text(J.Map(J.Get(handoff.Data, "package")), "video");
            var panel = new StackPanel();
            var header = new DockPanel { Margin = new Thickness(0, 0, 0, 12) };
            var reload = Ui.Button("刷新", delegate { }, glyph: "\uE72C");
            reload.VerticalAlignment = VerticalAlignment.Center;
            DockPanel.SetDock(reload, Dock.Right);
            header.Children.Add(reload);
            var heading = new StackPanel();
            heading.Children.Add(Ui.Title("投稿"));
            heading.Children.Add(Ui.Text("每条成片一个按钮。点一下，自动打开平台窗口、上传视频、填好标题和文案；最后在平台窗口里点一次「发布」。", 13, true));
            header.Children.Add(heading);
            panel.Children.Add(header);
            var filters = new WrapPanel { Margin = new Thickness(0, 0, 0, 10) };
            panel.Children.Add(filters);
            var list = new StackPanel();
            list.Children.Add(Ui.Card(Ui.Text("正在读取成片…", 14, true)));
            panel.Children.Add(list);

            string filter = "";
            int version = 0;
            IDictionary<string, object> result = null;
            Func<Task> refresh = null;
            Action draw = null;
            Func<IDictionary<string, object>, bool> focused = row => handoff != null
                ? J.Text(row, "platform") == handoff.Platform && (handoffNote.Length > 0 ? J.Text(row, "note_id") == handoffNote
                    : String.Equals(J.Text(row, "video"), handoffVideo, StringComparison.OrdinalIgnoreCase))
                : J.Text(row, "platform") == focusPlatform && J.Text(row, "day") == focusDay;
            draw = () =>
            {
                list.Children.Clear();
                filters.Children.Clear();
                var rows = J.Rows(J.Get(result, "rows")).OrderByDescending(focused).ToArray();
                if (rows.Length == 0)
                {
                    list.Children.Add(PublishingEmptyState());
                    return;
                }
                foreach (string key in new[] { "" }.Concat(PlatformKeys))
                {
                    string choice = key;
                    int count = rows.Count(row => choice.Length == 0 || J.Text(row, "platform") == choice);
                    if (choice.Length > 0 && count == 0) continue;
                    var option = Ui.Filter((choice.Length == 0 ? "全部" : Ui.Platform(choice)) + " " + count, () => { filter = choice; draw(); });
                    option.Background = Ui.Color(choice == filter ? "SelectedBrush" : "SurfaceBrush");
                    option.Margin = new Thickness(0, 0, 6, 4);
                    filters.Children.Add(option);
                }
                bool launch = launchOnce;
                launchOnce = false;
                foreach (var row in rows.Where(row => filter.Length == 0 || J.Text(row, "platform") == filter))
                {
                    list.Children.Add(PublishingCard(row, result, refresh, launch && focused(row)));
                    if (focused(row)) launch = false;
                }
            };
            refresh = async () =>
            {
                int request = ++version;
                try
                {
                    var data = J.Map(await Worker.RunAsync("publish_list", payload: J.Obj("note_id", handoffNote, "all", true),
                        day: handoff != null ? handoff.Day : focusDay));
                    if (request != version || !panel.IsLoaded) return;
                    result = data;
                    draw();
                }
                catch (Exception error)
                {
                    if (request != version) return;
                    list.Children.Clear();
                    list.Children.Add(Ui.Card(Ui.Text(error.Message)));
                }
            };
            reload.Click += async delegate { await refresh(); };
            panel.Loaded += async delegate { await refresh(); };
            return Ui.Scroll(panel);
        }

        private FrameworkElement PublishingCard(IDictionary<string, object> row, IDictionary<string, object> result,
            Func<Task> refresh, bool autoLaunch)
        {
            string platform = J.Text(row, "platform"), video = J.Text(row, "video"), noteId = J.Text(row, "note_id");
            string title = J.Text(row, "title"), day = J.Text(row, "day"), state = J.Text(row, "state");
            string name = Ui.Platform(platform);
            bool personal = IsPersonalCreator(platform);
            bool held = Object.Equals(J.Get(row, "held"), true), canFill = Object.Equals(J.Get(row, "can_fill"), true);
            var accounts = PersonalAccountPicker(platform, J.Map(J.Get(J.Map(J.Get(result, "personal_profiles")), platform)),
                J.Text(J.Map(J.Get(result, "preferred_profiles")), platform));
            if (accounts.SelectedIndex < 0 && accounts.Items.Count > 0) accounts.SelectedIndex = 0;
            bool needsAccount = personal && accounts.Items.Count == 0;

            Func<Task> prepare = async () =>
            {
                var account = accounts.SelectedItem as ComboBoxItem;
                if ((personal && account == null) || postingBusy) return;
                postingBusy = true;
                Notify("正在打开" + name + "并上传《" + title + "》…");
                try
                {
                    var payload = noteId.Length > 0 ? J.Obj("id", noteId) : J.Obj("video", video);
                    if (account != null)
                    {
                        payload["profile_id"] = Convert.ToString(account.Tag);
                        payload["owner"] = new WindowInteropHelper(this).Handle.ToInt64();
                    }
                    var reply = J.Map(await Worker.RunAsync(noteId.Length > 0 ? "publish_personal_note" : "publish_prepare",
                        platform, payload, day));
                    string code = J.Text(reply, "status");
                    Notify(FillMessage(code));
                    if (code == "ready" && account != null && noteId.Length == 0)
                        await RecordTeamPrepared(platform, J.Text(reply, "account"), title);
                }
                catch (Exception error) { Notify(error.Message); }
                finally { postingBusy = false; }
            };

            var box = new StackPanel();
            var top = new DockPanel();
            var buttons = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(14, 0, 0, 0) };
            if (video.Length > 0)
            {
                var play = Ui.Filter("播放", () => PlayMedia(video, title));
                play.Margin = new Thickness(0, 0, 8, 0);
                buttons.Children.Add(play);
            }
            if (canFill && personal && accounts.Items.Count > 1)
            {
                accounts.Width = 150;
                accounts.Margin = new Thickness(0, 0, 8, 0);
                accounts.VerticalAlignment = VerticalAlignment.Center;
                buttons.Children.Add(accounts);
            }
            Button main;
            if (canFill && needsAccount)
                main = Ui.Button("先登录" + name + "账号", () => Navigate("accounts"), true);
            else if (canFill)
                main = Ui.Button("投到" + name, async () => await prepare(), true, "\uE724");
            else if (held)
                main = Ui.Button("恢复投稿", async () =>
                {
                    try
                    {
                        await Worker.RunAsync("workspace_quality_decide", platform,
                            J.Obj("decision", "approved", "reason", "人工确认可发布"), day);
                        await refresh();
                    }
                    catch (Exception error) { Notify(error.Message); }
                });
            else if (state == "草稿已更新")
                main = Ui.Button("去重新导出", () => OpenProduction(platform, day));
            else
            {
                main = Ui.Button(state, delegate { });
                main.IsEnabled = false;
            }
            main.MinWidth = 128;
            buttons.Children.Add(main);
            DockPanel.SetDock(buttons, Dock.Right);
            top.Children.Add(buttons);

            var copy = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
            var meta = new WrapPanel { Margin = new Thickness(0, 0, 0, 4) };
            var badge = Ui.Badge(name, platform == "douyin" ? "Sky" : platform == "bilibili" ? "Sage" : "Blush");
            badge.Margin = new Thickness(0, 0, 8, 0);
            meta.Children.Add(badge);
            double seconds;
            string length = Double.TryParse(J.Text(row, "duration"), NumberStyles.Number, CultureInfo.InvariantCulture, out seconds) && seconds > 0
                ? " · " + TimeSpan.FromSeconds(seconds).ToString(@"m\:ss") : "";
            var when = Ui.Text(day + length + (noteId.Length > 0 ? " · 图文" : ""), 12, true);
            when.VerticalAlignment = VerticalAlignment.Center;
            meta.Children.Add(when);
            copy.Children.Add(meta);
            copy.Children.Add(EditorialStrong(title, 16));
            if (!canFill && held)
            {
                string why = J.Text(row, "audit_message");
                var reason = Ui.Text(why.Length > 0 ? "未通过检查：" + why : "这版已暂缓。", 12);
                reason.Foreground = Ui.Color("BlushInkBrush");
                reason.Margin = new Thickness(0, 3, 0, 0);
                copy.Children.Add(reason);
            }
            top.Children.Add(copy);
            box.Children.Add(top);

            var more = new StackPanel { Margin = new Thickness(0, 8, 0, 0) };
            var inspection = J.Map(J.Get(row, "inspection"));
            if (video.Length > 0 && inspection.Count > 0)
            {
                var gate = BuildQualityGateCard(inspection, platform, day, title, true, refresh, false);
                gate.Margin = new Thickness(0, 0, 0, 8);
                more.Children.Add(gate);
            }
            var tools = new WrapPanel();
            if (video.Length > 0)
            {
                AddPublishingAction(tools, Ui.Button("回剪辑台修改", () => OpenProduction(platform, day)));
                var review = Ui.Button("送团队审片", () => OpenTeamReview(platform, day));
                review.IsEnabled = Object.Equals(J.Get(row, "can_review"), true);
                AddPublishingAction(tools, review);
                if (!held && Object.Equals(J.Get(row, "can_hold"), true))
                    AddPublishingAction(tools, Ui.Button("暂缓这版", async () =>
                    {
                        try
                        {
                            await Worker.RunAsync("hold", platform, J.Obj("video", video, "hold", true), day);
                            await refresh();
                        }
                        catch (Exception error) { Notify(error.Message); }
                    }));
            }
            string creatorUrl = J.Text(row, "creator_url"), extensionDir = J.Text(row, "extension_dir");
            if (creatorUrl.Length > 0)
                AddPublishingAction(tools, Ui.Button("打开" + name + "后台 ↗", () => OpenPublicUrl(creatorUrl)));
            if (!personal && extensionDir.Length > 0 && System.IO.Directory.Exists(extensionDir))
                AddPublishingAction(tools, Ui.Button("B 站投稿插件目录", () => OpenSettingsFolder(extensionDir)));
            more.Children.Add(tools);
            string caption = J.Text(row, "caption");
            if (!String.IsNullOrWhiteSpace(caption))
                more.Children.Add(new TextBox { Text = caption, IsReadOnly = true, AcceptsReturn = true, Margin = new Thickness(0, 4, 0, 0),
                    TextWrapping = TextWrapping.Wrap, MaxHeight = 160, VerticalScrollBarVisibility = ScrollBarVisibility.Auto });
            box.Children.Add(new Expander { Header = Ui.Text("详情与更多操作", 12, true), Content = more,
                Margin = new Thickness(0, 6, 0, 0) });

            var card = Ui.Card(box);
            card.Padding = new Thickness(16, 12, 16, 10);
            card.Margin = new Thickness(0, 0, 0, 10);
            if (autoLaunch && canFill && !needsAccount)
            {
                RoutedEventHandler open = null;
                open = async delegate
                {
                    card.Loaded -= open;
                    await prepare();
                };
                card.Loaded += open;
            }
            return card;
        }

        private static void AddPublishingAction(Panel panel, Button button)
        {
            button.Margin = new Thickness(0, 0, 6, 6);
            panel.Children.Add(button);
        }

        private FrameworkElement BuildAccounts()
        {
            var panel = new StackPanel();
            var header = new StackPanel();
            header.Children.Add(Ui.Title("矩阵账号"));
            var intro = Ui.Text("在一张清单查看账号和负责人。抖音和小红书个人号可在工作台登录。投稿时选择对应的账号备注。", 13, true);
            intro.Margin = new Thickness(0, 2, 0, 0);
            header.Children.Add(intro);
            var headerCard = Ui.Card(header, "SkyBrush");
            headerCard.Margin = new Thickness(0, 0, 0, 14);
            panel.Children.Add(headerCard);
            var body = new StackPanel();
            var card = Ui.Card(body);
            card.Margin = new Thickness(0, 0, 0, 12);
            panel.Children.Add(card);
            var top = new WrapPanel { Margin = new Thickness(0, 0, 0, 8) };
            body.Children.Add(top);
            var notice = Ui.Text("正在读取账号…", 13, true);
            body.Children.Add(notice);
            var listing = new ContentControl();
            var filter = new ComboBox { Width = 156, Margin = new Thickness(0, 0, 6, 6) };
            body.Children.Add(listing);
            ListView list = null;
            var catalogue = new IDictionary<string, object>[0];
            var accounts = new IDictionary<string, object>[0];
            bool settingFilter = false;
            bool teamConnected = false;
            int version = 0, filterVersion = 0;
            Func<IDictionary<string, object>> selected = () => J.Map(list == null ? null : list.SelectedItem);
            Func<string, Task> refresh = null;
            var actions = new WrapPanel { Margin = new Thickness(0, 5, 0, 0) };
            body.Children.Add(actions);
            var connect = Ui.Button("登录或核对账号", async () =>
            {
                var row = selected();
                string profileId = J.Text(row, "profile_id");
                string platform = J.Text(row, "platform");
                if (profileId.Length == 0 || postingBusy) return;
                postingBusy = true;
                try
                {
                    Notify("请在" + Ui.Platform(platform) + "创作者窗口核对头像和昵称，需要时扫码。");
                    var result = await Worker.RunAsync("publish_personal_login", platform, J.Obj(
                        "profile_id", profileId, "owner", new WindowInteropHelper(this).Handle.ToInt64()));
                    Notify(Object.Equals(result, true) ? "账号窗口已关闭。投稿时请选择对应账号备注。" : "账号窗口未完成，请检查提示后重试。");
                }
                catch (Exception error) { Notify(error.Message); }
                finally { postingBusy = false; }
            }, true);
            var rename = Ui.Button("修改备注", async () =>
            {
                var row = selected();
                await EditMatrixAccount(catalogue, row, "", refresh);
            });
            var register = Ui.Button("登记到矩阵", async () =>
            {
                var row = selected();
                await RegisterMatrixAccount(J.Text(row, "platform"), J.Text(row, "label"), refresh);
            });
            var publish = Ui.Button("去发布中心", () =>
            {
                var row = selected();
                string profileId = J.Text(row, "profile_id");
                string platform = J.Text(row, "platform");
                if (profileId.Length > 0) postingProfiles[platform] = profileId;
                if (platform.Length > 0) publishingPlatform = platform;
                Navigate("publishing");
            });
            var add = Ui.Button("添加账号", async () =>
            {
                string preferred = filter.SelectedIndex > 0 ? J.Text(catalogue[filter.SelectedIndex - 1], "platform") : publishingPlatform;
                await EditMatrixAccount(catalogue, null, preferred, refresh);
            }, true);
            Action setActions = () =>
            {
                var row = selected();
                connect.IsEnabled = J.Text(row, "profile_id").Length > 0;
                connect.Visibility = connect.IsEnabled ? Visibility.Visible : Visibility.Collapsed;
                if (connect.IsEnabled) connect.Content = "登录或核对" + Ui.Platform(J.Text(row, "platform")) + "账号";
                rename.IsEnabled = connect.IsEnabled || Object.Equals(J.Get(row, "local_registration"), true);
                publish.IsEnabled = Object.Equals(J.Get(row, "can_publish"), true);
                register.IsEnabled = teamConnected && Object.Equals(J.Get(row, "can_share"), true);
                add.IsEnabled = catalogue.Length > 0;
            };
            Action<string> showRows = keep =>
            {
                var saved = accounts.FirstOrDefault(row => J.Text(row, "id") == keep);
                if (saved != null && filter.SelectedIndex > 0 &&
                    J.Text(catalogue[filter.SelectedIndex - 1], "platform") != J.Text(saved, "platform"))
                {
                    settingFilter = true;
                    filter.SelectedIndex = Array.FindIndex(catalogue, entry => J.Text(entry, "platform") == J.Text(saved, "platform")) + 1;
                    settingFilter = false;
                }
                string platform = filter.SelectedIndex > 0 ? J.Text(catalogue[filter.SelectedIndex - 1], "platform") : "";
                var rows = accounts.Where(row => platform.Length == 0 || J.Text(row, "platform") == platform).ToArray();
                list = MatrixList(rows, keep);
                list.GroupStyle.Add(MatrixGroupStyle());
                CollectionViewSource.GetDefaultView(list.ItemsSource).GroupDescriptions.Add(new PropertyGroupDescription("[category]"));
                list.SelectionChanged += delegate { setActions(); };
                if (list.SelectedIndex < 0 && list.Items.Count > 0) list.SelectedIndex = 0;
                listing.Content = rows.Length > 0 ? (FrameworkElement)list : Ui.Text("所选平台还没有账号。可以点「添加账号」登记。", 13, true);
                setActions();
            };
            filter.SelectionChanged += delegate
            {
                if (settingFilter || catalogue.Length == 0) return;
                filterVersion++;
                showRows(null);
            };
            Action<IDictionary<string, object>, string> render = (data, keep) =>
            {
                string status = J.Text(data, "team_status");
                teamConnected = status == "connected";
                notice.Text = status == "connected" ? "已同步矩阵账号。账号登记只同步平台、备注和负责人。" :
                    status == "loading" ? "正在同步矩阵账号…" :
                    status == "unconfigured" ? "连接团队后，可以在此查看同事登记的账号。" :
                    "暂时无法同步矩阵账号。仍可使用本机账号。";
                var localError = J.Text(data, "local_error");
                if (localError.Length > 0) notice.Text += " " + localError;
                int currentFilter = filter.SelectedIndex;
                catalogue = J.Rows(J.Get(data, "platforms")).ToArray();
                accounts = J.Rows(J.Get(data, "accounts")).ToArray();
                var counts = accounts.GroupBy(row => J.Text(row, "platform")).ToDictionary(group => group.Key, group => group.Count());
                settingFilter = true;
                filter.ItemsSource = new[] { "全部平台 · " + catalogue.Length + " 种" }.Concat(catalogue.Select(entry =>
                {
                    int count;
                    counts.TryGetValue(J.Text(entry, "platform"), out count);
                    return J.Text(entry, "platform_name") + " · " + count;
                })).ToArray();
                filter.SelectedIndex = Math.Max(0, Math.Min(currentFilter, catalogue.Length));
                settingFilter = false;
                showRows(keep);
            };
            refresh = async keep =>
            {
                int request = ++version;
                int requestedFilter = filterVersion;
                try
                {
                    if (list == null || !String.IsNullOrEmpty(keep))
                    {
                        var local = J.Map(await Worker.RunAsync("workspace_account_matrix", payload: J.Obj("include_team", false)));
                        if (request != version) return;
                        render(local, requestedFilter == filterVersion ? keep : J.Text(selected(), "id"));
                    }
                    var result = J.Map(await Worker.RunAsync("workspace_account_matrix"));
                    if (request == version) render(result, J.Text(selected(), "id"));
                }
                catch (Exception error)
                {
                    if (request == version) notice.Text = "无法刷新账号。 " + error.Message;
                }
            };
            AddPublishingAction(top, add);
            System.Windows.Automation.AutomationProperties.SetName(filter, "按平台筛选");
            top.Children.Add(filter);
            AddPublishingAction(top, Ui.Button("共享运营连接", async () => { await TeamConnection(false, true); await refresh(null); }));
            AddPublishingAction(top, Ui.Button("刷新", async () => await refresh(null)));
            AddPublishingAction(actions, connect);
            AddPublishingAction(actions, rename);
            AddPublishingAction(actions, register);
            AddPublishingAction(actions, publish);
            setActions();

            var options = new StackPanel();
            var settings = new WrapPanel { Margin = new Thickness(0, 8, 0, 0) };
            AddPublishingAction(settings, Ui.Button("B 站授权设置", async () => await ShowApplicationSettings("bilibili")));
            AddPublishingAction(settings, Ui.Button("查看已授权作品", ShowPlatformWorks));
            options.Children.Add(settings);
            panel.Children.Add(Ui.Card(new Expander { Header = "已有开放平台账号（可选）", Content = options }));
            panel.Loaded += async delegate { await refresh(null); };
            return Ui.Scroll(panel);
        }

        private FrameworkElement PublishingEmptyState()
        {
            var body = new StackPanel { Margin = new Thickness(10, 22, 10, 22), MaxWidth = 520,
                HorizontalAlignment = HorizontalAlignment.Left };
            body.Children.Add(new TextBlock { Text = "\uE8B2", FontFamily = new FontFamily("Segoe MDL2 Assets"),
                FontSize = 26, Foreground = Ui.Color("AccentBrush"), Margin = new Thickness(0, 0, 0, 16) });
            body.Children.Add(EditorialStrong("当前还没有正式导出的 MP4 成片", 20));
            var note = Ui.Text("「全片试剪」仅用于预览。在「② 审素材与剪辑」点击「正式导出成片（自动质检）」后，成片就会出现在这里，可一键免 API 填入抖音、小红书或 B 站发布。", 13, true);
            note.Margin = new Thickness(0, 8, 0, 20);
            body.Children.Add(note);
            var actions = new WrapPanel();
            var create = Ui.Button("去 ② 审素材与剪辑导出成片 →", () => OpenProduction(productionPlatform, null), true, "\uE768");
            create.Margin = new Thickness(0, 0, 8, 6);
            actions.Children.Add(create);
            var library = Ui.Button("查看作品库", () => Navigate("works"));
            library.Margin = new Thickness(0, 0, 0, 6);
            actions.Children.Add(library);
            body.Children.Add(actions);
            return Ui.Card(body);
        }

        private static ListView MatrixList(IEnumerable<IDictionary<string, object>> rows, string selected)
        {
            var list = new ListView { SelectionMode = SelectionMode.Single, MaxHeight = 265, MinHeight = 72,
                Margin = new Thickness(0, 8, 0, 12), BorderBrush = Ui.Color("LineBrush"), BorderThickness = new Thickness(0),
                Background = Ui.Color("SurfaceBrush"), Foreground = Ui.Color("InkBrush"),
                FocusVisualStyle = null, FontFamily = new FontFamily("Microsoft YaHei UI, Segoe UI") };
            list.SetResourceReference(Control.FontSizeProperty, "Studio.ControlFontSize");
            System.Windows.Automation.AutomationProperties.SetName(list, "矩阵账号清单");
            list.ItemContainerStyle = (Style)Application.Current.FindResource("MatrixRow");
            list.View = new GridView
            {
                ColumnHeaderContainerStyle = (Style)Application.Current.FindResource("MatrixHeader"),
                Columns =
                {
                    new GridViewColumn { Header = "平台", DisplayMemberBinding = new Binding("[_platform]"), Width = 115 },
                    new GridViewColumn { Header = "账号备注", DisplayMemberBinding = new Binding("[label]"), Width = 220 },
                    new GridViewColumn { Header = "负责人", DisplayMemberBinding = new Binding("[owner]"), Width = 130 },
                    new GridViewColumn { Header = "使用方式", DisplayMemberBinding = new Binding("[access]"), Width = 160 }
                }
            };
            var entries = rows.Select(row =>
            {
                var copy = new Dictionary<string, object>(row);
                string name = J.Text(row, "platform_name");
                copy["_platform"] = name.Length > 0 ? name : Ui.Platform(J.Text(row, "platform"));
                return copy;
            }).ToArray();
            list.ItemsSource = entries;
            list.SelectedItem = entries.FirstOrDefault(row => J.Text(row, "id") == selected);
            return list;
        }

        private static GroupStyle MatrixGroupStyle()
        {
            var label = new FrameworkElementFactory(typeof(TextBlock));
            label.SetBinding(TextBlock.TextProperty, new Binding("Name"));
            label.SetValue(TextBlock.FontWeightProperty, FontWeights.SemiBold);
            label.SetValue(TextBlock.ForegroundProperty, Ui.Color("MutedBrush"));
            label.SetValue(FrameworkElement.MarginProperty, new Thickness(8, 7, 0, 4));
            return new GroupStyle { HeaderTemplate = new DataTemplate { VisualTree = label } };
        }

        private async Task EditMatrixAccount(IDictionary<string, object>[] catalogue, IDictionary<string, object> account,
            string preferred, Func<string, Task> refresh)
        {
            if (catalogue.Length == 0) return;
            bool edit = account != null;
            string platform = edit ? J.Text(account, "platform") : preferred;
            var body = new StackPanel();
            var dialog = TeamDialog(edit ? "修改账号备注" : "添加账号", body);
            TeamLine(body, "选择平台");
            var picker = TeamChoice("账号平台", catalogue.Select(entry =>
                J.Text(entry, "category") + " · " + J.Text(entry, "platform_name")),
                Array.FindIndex(catalogue, entry => J.Text(entry, "platform") == platform));
            picker.IsEnabled = !edit;
            body.Children.Add(picker);
            TeamLine(body, "账号备注");
            var name = TeamBox("账号备注", edit ? J.Text(account, "label") : "", 60);
            body.Children.Add(name);
            var help = TeamLine(body, "");
            var save = Ui.Button("保存账号", () => dialog.DialogResult = true, true);
            save.IsDefault = true;
            body.Children.Add(save);
            Action update = () =>
            {
                var choice = picker.SelectedIndex >= 0 ? catalogue[picker.SelectedIndex] : null;
                var key = choice == null ? "" : J.Text(choice, "platform");
                name.MaxLength = key == "douyin" ? 30 : 60;
                save.IsEnabled = choice != null && !String.IsNullOrWhiteSpace(name.Text) && name.Text.Trim().Length <= name.MaxLength;
                help.Text = key.Length == 0 ? "选平台后会显示可用的账号操作。" :
                    IsPersonalCreator(key) ? "保存后可在工作台打开" + Ui.Platform(key) + "创作者窗口。" :
                    Object.Equals(J.Get(choice, "can_share"), true) ? "保存为本机账号记录，也可登记到团队矩阵。" :
                    "当前支持本机登记。登录和投稿请到该平台操作。";
            };
            picker.SelectionChanged += delegate { update(); };
            name.TextChanged += delegate { update(); };
            update();
            dialog.Loaded += delegate { if (picker.SelectedIndex < 0) picker.Focus(); else name.Focus(); };
            if (dialog.ShowDialog() != true) return;
            try
            {
                var result = J.Map(await Worker.RunAsync("workspace_matrix_account_save", payload: J.Obj(
                    "platform", J.Text(catalogue[picker.SelectedIndex], "platform"), "label", name.Text,
                    "profile_id", edit ? J.Get(account, "profile_id") : null,
                    "id", edit ? J.Get(account, "id") : null)));
                Notify(edit ? "已保存账号备注。" : "已添加账号。");
                await refresh(J.Text(result, "id"));
            }
            catch (Exception error) { Notify(error.Message); }
        }

        private async Task RegisterMatrixAccount(string platform, string label, Func<string, Task> refresh)
        {
            if (!TeamPlatforms.Contains(platform) || String.IsNullOrWhiteSpace(label)) return;
            if (MessageBox.Show(this, "登记「" + Ui.Platform(platform) + " · " + label + "」？同事会看到平台、账号备注和负责人。",
                "登记账号到矩阵", MessageBoxButton.YesNo, MessageBoxImage.Question, MessageBoxResult.No) != MessageBoxResult.Yes) return;
            try
            {
                await Worker.RunAsync("team_account_add", platform, J.Obj("label", label));
                Notify("账号已登记到矩阵。");
                await refresh(null);
            }
            catch (Exception error) { Notify(error.Message); }
        }

        private void ShowPlatformWorks()
        {
            var dialog = PublishingDialog("已授权账号作品", 660, 490);
            var root = new DockPanel { Margin = new Thickness(17) };
            var toolbar = new WrapPanel { Margin = new Thickness(0, 0, 0, 8) };
            var selector = new ComboBox { ItemsSource = new[] { "抖音", "B站" }, SelectedIndex = 0,
                Width = 110, Margin = new Thickness(0, 0, 6, 6) };
            toolbar.Children.Add(selector);
            DockPanel.SetDock(toolbar, Dock.Top);
            root.Children.Add(toolbar);
            var statusLine = Ui.Text("选择平台后读取已授权账号的作品。", 13, true);
            statusLine.Margin = new Thickness(0, 7, 0, 0);
            DockPanel.SetDock(statusLine, Dock.Bottom);
            root.Children.Add(statusLine);
            var works = new ListBox();
            root.Children.Add(works);
            var identityKey = "";
            object cursor = null;
            int version = 0;
            bool loading = false;
            Button next = null;
            Button refresh = null;
            Func<object, Task> load = async nextPage =>
            {
                if (loading) return;
                loading = true;
                int request = ++version;
                string platform = selector.SelectedIndex == 0 ? "douyin" : "bilibili";
                next.IsEnabled = refresh.IsEnabled = false;
                statusLine.Text = "正在读取平台作品…";
                try
                {
                    var data = J.Map(await Worker.RunAsync("publish_platform_works", platform,
                        J.Obj("cursor", nextPage ?? 0, "identity_key", identityKey)));
                    if (request != version) return;
                    identityKey = J.Text(data, "identity_key");
                    cursor = J.Get(data, "next");
                    works.Items.Clear();
                    foreach (var row in J.Rows(J.Get(data, "items")))
                    {
                        var caption = new StackPanel();
                        caption.Children.Add(Ui.Text(J.Text(row, "title"), 14));
                        caption.Children.Add(Ui.Text(J.Text(row, "status"), 12, true));
                        works.Items.Add(new ListBoxItem { Content = caption, Tag = J.Text(row, "url") });
                    }
                    statusLine.Text = J.Text(data, "account") + " · " + works.Items.Count + " 条";
                }
                catch (Exception error)
                {
                    if (request == version)
                    {
                        cursor = null;
                        works.Items.Clear();
                        statusLine.Text = error.Message;
                    }
                }
                finally
                {
                    loading = false;
                    next.IsEnabled = cursor != null;
                    refresh.IsEnabled = true;
                }
            };
            refresh = Ui.Button("读取作品", async () => await load(null), true);
            next = Ui.Button("下一页", async () => { if (cursor != null) await load(cursor); });
            next.IsEnabled = false;
            AddPublishingAction(toolbar, refresh);
            AddPublishingAction(toolbar, next);
            selector.SelectionChanged += delegate
            {
                version++;
                cursor = null;
                identityKey = "";
                next.IsEnabled = false;
                works.Items.Clear();
                statusLine.Text = "点击读取作品。";
            };
            works.MouseDoubleClick += delegate
            {
                var item = works.SelectedItem as ListBoxItem;
                if (item != null) OpenPublicUrl(Convert.ToString(item.Tag));
            };
            dialog.Content = root;
            dialog.ShowDialog();
        }

        private async Task ShowApplicationSettings(string platform)
        {
            try
            {
                var info = J.Map(await Worker.RunAsync("publish_account_info", platform));
                var dialog = PublishingDialog(Ui.Platform(platform) + " 开放平台授权", 535, 470);
                var panel = new StackPanel { Margin = new Thickness(18) };
                panel.Children.Add(Ui.Text(J.Text(info, "account"), 14, true));
                var client = SettingsField(panel, "应用 ID", J.Text(info, "client"));
                panel.Children.Add(Ui.Text("应用密钥", 13, true));
                var secret = new PasswordBox();
                panel.Children.Add(secret);
                var redirect = SettingsField(panel, "登记的回调地址", J.Text(info, "redirect"));
                TextBox partition = platform == "bilibili" ? SettingsField(panel, "投稿分区 ID", J.Text(info, "partition")) : null;
                var feedback = Ui.Text("", 12, true);
                var buttons = new WrapPanel { Margin = new Thickness(0, 10, 0, 0) };
                panel.Children.Add(buttons);
                panel.Children.Add(feedback);
                IDictionary<string, object> pending = null;
                AddPublishingAction(buttons, Ui.Button("保存设置", async () =>
                {
                    try
                    {
                        string sealedValue = WorkerBridge.Seal(J.Obj("client", client.Text, "secret", secret.Password,
                            "redirect", redirect.Text, "partition", partition == null ? "" : partition.Text));
                        await Worker.RunAsync("publish_application_save", platform, J.Obj("sealed", sealedValue));
                        pending = null;
                        secret.Clear();
                        feedback.Text = "已保存。";
                    }
                    catch (Exception error) { feedback.Text = error.Message; }
                }, true));
                AddPublishingAction(buttons, Ui.Button("发起授权", async () =>
                {
                    try
                    {
                        pending = J.Map(await Worker.RunAsync("publish_authorize_start", platform));
                        OpenPublicUrl(J.Text(pending, "url"));
                        feedback.Text = "在平台完成授权后，把完整回调地址填到下面。";
                    }
                    catch (Exception error) { feedback.Text = error.Message; }
                }));
                panel.Children.Add(Ui.Text("授权后的完整回调地址", 13, true));
                var callback = new PasswordBox();
                panel.Children.Add(callback);
                var finish = Ui.Button("完成授权", async () =>
                {
                    if (pending == null) { feedback.Text = "先点「发起授权」。"; return; }
                    try
                    {
                        var encrypted = WorkerBridge.Seal(J.Obj("login", pending, "callback", callback.Password));
                        var result = J.Map(await Worker.RunAsync("publish_authorize_finish", platform, J.Obj("sealed", encrypted)));
                        pending = null;
                        callback.Clear();
                        feedback.Text = "授权成功 · " + J.Text(result, "account");
                    }
                    catch (Exception error) { feedback.Text = error.Message; }
                });
                finish.Margin = new Thickness(0, 8, 0, 0);
                panel.Children.Add(finish);
                dialog.Content = Ui.Scroll(panel);
                dialog.ShowDialog();
            }
            catch (Exception error) { Notify(error.Message); }
        }

        private Window PublishingDialog(string title, double width, double height)
        {
            return new Window { Owner = this, Title = title, Width = width, Height = height,
                WindowStartupLocation = WindowStartupLocation.CenterOwner, Background = Ui.Color("PaperBrush") };
        }

        private static TextBox SettingsField(Panel panel, string label, string value)
        {
            var heading = Ui.Text(label, 13, true);
            heading.Margin = new Thickness(0, 5, 0, 0);
            panel.Children.Add(heading);
            var field = new TextBox { Text = value };
            panel.Children.Add(field);
            return field;
        }

        private static string DateKey(DateTime date)
        {
            return date.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        private static string FillMessage(string code)
        {
            switch (code)
            {
                case "ready": return "素材和文案已填入。请在工作台内核对账号和上传进度，再确认发布。";
                case "partial": return "素材已交给内置投稿面板，请在面板里核对并补齐文案。";
                case "no_form": return "内置面板未找到可用的空白上传区，请检查页面后重试。";
                case "no_connection": return "投稿助手未连接。请检查登录状态或浏览器扩展。";
                case "closed": return "投稿窗口已关闭。";
                case "needs_check": return "请先核对平台上的上传和填稿状态，再决定是否重试。";
                default: return "请查看投稿页的状态。";
            }
        }
    }
}
