using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace Acf.Studio
{
    internal sealed partial class WorkbenchWindow
    {
        private FrameworkElement BuildWorks()
        {
            var page = new Grid();
            var layout = new Grid();
            layout.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            layout.RowDefinitions.Add(new RowDefinition());
            layout.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star), MinWidth = 260 });
            layout.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.14, GridUnitType.Star), MinWidth = 275 });
            page.Children.Add(layout);
            var controls = new StackPanel();
            Grid.SetColumnSpan(controls, 2);
            layout.Children.Add(controls);
            var header = new DockPanel();
            var commands = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
            var hint = Ui.Text("点击封面切换预览 · 靠边浏览", 12, true);
            hint.VerticalAlignment = VerticalAlignment.Center;
            hint.Margin = new Thickness(0, 0, 14, 0);
            commands.Children.Add(hint);
            var bin = new Button { Content = "回收站", MinWidth = 109, Margin = new Thickness(0, 0, 8, 0) };
            AutomationProperties.SetName(bin, "回收站");
            commands.Children.Add(bin);
            commands.Children.Add(Ui.Button("新建作品", () => OpenTopicBrowser(), true, "\uE710"));
            DockPanel.SetDock(commands, Dock.Right);
            header.Children.Add(commands);
            var pageTitle = new StackPanel();
            pageTitle.Children.Add(Ui.Title("作品库"));
            pageTitle.Children.Add(Ui.Text("在书架翻阅图文、文案、试剪与成片。", 13, true));
            header.Children.Add(pageTitle);
            var banner = Ui.Card(header, "HoneyBrush");
            banner.Margin = new Thickness(0, 0, 0, 10);
            controls.Children.Add(banner);

            var tabs = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 0, 10) };
            var notes = new Button { Content = "图文书架", Margin = new Thickness(0, 0, 7, 0) };
            var videos = new Button { Content = "视频书架" };
            tabs.Children.Add(notes);
            tabs.Children.Add(videos);
            controls.Children.Add(tabs);
            var shelfPanel = new StackPanel();
            var shelfHeading = new DockPanel { Margin = new Thickness(0, 0, 0, 7) };
            var count = Ui.Text("", 12, true);
            count.VerticalAlignment = VerticalAlignment.Center;
            DockPanel.SetDock(count, Dock.Right);
            shelfHeading.Children.Add(count);
            var shelfTitle = EditorialStrong("图文书架", 16);
            shelfHeading.Children.Add(shelfTitle);
            shelfPanel.Children.Add(shelfHeading);
            var toolbar = new WrapPanel { Margin = new Thickness(0, 0, 0, 6) };
            var search = Ui.Search("搜索作品或选题", 178);
            var platformButtons = new Dictionary<string, Button>();
            foreach (string key in new[] { "", "xiaohongshu", "douyin", "bilibili" })
            {
                var option = new Button { Content = key.Length == 0 ? "全部平台" : Ui.Platform(key), Margin = new Thickness(0, 0, 5, 5),
                    Style = (Style)FindResource("FilterButton") };
                platformButtons.Add(key, option);
                toolbar.Children.Add(option);
            }
            toolbar.Children.Add(search);
            shelfPanel.Children.Add(toolbar);

            var shelf = new WorkShelf(page, bin);
            shelfPanel.Children.Add(shelf);
            var shelfCard = Ui.Card(shelfPanel);
            shelfCard.Margin = new Thickness(0, 0, 13, 0);
            shelfCard.VerticalAlignment = VerticalAlignment.Top;
            Grid.SetRow(shelfCard, 1);
            layout.Children.Add(shelfCard);
            page.SizeChanged += delegate
            {
                shelf.Height = Math.Round(Math.Max(175, Math.Min(258, page.ActualHeight - 250)));
            };
            var reader = new Grid();
            reader.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            reader.RowDefinitions.Add(new RowDefinition());
            var rail = new DockPanel { Margin = new Thickness(0, 0, 0, 10) };
            var undo = new Button { Content = "撤销移入回收站", VerticalAlignment = VerticalAlignment.Center,
                Visibility = Visibility.Collapsed, Margin = new Thickness(12, 0, 0, 0) };
            DockPanel.SetDock(undo, Dock.Right);
            rail.Children.Add(undo);
            var selection = new StackPanel();
            var meta = Ui.Text("", 12, true);
            var title = Ui.Text("作品预览", 19);
            title.FontWeight = FontWeights.SemiBold;
            title.TextWrapping = TextWrapping.Wrap;
            selection.Children.Add(meta);
            selection.Children.Add(title);
            rail.Children.Add(selection);
            reader.Children.Add(rail);
            var preview = new ContentControl { HorizontalContentAlignment = HorizontalAlignment.Stretch,
                VerticalContentAlignment = VerticalAlignment.Stretch };
            AutomationProperties.SetName(preview, "当前作品预览");
            Grid.SetRow(preview, 1);
            reader.Children.Add(preview);
            var readerCard = Ui.Card(reader);
            readerCard.Padding = new Thickness(15, 12, 15, 14);
            Grid.SetRow(readerCard, 1);
            Grid.SetColumn(readerCard, 1);
            layout.Children.Add(readerCard);
            ShelfWork displayed = null;
            Func<ShelfWork, bool, Task<bool>> change = null;
            Action<ShelfWork> showPreview = work =>
            {
                if (work == displayed) return;
                displayed = work;
                meta.Text = work == null ? "" : work.Label + " · " + work.Day + " · " + J.Text(work.Data, "state");
                title.Text = work == null ? "作品预览" : work.Title;
                title.ToolTip = work == null ? null : work.Title;
                preview.Content = work == null ? Ui.Text("选择一本封面后查看作品。", 13, true) :
                    BuildAlbumWork(work, async () => await change(work, false));
            };
            shelf.CurrentChanged += work =>
            {
                if (!shelf.IsBrowsing) showPreview(work);
            };
            shelf.BrowseEnded += () => showPreview(shelf.Current);

            var saved = new List<ShelfWork>();
            var recycled = new List<ShelfWork>();
            var remembered = new string[2];
            ShelfWork lastRemoved = null;
            bool videoShelf = false, busy = false, loaded = false;
            string platform = "";
            Action<int> showBinState = state =>
            {
                if (state == 2) bin.Style = (Style)FindResource("PrimaryButton"); else bin.ClearValue(StyleProperty);
                bin.Content = state == 2 ? "松手移入" : state == 1 ? "拖到这里" : recycled.Count == 0 ? "回收站" : "回收站 · " + recycled.Count;
                bin.Background = state == 1 ? Ui.Color("SelectedBrush") :
                    state == 2 ? Ui.Color("AccentBrush") : Ui.Color("SurfaceBrush");
            };
            shelf.DropStateChanged += showBinState;
            Action<string> filter = keep =>
            {
                videos.Style = (Style)FindResource(videoShelf ? "AccentSoftButton" : "QuietButton");
                notes.Style = (Style)FindResource(videoShelf ? "QuietButton" : "AccentSoftButton");
                shelfTitle.Text = videoShelf ? "视频书架" : "图文书架";
                foreach (var option in platformButtons)
                {
                    option.Value.Visibility = option.Key == "bilibili" && !videoShelf ? Visibility.Collapsed : Visibility.Visible;
                    option.Value.Background = option.Key == platform ? Ui.Color("SelectedBrush") : Ui.Color("SurfaceBrush");
                }
                if (!loaded) return;
                string query = search.Text.Trim();
                var visible = saved.Where(work => work.VideoShelf == videoShelf && (platform.Length == 0 || work.Platform == platform)
                    && (work.Title.IndexOf(query, StringComparison.CurrentCultureIgnoreCase) >= 0 ||
                        work.Topic.IndexOf(query, StringComparison.CurrentCultureIgnoreCase) >= 0)).ToArray();
                count.Text = visible.Length + " 件";
                shelf.ShowWorks(visible, keep, query.Length > 0 ? "没有匹配的作品。" : "这层书架还没有作品。");
            };
            Action<IDictionary<string, object>, string> render = (data, keep) =>
            {
                saved = J.Rows(J.Get(data, "albums")).SelectMany(album =>
                    J.Rows(J.Get(album, "works")).Select(work => new ShelfWork(J.Text(album, "title"), work)))
                    .OrderByDescending(work => work.Day, StringComparer.Ordinal).ToList();
                recycled = J.Rows(J.Get(data, "recycled")).Select(work => new ShelfWork(J.Text(work, "topic"), work)).ToList();
                loaded = true;
                showBinState(0);
                filter(keep);
            };
            Func<string, object, Action<IDictionary<string, object>>, Task<bool>> update = async (stage, payload, apply) =>
            {
                if (busy) return false;
                busy = true;
                shelf.IsEnabled = false;
                undo.IsEnabled = false;
                bin.IsEnabled = false;
                try
                {
                    apply(J.Map(await Worker.RunAsync(stage, payload: payload)));
                    undo.Visibility = lastRemoved != null ? Visibility.Visible : Visibility.Collapsed;
                    return true;
                }
                catch (Exception error)
                {
                    if (page.IsLoaded) Notify("操作未完成。" + error.Message);
                    return false;
                }
                finally
                {
                    shelf.IsEnabled = true;
                    undo.IsEnabled = true;
                    bin.IsEnabled = true;
                    busy = false;
                }
            };
            change = (work, restore) =>
                update("workspace_work_recycle", J.Obj("id", work.Id, "restore", restore), result =>
                {
                    if (!restore) lastRemoved = work;
                    else if (lastRemoved != null && lastRemoved.Id == work.Id) lastRemoved = null;
                    var keep = shelf.Current;
                    render(result, restore ? work.Id : keep != null && keep.Id != work.Id ? keep.Id : null);
                    if (page.IsLoaded) Notify(restore ? "已恢复作品。" : "已将《" + work.Title + "》移入回收站。");
                });
            Func<string[], Task<bool>> clear = ids =>
                update("workspace_work_empty", J.Obj("ids", ids), result =>
                {
                    if (lastRemoved != null && ids.Contains(lastRemoved.Id)) lastRemoved = null;
                    render(result, shelf.Current == null ? null : shelf.Current.Id);
                    if (page.IsLoaded) Notify("已清空选定的回收站记录。原文件仍保存在本机。");
                });
            shelf.RecycleRequested += async work => { await change(work, false); };
            undo.Click += async delegate { if (lastRemoved != null) await change(lastRemoved, true); };
            bin.Click += delegate { OpenWorksRecycle(() => recycled, change, clear); };

            Action<bool> changeFormat = video =>
            {
                if (video == videoShelf) return;
                if (shelf.Current != null) remembered[videoShelf ? 1 : 0] = shelf.Current.Id;
                videoShelf = video;
                if (!video && platform == "bilibili") platform = "";
                filter(remembered[video ? 1 : 0]);
            };
            notes.Click += delegate { changeFormat(false); };
            videos.Click += delegate { changeFormat(true); };
            foreach (var option in platformButtons)
            {
                var key = option.Key;
                option.Value.Click += delegate { platform = key; filter(shelf.Current == null ? null : shelf.Current.Id); };
            }
            search.TextChanged += delegate { filter(shelf.Current == null ? null : shelf.Current.Id); };
            filter(null);
            page.Loaded += async delegate
            {
                if (loaded || busy) return;
                busy = true;
                try { render(J.Map(await Worker.RunAsync("workspace_works")), null); }
                catch (Exception error)
                {
                    shelf.ShowWorks(new ShelfWork[0], null, "暂时无法读取作品，请重新打开作品库。");
                    if (page.IsLoaded) Notify(error.Message);
                }
                finally { busy = false; }
            };
            return page;
        }

        private void OpenWorksRecycle(Func<List<ShelfWork>> read, Func<ShelfWork, bool, Task<bool>> change,
            Func<string[], Task<bool>> clear)
        {
            var dialog = new Window { Owner = this, Title = "作品库回收站", Width = 530, Height = 390,
                WindowStartupLocation = WindowStartupLocation.CenterOwner, Background = Ui.Color("PaperBrush") };
            var dock = new DockPanel { Margin = new Thickness(18) };
            var explanation = Ui.Text("这是作品库的回收站。清空后记录无法恢复，视频、图片和文案原文件会留在本机。", 13, true);
            explanation.Margin = new Thickness(0, 0, 0, 10);
            DockPanel.SetDock(explanation, Dock.Top);
            dock.Children.Add(explanation);
            var footer = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 10, 0, 0) };
            var list = new ListBox();
            AutomationProperties.SetName(list, "已移入回收站的作品");
            var restore = new Button { Content = "恢复所选作品", IsEnabled = false };
            var empty = new Button { Content = "清空回收站", Margin = new Thickness(8, 0, 0, 0) };
            var message = Ui.Text("", 13, true);
            message.Margin = new Thickness(9, 7, 0, 0);
            footer.Children.Add(restore);
            footer.Children.Add(empty);
            footer.Children.Add(message);
            DockPanel.SetDock(footer, Dock.Bottom);
            dock.Children.Add(footer);
            dock.Children.Add(list);
            Action render = () =>
            {
                list.Items.Clear();
                foreach (var work in read())
                    list.Items.Add(new ListBoxItem { Content = Ui.Text(work.Title + "   ·   " + work.Label), Tag = work });
                empty.IsEnabled = list.Items.Count > 0;
                message.Text = list.Items.Count == 0 ? "回收站为空。" : "";
            };
            list.SelectionChanged += delegate { restore.IsEnabled = list.SelectedItem != null; };
            restore.Click += async delegate
            {
                var item = list.SelectedItem as ListBoxItem;
                if (item == null) return;
                restore.IsEnabled = false;
                empty.IsEnabled = false;
                list.IsEnabled = false;
                if (await change((ShelfWork)item.Tag, true)) render();
                else message.Text = "暂时无法恢复，可重试。";
                list.IsEnabled = true;
                restore.IsEnabled = list.SelectedItem != null;
                empty.IsEnabled = list.Items.Count > 0;
            };
            empty.Click += async delegate
            {
                var ids = read().Select(work => work.Id).ToArray();
                if (ids.Length == 0 || MessageBox.Show(dialog,
                    "清空这 " + ids.Length + " 件作品的回收站记录？清空后不能在工作台恢复，原文件会保留。",
                    "清空作品库回收站", MessageBoxButton.YesNo, MessageBoxImage.Question, MessageBoxResult.No) != MessageBoxResult.Yes)
                    return;
                list.IsEnabled = restore.IsEnabled = empty.IsEnabled = false;
                if (await clear(ids)) render();
                else message.Text = "请重新打开作品库后重试。";
                list.IsEnabled = true;
                restore.IsEnabled = list.SelectedItem != null;
                empty.IsEnabled = list.Items.Count > 0;
            };
            render();
            dialog.Content = dock;
            dialog.ShowDialog();
        }

        private FrameworkElement BuildAlbumWork(ShelfWork work, Action moveToRecycle)
        {
            var panel = new StackPanel();
            var package = J.Map(J.Get(work.Data, "package"));
            var draft = J.Map(J.Get(work.Data, "draft"));
            var pictures = J.Rows(J.Get(work.Data, "pictures")).ToArray();
            string file = J.Text(package, "video");
            var visual = new DockPanel { Margin = new Thickness(0, 0, 0, 9), ClipToBounds = true };
            if (pictures.Length > 0 || file.Length > 0) panel.Children.Add(visual);
            var cover = new Border { Background = Ui.Color("RailBrush"), CornerRadius = new CornerRadius(7), ClipToBounds = true, Height = 178 };
            var art = new Grid();
            var image = new Image { Stretch = Stretch.Uniform };
            art.Children.Add(image);
            var fallback = new Border { Background = WorkShelf.IllustratedCover(work), CornerRadius = new CornerRadius(7) };
            var missing = Ui.Text(work.Kind == "text" || work.Kind == "copy" ? "此作品只有文案，尚无配图" : "尚无可预览画面", 16);
            missing.Foreground = Ui.Color("InkBrush");
            missing.HorizontalAlignment = HorizontalAlignment.Center;
            missing.VerticalAlignment = VerticalAlignment.Center;
            fallback.Child = missing;
            art.Children.Add(fallback);
            cover.Child = art;
            int index = 0;
            var counter = Ui.Text("", 12, true);
            Action render = () =>
            {
                string picture = pictures.Length > 0 ? J.Text(pictures[index], "file") : J.Text(work.Data, "preview");
                image.Source = ReadWorkImage(picture, 680);
                fallback.Visibility = image.Source == null ? Visibility.Visible : Visibility.Collapsed;
                counter.Text = pictures.Length > 0 ? (index + 1) + " / " + pictures.Length : "";
            };
            if (pictures.Length > 1)
            {
                var pagination = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 7, 0, 0) };
                pagination.Children.Add(Ui.Button("上一页", () => { index = (index + pictures.Length - 1) % pictures.Length; render(); }, glyph: "\uE76B"));
                counter.Margin = new Thickness(8, 0, 8, 0);
                counter.VerticalAlignment = VerticalAlignment.Center;
                pagination.Children.Add(counter);
                pagination.Children.Add(Ui.Button("下一页", () => { index = (index + 1) % pictures.Length; render(); }, glyph: "\uE76C"));
                DockPanel.SetDock(pagination, Dock.Bottom);
                visual.Children.Add(pagination);
            }
            visual.Children.Add(cover);
            render();

            var reading = new StackPanel();
            var scroll = Ui.Scroll(panel);
            AutomationProperties.SetName(scroll, "作品文案和操作");
            panel.Children.Add(reading);
            var actions = new WrapPanel { Margin = new Thickness(0, 0, 0, 5) };
            Action<Button> addAction = button =>
            {
                button.Margin = new Thickness(0, 0, 6, 6);
                actions.Children.Add(button);
            };
            if (!String.IsNullOrEmpty(file))
                addAction(Ui.Button("播放此版本", () => PlayMedia(file, work.Title + " · " + work.Label + " · " + work.Day), true, "\uE768"));
            if (Object.Equals(J.Get(work.Data, "editable"), true))
            {
                addAction(Ui.Button("继续编辑", () => OpenProduction(work.Platform, work.Day)));
                if (work.Kind == "video")
                {
                    var post = Ui.Button(IsPersonalCreator(work.Platform) ? "投稿" : "打开发布中心",
                        () => OpenPublishing(work), glyph: "\uE724");
                    post.Style = (Style)FindResource("AccentSoftButton");
                    addAction(post);
                }
            }
            if (work.Platform == "xiaohongshu" && work.Kind == "image")
                addAction(Ui.Button("投稿", () => OpenPublishing(work), true, "\uE724"));
            string document = J.Text(work.Data, "document");
            if (File.Exists(document)) addAction(Ui.Button("打开文案文件", () => OpenLocalFile(document), glyph: "\uE8A5"));
            string body = J.Text(draft, "script");
            string copy = J.Text(draft, "caption");
            if (String.IsNullOrWhiteSpace(copy)) copy = J.Text(package, "caption");
            if (!String.IsNullOrWhiteSpace(copy)) addAction(Ui.Button("复制配文", () =>
            {
                try { Clipboard.SetText(copy); Notify("已复制配文。"); }
                catch (Exception error) { Notify(error.Message); }
            }, glyph: "\uE8C8"));
            var recycle = Ui.Button("移入回收站", moveToRecycle, glyph: "\uE74D");
            recycle.Style = (Style)FindResource("QuietButton");
            recycle.Foreground = Ui.Color("BlushInkBrush");
            addAction(recycle);
            if (actions.Children.Count > 0) reading.Children.Add(actions);
            var generation = J.Map(J.Get(draft, "generation"));
            if (J.Text(generation, "writing_model").Length > 0)
                reading.Children.Add(Ui.Text("本机写稿 · " + J.Text(generation, "writing_model")
                    + "  ·  配图方式 · " + J.Text(generation, "image_mode"), 12, true));
            if (!String.IsNullOrWhiteSpace(body) && !String.IsNullOrWhiteSpace(copy) && body != copy)
            {
                var writing = new TabControl();
                writing.Items.Add(new TabItem { Header = "正文", Content = new Border
                    { Child = Ui.Text(body, 14), Padding = new Thickness(10), Background = Ui.Color("SurfaceBrush") } });
                writing.Items.Add(new TabItem { Header = "发布配文", Content = new Border
                    { Child = Ui.Text(copy, 14), Padding = new Thickness(10), Background = Ui.Color("SurfaceBrush") } });
                reading.Children.Add(writing);
            }
            else if (!String.IsNullOrWhiteSpace(body)) AddWorkText(reading, "正文", body);
            else if (!String.IsNullOrWhiteSpace(copy)) AddWorkText(reading, "发布配文", copy);
            var reference = J.Map(J.Get(draft, "reference"));
            if (IsEditorialWebLink(J.Text(reference, "url")))
            {
                reading.Children.Add(EditorialLink("参考案例 · " + J.Text(reference, "title"), J.Text(reference, "url")));
                reading.Children.Add(Ui.Text(J.Text(draft, "borrowed"), 13, true));
                reading.Children.Add(Ui.Text(J.Text(reference, "evidence"), 12, true));
            }
            foreach (var picture in pictures)
            {
                var photo = J.Map(J.Get(picture, "photo"));
                if (IsEditorialWebLink(J.Text(photo, "page_url")))
                    reading.Children.Add(EditorialLink("照片出处 · " + J.Text(photo, "author")
                        + " · " + J.Text(photo, "license"), J.Text(photo, "page_url")));
            }
            if (String.IsNullOrWhiteSpace(body) && String.IsNullOrWhiteSpace(copy))
            {
                if (!String.IsNullOrWhiteSpace(work.Topic) && work.Topic != work.Title)
                    AddWorkText(reading, "选题", work.Topic);
                else
                    reading.Children.Add(Ui.Text("这件作品没有保存文案。", 13, true));
            }
            return scroll;
        }

        private static void AddWorkText(Panel reading, string heading, string content)
        {
            var label = Ui.Text(heading, 12, true);
            label.Margin = new Thickness(0, 3, 0, 4);
            reading.Children.Add(label);
            var body = Ui.Text(content, 14);
            body.LineHeight = 23;
            body.Margin = new Thickness(0, 0, 0, 8);
            reading.Children.Add(body);
        }

        internal static BitmapSource ReadWorkImage(string path, int width)
        {
            if (!File.Exists(path)) return null;
            try
            {
                using (var stream = File.OpenRead(path))
                {
                    var bitmap = new BitmapImage();
                    bitmap.BeginInit();
                    bitmap.CacheOption = BitmapCacheOption.OnLoad;
                    bitmap.DecodePixelWidth = width;
                    bitmap.StreamSource = stream;
                    bitmap.EndInit();
                    bitmap.Freeze();
                    return bitmap;
                }
            }
            catch (IOException) { return null; }
            catch (UnauthorizedAccessException) { return null; }
            catch (NotSupportedException) { return null; }
            catch (ArgumentException) { return null; }
            catch (FormatException) { return null; }
            catch (InvalidOperationException) { return null; }
        }

        internal static string WorkFormat(string key)
        {
            switch (key)
            {
                case "video": return "视频";
                case "preview": return "试剪";
                case "image": return "图文";
                case "text": return "纯文字";
                case "copy": return "文案";
                default: return "草稿";
            }
        }

        private void PlayMedia(string file, string title)
        {
            if (!File.Exists(file)) { Notify("找不到本机视频文件。"); return; }
            var dialog = new Window { Owner = this, Title = title, Width = 780, Height = 565,
                WindowStartupLocation = WindowStartupLocation.CenterOwner, Background = Ui.Color("PaperBrush") };
            var view = new DockPanel { Margin = new Thickness(10) };
            var media = new MediaElement { Source = new Uri(file), LoadedBehavior = MediaState.Manual,
                UnloadedBehavior = MediaState.Manual, Stretch = Stretch.Uniform };
            var tools = new WrapPanel { Margin = new Thickness(0, 8, 0, 0) };
            tools.Children.Add(Ui.Button("播放", media.Play));
            tools.Children.Add(Ui.Button("暂停", media.Pause));
            tools.Children.Add(Ui.Button("从头播放", () => { media.Position = TimeSpan.Zero; media.Play(); }));
            tools.Children.Add(Ui.Button("用系统播放器打开", () => OpenLocalFile(file)));
            DockPanel.SetDock(tools, Dock.Bottom);
            view.Children.Add(tools);
            view.Children.Add(media);
            media.MediaFailed += delegate { Notify("本机播放器无法解码这段视频，可用系统播放器打开。"); };
            dialog.Content = view;
            dialog.Loaded += delegate { media.Play(); };
            dialog.Closed += delegate { media.Close(); };
            dialog.Show();
        }

        private void OpenLocalFile(string file)
        {
            if (!File.Exists(file)) { Notify("找不到本机文件。"); return; }
            try { Process.Start(new ProcessStartInfo(file) { UseShellExecute = true }); }
            catch (Exception error) { Notify(error.Message); }
        }
    }
}
