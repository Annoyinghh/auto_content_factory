using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace Acf.Studio
{
    internal sealed partial class WorkbenchWindow
    {
        private static readonly string[] SearchMediaKinds = { "all", "image", "video" };

        private void OpenMaterialSearch(string platform, string day, IDictionary<string, object> current,
                                        Action<IDictionary<string, object>> onSelect = null, string initialQuery = "", string sourceReportId = "")
        {
            bool original = sourceReportId.Length > 0;
            string draftId = current == null ? "" : J.Text(current, "id");
            string draftTitle = current == null ? "" : J.Text(J.Map(J.Get(current, "story")), "title");
            var dialog = new Window { Owner = this, Title = original ? "原文图片与视频" : "搜索素材", Width = 810, Height = 660, MinWidth = 620,
                MinHeight = 430, WindowStartupLocation = WindowStartupLocation.CenterOwner, Background = Ui.Color("PaperBrush") };
            var page = new DockPanel { Margin = new Thickness(22, 18, 22, 18) };
            dialog.Content = page;
            var head = new StackPanel();
            DockPanel.SetDock(head, Dock.Top);
            page.Children.Add(head);
            head.Children.Add(EditorialHeading(original ? "原文图片与视频" : "搜索素材", original ? "查看这篇原文的图片、动图和播放器视频。先预览再接入，保留出处与实际清晰度。" : draftId.Length == 0 ? "找到的素材可先保存，建好工作稿后再加入。" :
                "当前工作稿 · " + draftTitle + "  /  " + EditorialPlatformName(platform) + "  /  " + day));
            var bar = new DockPanel { Margin = new Thickness(0, 0, 0, 9) };
            var actions = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(8, 0, 0, 0) };
            DockPanel.SetDock(actions, Dock.Right);
            bar.Children.Add(actions);
            var kind = EditorialChoice(new[] { "全部类型", "图片", "视频／动图" }, 0, "素材类型");
            kind.MinWidth = 96;
            actions.Children.Add(kind);
            var search = new Button { Content = "搜索", IsDefault = true,
                Style = (Style)FindResource("PrimaryButton"), Margin = new Thickness(7, 0, 0, 0) };
            var library = new Button { Content = "已保存素材", Margin = new Thickness(7, 0, 0, 0) };
            actions.Children.Add(search);
            actions.Children.Add(library);
            var query = EditorialField(initialQuery, "素材关键词");
            query.MaxLength = 100;
            if (original) { query.IsReadOnly = true; search.Content = "刷新原文素材"; library.Visibility = Visibility.Collapsed; }
            query.ToolTip = "输入具体的物件、场景或地点，例如计算器、街景";
            bar.Children.Add(query);
            head.Children.Add(bar);
            var suggestions = EditorialBar();
            suggestions.Margin = new Thickness(0, 0, 0, 5);
            suggestions.Visibility = Visibility.Collapsed;
            head.Children.Add(suggestions);
            var status = Ui.Text("输入要找的内容，例如「计算器」或「城市夜景」。", 13, true);
            status.Margin = new Thickness(0, 2, 0, 10);
            head.Children.Add(status);
            var results = new StackPanel();
            var scroll = Ui.Scroll(results);
            page.Children.Add(scroll);

            bool saved = false, started = false;
            int request = 0;
            Func<bool, Task> load = null;
            load = async showSaved =>
            {
                string keyword = query.Text.Trim();
                if (!showSaved && keyword.Length == 0)
                {
                    status.Text = "请输入素材关键词。";
                    query.Focus();
                    return;
                }
                int token = ++request;
                saved = showSaved;
                started = true;
                search.IsEnabled = library.IsEnabled = kind.IsEnabled = false;
                string format = SearchMediaKinds[Math.Max(0, kind.SelectedIndex)];
                results.Children.Clear();
                suggestions.Children.Clear();
                suggestions.Visibility = Visibility.Collapsed;
                status.Text = showSaved ? "正在读取本机素材…" : "正在按关键词搜索公开素材…";
                try
                {
                    var reply = J.Map(await Worker.RunAsync(showSaved ? "workspace_media_library" : original ? "workspace_source_media" : "workspace_media_search",
                        payload: showSaved ? null : original ? J.Obj("report_id", sourceReportId) : J.Obj("query", keyword, "kind", format)));
                    if (request != token) return;
                    var matches = J.Rows(J.Get(reply, "items")).Where(row =>
                        format == "all" || J.Text(row, "kind") == format || format == "video" && J.Text(row, "kind") == "animation").ToArray();
                    var related = J.Items(J.Get(reply, "suggestions")).Select(Convert.ToString).ToArray();
                    if (related.Length > 0)
                    {
                        var label = Ui.Text("相关关键词", 13, true);
                        label.Margin = new Thickness(0, 7, 8, 0);
                        suggestions.Children.Add(label);
                        foreach (string word in related)
                        {
                            string next = word;
                            var chip = Ui.Button(next, async () => { query.Text = next; await load(false); });
                            chip.Margin = new Thickness(0, 0, 6, 4);
                            suggestions.Children.Add(chip);
                        }
                        suggestions.Visibility = Visibility.Visible;
                    }
                    bool sourceFailed = J.Items(J.Get(reply, "failed")).Any();
                    status.Text = showSaved
                        ? (matches.Length == 0 ? "这个类型还没有已保存的素材。" : "已保存素材 · " + matches.Length + " 项")
                        : matches.Length == 0
                            ? (sourceFailed ? "素材来源暂时不可用，可以重试或打开已保存素材。" : "未找到符合该类型和许可的素材，可以换关键词。")
                            : "「" + keyword + "」找到 " + matches.Length + " 项 · " + J.Text(reply, "provider_name") + (sourceFailed ? " · 部分类型暂不可用" : "");
                    if (original) status.Text = matches.Length == 0 ? "这篇原文没有可读取的此类媒体。可选择其他原文或添加本机素材。" : "所选原文 · " + matches.Length + " 项";
                    string translated = J.Text(reply, "search_query");
                    if (!showSaved && translated.Length > 0 && translated != keyword) status.Text += " · 检索词：" + translated;
                    foreach (var row in matches)
                        results.Children.Add(MaterialSearchRow(row, showSaved, platform, day, draftId, onSelect));
                    scroll.ScrollToTop();
                }
                catch (Exception error) { if (request == token) status.Text = error.Message; }
                finally { if (request == token) search.IsEnabled = library.IsEnabled = kind.IsEnabled = true; }
            };
            search.Click += async delegate { await load(false); };
            library.Click += async delegate { await load(true); };
            kind.SelectionChanged += async delegate { if (started) await load(saved); };
            dialog.Closed += delegate { request++; };
            dialog.Loaded += async delegate { query.Focus(); if (initialQuery.Trim().Length > 0) await load(false); };
            dialog.ShowDialog();
        }

        private FrameworkElement MaterialSearchRow(IDictionary<string, object> asset, bool saved, string platform, string day, string draftId,
                                                   Action<IDictionary<string, object>> onSelect = null)
        {
            var line = new DockPanel();
            var thumbnail = MaterialSearchThumbnail(asset);
            if (thumbnail != null)
            {
                thumbnail.Width = 104;
                thumbnail.Margin = new Thickness(0, 0, 13, 0);
                DockPanel.SetDock(thumbnail, Dock.Left);
                line.Children.Add(thumbnail);
            }
            var body = new StackPanel();
            string title = J.Text(asset, "title");
            body.Children.Add(EditorialStrong(title, 15));
            string mediaKind = J.Text(asset, "kind");
            body.Children.Add(Ui.Text((mediaKind == "animation" || J.Text(asset, "origin_format") == "gif" ? "原文动图 · 可剪辑" : mediaKind == "video" ? "视频" : "图片")
                + "  ·  " + J.Text(asset, "author") + "  ·  " + J.Text(asset, "license"), 12, true));
            if (J.Text(asset, "width").Length > 0)
                body.Children.Add(Ui.Text("原始尺寸 " + J.Text(asset, "width") + " × " + J.Text(asset, "height") + "  " + J.Text(asset, "resolution_note"), 12, true));
            var buttons = EditorialBar();
            var feedback = Ui.Text("", 12, true);
            var save = new Button { Content = "保存素材", Margin = new Thickness(0, 0, 6, 0) };
            var add = new Button { Content = "加入当前工作稿", Margin = new Thickness(0, 0, 6, 0) };
            var id = J.Get(asset, "page_id");
            bool article = J.Text(asset, "origin") == "article";
            Func<Task<IDictionary<string, object>>> saveAsset = async () => saved ? asset : J.Map(await Worker.RunAsync(
                article ? "workspace_source_media_save" : "workspace_media_save", payload: article
                ? J.Obj("report_id", J.Get(asset, "source_report_id"), "media_id", J.Get(asset, "source_media_id")) : J.Obj("page_id", id)));
            save.Click += async delegate
            {
                save.IsEnabled = false;
                feedback.Text = "正在保存到本机…";
                try
                {
                    asset = await saveAsset();
                    saved = true;
                    save.Content = "已保存";
                    feedback.Text = "";
                }
                catch (Exception error) { save.IsEnabled = true; feedback.Text = error.Message; }
            };
            add.Click += async delegate
            {
                add.IsEnabled = false;
                feedback.Text = "正在加入工作稿…";
                if (onSelect != null)
                {
                    try
                    {
                        var chosen = await saveAsset();
                        onSelect(chosen);
                        add.Content = "已接入本选题";
                        feedback.Text = "已放入右侧选用素材。";
                    }
                    catch (Exception error) { add.IsEnabled = true; feedback.Text = error.Message; }
                    return;
                }
                if (await RunProductionJob("attach_search_media", platform, day, J.Obj("page_id", id, "id", draftId,
                    "source_report_id", J.Get(asset, "source_report_id"), "source_media_id", J.Get(asset, "source_media_id")),
                    "正在加入搜索素材…", "素材已加入工作稿。"))
                {
                    add.Content = "已加入";
                    feedback.Text = "在制作台的「媒体素材」里审核。";
                }
                else
                {
                    add.IsEnabled = true;
                    feedback.Text = "加入未完成。若工作稿已切换，请重新打开搜索。";
                }
            };
            if (!saved) buttons.Children.Add(save);
            var preview = new Button { Content = "预览素材", Margin = new Thickness(0, 0, 6, 0) };
            preview.Click += async delegate
            {
                preview.IsEnabled = false;
                feedback.Text = "正在准备原始素材预览…";
                try
                {
                    asset = await saveAsset();
                    saved = true;
                    save.Content = "已保存";
                    save.IsEnabled = false;
                    feedback.Text = J.Text(asset, "resolution_note");
                    if (J.Text(asset, "kind") == "video")
                        ShowProductionVideo(J.Map(await Worker.RunAsync("workspace_media_preview", payload: J.Obj(
                            "page_id", id, "source_report_id", J.Get(asset, "source_report_id"),
                            "source_media_id", J.Get(asset, "source_media_id")))), title, "播放素材");
                    else
                    {
                        var view = new Window { Owner = Window.GetWindow(preview), Title = title, Width = 760, Height = 650,
                            WindowStartupLocation = WindowStartupLocation.CenterOwner, Background = Ui.Color("PaperBrush"),
                            Content = EditorialImage(J.Text(asset, "path"), 580, 740) };
                        view.ShowDialog();
                    }
                }
                catch (Exception error) { feedback.Text = error.Message; }
                finally { preview.IsEnabled = true; }
            };
            buttons.Children.Add(preview);
            if (draftId.Length > 0 || onSelect != null) buttons.Children.Add(add);
            buttons.Children.Add(EditorialLink("查看来源", J.Text(asset, "page_url")));
            body.Children.Add(buttons);
            body.Children.Add(feedback);
            line.Children.Add(body);
            return EditorialRow(line);
        }

        private static Image MaterialSearchThumbnail(IDictionary<string, object> asset)
        {
            string local = J.Text(asset, "preview");
            if (File.Exists(local)) return EditorialImage(local, 76, 200);
            Uri uri;
            if (!Uri.TryCreate(J.Text(asset, "thumbnail_url"), UriKind.Absolute, out uri) || uri.Scheme != "https" ||
                (uri.Host != "upload.wikimedia.org" && uri.Host != "thumb.wikimedia.org" &&
                 !System.Text.RegularExpressions.Regex.IsMatch(uri.Host, @"^p[0-9]\.itc\.cn$"))) return null;
            try
            {
                var bitmap = new BitmapImage();
                bitmap.BeginInit();
                bitmap.DecodePixelWidth = 208;
                bitmap.UriSource = uri;
                bitmap.EndInit();
                var image = new Image { Source = bitmap, Height = 76, Stretch = Stretch.Uniform };
                image.ImageFailed += delegate { image.Visibility = Visibility.Collapsed; };
                return image;
            }
            catch (NotSupportedException) { return null; }
            catch (ArgumentException) { return null; }
        }

    }
}
