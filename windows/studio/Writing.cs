using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;

namespace Acf.Studio
{
    internal sealed partial class WorkbenchWindow
    {
        private void OpenWriting(string topic = null, string platform = "xiaohongshu", string selectedFacts = "",
                                 string[] selectedPaths = null, bool fromHotspot = true, bool textOnly = false,
                                 string[] selectedSourceIds = null)
        {
            var dialog = new Window { Title = "图文与文字", Owner = this, Width = 940, Height = 700,
                MinWidth = 750, MinHeight = 520, Background = Brushes.White, WindowStartupLocation = WindowStartupLocation.CenterOwner };
            var page = new DockPanel { Margin = new Thickness(22) };
            dialog.Content = page;
            var heading = EditorialHeading("图文与文字", "本机生成不同角度的稿件，核对参考出处后再使用。");
            DockPanel.SetDock(heading, Dock.Top);
            page.Children.Add(heading);
            var tabs = new TabControl();
            var grid = new Grid { Margin = new Thickness(14) };
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(278) });
            grid.ColumnDefinitions.Add(new ColumnDefinition());
            tabs.Items.Add(new TabItem { Header = "批量写作", Content = grid });
            page.Children.Add(tabs);

            var form = new StackPanel { Margin = new Thickness(0, 0, 18, 0) };
            grid.Children.Add(Ui.Scroll(form));
            string originalTopic = topic ?? "";
            var title = EditorialField(originalTopic, "写作选题或生活关键词");
            title.MaxLength = 160;
            form.Children.Add(EditorialLabeled("写作选题或生活关键词", title));
            tabs.Items.Add(new TabItem { Header = "案例库", Content = BuildWritingCases(dialog, platform, () => title.Text.Trim()) });
            tabs.SelectedIndex = 0;
            var facts = EditorialField(selectedFacts.Length > 3000 ? selectedFacts.Substring(0, 3000) : selectedFacts, "已核实的事实与素材", 130);
            facts.MaxLength = 3000;
            form.Children.Add(EditorialLabeled("已核实的事实与素材", facts));
            var target = EditorialChoice(EditorialPlatformNames.Concat(new[] { "全部平台" }), Math.Max(0, Array.IndexOf(EditorialPlatforms, platform)), "稿件平台");
            form.Children.Add(EditorialLabeled("稿件平台", target));
            bool xhsHotspot = fromHotspot && originalTopic.Length > 0 && platform == "xiaohongshu";
            var topicMode = new CheckBox { Content = "作为自定主题（不依赖榜单日期）", IsChecked = !xhsHotspot,
                Margin = new Thickness(0, 7, 0, 7) };
            form.Children.Add(topicMode);
            var format = EditorialChoice(new[] { "原文实拍图文", "纯文字文案" }, textOnly ? 1 : 0, "小红书稿件格式");
            var formatRow = EditorialLabeled("小红书稿件格式", format);
            form.Children.Add(formatRow);
            var count = EditorialChoice(new[] { "1 版", "2 版", "3 版" }, 1, "每平台生成数量");
            form.Children.Add(EditorialLabeled("每平台生成数量", count));
            Func<bool> isHotspot = () => xhsHotspot && topicMode.IsChecked != true && target.SelectedIndex == 0 && title.Text.Trim() == originalTopic;
            Func<bool> isNote = () => target.SelectedIndex == 0 && format.SelectedIndex == 0;
            Action updateFormat = () =>
            {
                bool note = isNote();
                formatRow.Visibility = target.SelectedIndex == 0 ? Visibility.Visible : Visibility.Collapsed;
                if (count.Items.Count == (note ? 2 : 3)) return;
                int index = note ? Math.Max(0, count.SelectedIndex + (count.Items.Count == 3 ? -1 : 0))
                    : count.SelectedIndex + (count.Items.Count == 2 ? 1 : 0);
                count.ItemsSource = note ? new[] { "2 版", "3 版" } : new[] { "1 版", "2 版", "3 版" };
                count.SelectedIndex = Math.Max(0, index);
            };
            target.SelectionChanged += (sender, args) => updateFormat();
            format.SelectionChanged += (sender, args) => updateFormat();
            title.TextChanged += (sender, args) => updateFormat();
            updateFormat();
            form.Children.Add(Ui.Text(selectedPaths != null && selectedPaths.Length > 0
                ? "使用本选题已选的 " + selectedPaths.Length + " 项媒体。图文每版至少两张不同实拍；视频不会被当作图片。"
                : "读取同题笔记的原文与清晰配图，各版分别依据对应资料写稿。教程须有真实步骤图；原图保留作者与出处，供审稿。", 13, true));
            var generate = new Button { Content = "生成稿件", Style = (Style)FindResource("PrimaryButton"),
                HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 12, 0, 0) };
            form.Children.Add(generate);
            var status = Ui.Text("", 13, true);
            status.Margin = new Thickness(0, 8, 0, 0);
            form.Children.Add(status);

            var output = new StackPanel();
            var outputScroll = Ui.Scroll(output);
            Grid.SetColumn(outputScroll, 1);
            grid.Children.Add(outputScroll);
            output.Children.Add(Ui.Card(Ui.Text("生成后在这里阅读各版正文、发布配文和配图。", 14, true)));
            generate.Click += async (sender, args) =>
            {
                string chosenTopic = title.Text.Trim();
                if (chosenTopic.Length == 0) { status.Text = "请填写写作选题。"; title.Focus(); return; }
                bool note = isNote();
                string[] platforms = target.SelectedIndex == EditorialPlatforms.Length ? EditorialPlatforms
                    : new[] { EditorialPlatforms[Math.Max(0, target.SelectedIndex)] };
                int copies = count.SelectedIndex + (note ? 2 : 1);
                var payload = J.Obj("topic", chosenTopic, "facts", facts.Text, "count", copies);
                if (note)
                {
                    payload["text_only"] = format.SelectedIndex == 1;
                    payload["photo_first"] = format.SelectedIndex == 0;
                    payload["keyword_topic"] = !isHotspot();
                    if (selectedPaths != null && selectedPaths.Length > 0) payload["asset_paths"] = selectedPaths;
                    if (selectedSourceIds != null && selectedSourceIds.Length > 0) payload["source_ids"] = selectedSourceIds;
                }
                else
                {
                    payload["platforms"] = platforms;
                    if (fromHotspot && originalTopic.Length > 0 && chosenTopic == originalTopic) payload["hotspot_platform"] = platform;
                }
                form.IsEnabled = false;
                status.Text = note ? "正在核对同题案例并生成稿件…" : "正在检索参考写法并生成文案…";
                Notify(status.Text);
                try
                {
                    var batch = J.Map(await Worker.RunAsync(note ? "xiaohongshu_notes" : "copywriting", payload: payload));
                    var successes = J.Map(J.Get(batch, "results"));
                    var errors = J.Map(J.Get(batch, "errors"));
                    status.Text = "已完成 " + successes.Count + " 个平台"
                        + (errors.Count > 0 ? " · " + errors.Count + " 个平台生成失败" : "");
                    Notify(status.Text);
                    if (!dialog.IsVisible) return;
                    output.Children.Clear();
                    var bar = EditorialBar();
                    bar.Children.Add(EditorialLink("打开稿件目录", J.Text(batch, "directory")));
                    var source = J.Map(J.Get(batch, "source"));
                    string sourceUrl = J.Text(source, "url");
                    if (IsEditorialWebLink(sourceUrl)) bar.Children.Add(EditorialLink("核对事实出处", sourceUrl));
                    output.Children.Add(bar);
                    var generation = J.Map(J.Get(batch, "generation"));
                    if (J.Text(generation, "writing_model").Length > 0)
                        output.Children.Add(Ui.Text("本机写稿 · " + J.Text(generation, "writing_model")
                            + "  ·  配图方式 · " + J.Text(generation, "image_mode"), 12, true));
                    foreach (var failure in errors)
                        output.Children.Add(EditorialRow(Ui.Text(EditorialPlatformName(failure.Key) + " · " + failure.Value, 14)));
                    foreach (var result in successes)
                    {
                        var value = J.Map(result.Value);
                        string file = J.Text(value, "file");
                        var header = EditorialBar();
                        header.Children.Add(EditorialStrong(EditorialPlatformName(result.Key), 18));
                        if (File.Exists(file)) header.Children.Add(EditorialLink("打开稿件文件", file));
                        output.Children.Add(header);
                        int i = 0;
                        foreach (var draft in J.Rows(J.Get(value, "drafts")))
                        {
                            var paper = new StackPanel();
                            string name = J.Text(draft, "title");
                            string script = J.Text(draft, "script");
                            string caption = J.Text(draft, "caption");
                            paper.Children.Add(EditorialKicker("第 " + (++i) + " 版  ·  " + J.Text(draft, "angle")));
                            paper.Children.Add(EditorialStrong(name, 20));
                            var copy = EditorialBar();
                            copy.Children.Add(Ui.Button("复制正文", () => CopyEditorialText(script)));
                            copy.Children.Add(Ui.Button("复制发布配文", () => CopyEditorialText(caption)));
                            paper.Children.Add(copy);
                            paper.Children.Add(Ui.Text(script));
                            paper.Children.Add(EditorialKicker("发布配文"));
                            paper.Children.Add(Ui.Text(caption));
                            var reference = J.Map(J.Get(draft, "reference"));
                            string referenceUrl = J.Text(reference, "url");
                            if (IsEditorialWebLink(referenceUrl))
                            {
                                paper.Children.Add(EditorialLink("参考案例 · " + J.Text(reference, "title"), referenceUrl));
                                paper.Children.Add(Ui.Text(J.Text(draft, "borrowed"), 13, true));
                                paper.Children.Add(Ui.Text(J.Text(reference, "evidence"), 13, true));
                            }
                            else if (note) paper.Children.Add(Ui.Text("本版未找到可核实的同题图文案例。", 13, true));
                            var titleReference = J.Map(J.Get(draft, "title_reference"));
                            string titleSource = J.Text(titleReference, "url");
                            if (IsEditorialWebLink(titleSource))
                            {
                                paper.Children.Add(EditorialLink("标题参考 · " + J.Text(titleReference, "title"), titleSource));
                                paper.Children.Add(Ui.Text(J.Text(titleReference, "adapt") + " " + J.Text(titleReference, "evidence"), 13, true));
                            }
                            var pictures = EditorialBar();
                            foreach (var picture in J.Rows(J.Get(draft, "pictures")))
                            {
                                string path = J.Text(picture, "file");
                                var tile = new StackPanel { Width = 142, Margin = new Thickness(0, 8, 8, 8) };
                                var image = EditorialImage(path, 160, 220);
                                if (image.Source != null) tile.Children.Add(image);
                                tile.Children.Add(EditorialLink("打开配图", path));
                                var photo = J.Map(J.Get(picture, "photo"));
                                string photoUrl = J.Text(photo, "page_url");
                                if (IsEditorialWebLink(photoUrl))
                                {
                                    tile.Children.Add(Ui.Text(J.Text(photo, "author") + " · " + J.Text(photo, "license"), 11, true));
                                    tile.Children.Add(EditorialLink("照片出处", photoUrl));
                                }
                                pictures.Children.Add(tile);
                            }
                            if (pictures.Children.Count > 0) paper.Children.Add(pictures);
                            var card = EditorialRow(paper);
                            card.Padding = new Thickness(16);
                            output.Children.Add(card);
                        }
                    }
                    outputScroll.ScrollToTop();
                }
                catch (Exception error) { status.Text = error.Message; Notify(error.Message); }
                finally { form.IsEnabled = true; }
            };
            dialog.ShowDialog();
        }

        private FrameworkElement BuildWritingCases(Window owner, string platform, Func<string> topic)
        {
            var page = new DockPanel { Margin = new Thickness(14) };
            var header = new StackPanel();
            DockPanel.SetDock(header, Dock.Top);
            page.Children.Add(header);
            var filters = EditorialBar();
            var platformChoice = EditorialChoice(EditorialPlatformNames, Math.Max(0, Array.IndexOf(EditorialPlatforms, platform)), "案例平台");
            var query = EditorialField("", "搜索案例、关键词或写法");
            query.Width = 212;
            var refresh = new Button { Content = "刷新", Margin = new Thickness(0, 0, 8, 8), VerticalAlignment = VerticalAlignment.Bottom };
            var add = new Button { Content = "录入案例", Style = (Style)FindResource("PrimaryButton"),
                Margin = new Thickness(0, 0, 0, 8), VerticalAlignment = VerticalAlignment.Bottom };
            filters.Children.Add(EditorialLabeled("案例平台", platformChoice));
            filters.Children.Add(EditorialLabeled("搜索案例或写法", query));
            filters.Children.Add(refresh);
            filters.Children.Add(add);
            header.Children.Add(filters);
            var importTools = new StackPanel { Margin = new Thickness(14, 6, 0, 2) };
            var publicUrl = EditorialField("", "公开作品链接或分享文字", 52);
            publicUrl.MaxLength = 3000;
            importTools.Children.Add(EditorialLabeled("公开作品链接或分享文字", publicUrl));
            var actions = EditorialBar();
            var import = new Button { Content = "采集此链接", Margin = new Thickness(0, 0, 6, 4) };
            var collect = new Button { Content = "采集写作选题的同题案例", Margin = new Thickness(0, 0, 6, 4) };
            var learn = new Button { Content = "处理本机学习队列", Margin = new Thickness(0, 0, 0, 4) };
            actions.Children.Add(import);
            actions.Children.Add(collect);
            actions.Children.Add(learn);
            importTools.Children.Add(actions);
            header.Children.Add(EditorialRow(new Expander { Header = "采集与学习", Content = importTools }));
            var feedback = Ui.Text("", 13, true);
            feedback.Margin = new Thickness(0, 2, 0, 6);
            header.Children.Add(feedback);

            var footer = Ui.Text("", 12, true);
            footer.Margin = new Thickness(0, 8, 0, 0);
            DockPanel.SetDock(footer, Dock.Bottom);
            page.Children.Add(footer);
            var columns = new Grid();
            columns.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(2, GridUnitType.Star), MinWidth = 230 });
            columns.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(3, GridUnitType.Star) });
            page.Children.Add(columns);
            var list = new ListBox { Margin = new Thickness(0, 0, 12, 0), BorderBrush = EditorialLine };
            AutomationProperties.SetName(list, "匹配的写法案例");
            columns.Children.Add(list);
            var detail = new StackPanel { Margin = new Thickness(10) };
            var reader = Ui.Scroll(detail);
            Grid.SetColumn(reader, 1);
            columns.Children.Add(reader);

            var rows = new List<IDictionary<string, object>>();
            Func<string> selectedPlatform = () => EditorialPlatforms[Math.Max(0, platformChoice.SelectedIndex)];
            Action describe = () =>
            {
                detail.Children.Clear();
                var selected = list.SelectedItem as ListBoxItem;
                if (selected == null)
                {
                    string hint = list.Items.Count > 0 ? "选择案例查看写法摘要和热度依据。"
                        : query.Text.Trim().Length > 0 ? "没有符合搜索条件的案例。" : "此处显示所选平台的本机案例。";
                    detail.Children.Add(Ui.Text(hint, 14, true));
                    return;
                }
                var row = J.Map(selected.Tag);
                detail.Children.Add(EditorialKicker(EditorialPlatformName(selectedPlatform()) + "  ·  写法案例"));
                detail.Children.Add(EditorialStrong(J.Text(row, "title"), 20));
                detail.Children.Add(WritingCaseDetail("热度依据", J.Text(row, "evidence")));
                string keywords = J.Text(row, "keywords");
                if (keywords.Length > 0) detail.Children.Add(WritingCaseDetail("关键词", keywords));
                detail.Children.Add(WritingCaseDetail("写法摘要", J.Text(row, "notes")));
                string url = J.Text(row, "url");
                if (IsEditorialWebLink(url)) detail.Children.Add(EditorialLink("打开出处", url));
                reader.ScrollToTop();
            };
            Action draw = () =>
            {
                var previous = list.SelectedItem as ListBoxItem;
                var previousRow = previous == null ? J.Obj() : J.Map(previous.Tag);
                ListBoxItem retained = null;
                list.Items.Clear();
                foreach (var row in rows)
                {
                    string name = J.Text(row, "title");
                    string evidence = J.Text(row, "evidence");
                    if (!EditorialMatches(query.Text, name, evidence + " " + J.Text(row, "keywords") + " " + J.Text(row, "notes"))) continue;
                    var label = new StackPanel();
                    label.Children.Add(EditorialStrong(name, 14));
                    label.Children.Add(Ui.Text(evidence, 12, true));
                    var item = new ListBoxItem { Content = label, Tag = row };
                    list.Items.Add(item);
                    if (J.Text(previousRow, "url") == J.Text(row, "url") && J.Text(previousRow, "title") == name) retained = item;
                }
                list.SelectedItem = retained;
                describe();
            };
            list.SelectionChanged += (sender, args) => describe();
            query.TextChanged += (sender, args) => draw();
            int request = 0;
            Func<Task> load = async () =>
            {
                int token = ++request;
                string target = selectedPlatform();
                try
                {
                    var result = J.Map(await Worker.RunAsync("cases_list", target));
                    if (token != request) return;
                    rows = J.Rows(J.Get(result, "cases")).ToList();
                    string last = J.Text(result, "last_refresh");
                    footer.Text = rows.Count + " 条本机案例" + (last.Length > 0 ? "  ·  最近学习  " + last : "");
                    draw();
                }
                catch (Exception error) { if (token == request) feedback.Text = error.Message; }
            };
            Action<bool> setBusy = busy =>
            {
                refresh.IsEnabled = add.IsEnabled = import.IsEnabled = collect.IsEnabled = learn.IsEnabled = !busy;
            };
            Func<string, string, Task> collectPublic = async (subject, url) =>
            {
                setBusy(true);
                feedback.Text = "正在读取公开作品并提炼写法…";
                try
                {
                    var result = J.Map(await Worker.RunAsync("cases_collect", selectedPlatform(),
                        J.Obj("topic", subject, "url", url, "interactive", true)));
                    string saved = String.Join("、", J.Items(J.Get(result, "saved")));
                    string error = J.Text(result, "error");
                    feedback.Text = saved.Length > 0 ? "写法已入库 · " + saved
                        : error.Length > 0 ? error : "暂无新的可读案例。";
                    if (saved.Length > 0 && url == publicUrl.Text.Trim()) publicUrl.Clear();
                    await load();
                }
                catch (Exception error) { feedback.Text = error.Message; }
                finally { setBusy(false); }
            };
            page.Loaded += async (sender, args) => await load();
            page.Unloaded += (sender, args) => request++;
            platformChoice.SelectionChanged += async (sender, args) =>
            {
                rows.Clear();
                draw();
                await load();
            };
            refresh.Click += async (sender, args) => await load();
            add.Click += async (sender, args) =>
            {
                string result = OpenWritingCaseEditor(owner, selectedPlatform());
                if (result.Length == 0) return;
                feedback.Text = result;
                await load();
            };
            import.Click += async (sender, args) =>
            {
                string url = publicUrl.Text.Trim();
                if (url.Length == 0) { feedback.Text = "请粘贴公开作品链接或分享文字。"; publicUrl.Focus(); return; }
                await collectPublic("", url);
            };
            collect.Click += async (sender, args) =>
            {
                string subject = topic();
                if (subject.Length == 0) { feedback.Text = "请先在「批量写作」填写选题。"; return; }
                await collectPublic(subject, "");
            };
            learn.Click += async (sender, args) =>
            {
                setBusy(true);
                feedback.Text = "正在处理一条本机学习队列…";
                try
                {
                    var result = J.Map(await Worker.RunAsync("cases_learn"));
                    string saved = String.Join("、", J.Items(J.Get(result, "saved")));
                    string error = J.Text(result, "error");
                    feedback.Text = saved.Length > 0 ? "写法已入库 · " + saved
                        : error.Length > 0 ? error : "当前没有可处理的排队片段。";
                    await load();
                }
                catch (Exception error) { feedback.Text = error.Message; }
                finally { setBusy(false); }
            };
            describe();
            return page;
        }

        private string OpenWritingCaseEditor(Window owner, string platform)
        {
            var dialog = new Window { Title = "录入案例 · " + EditorialPlatformName(platform), Owner = owner, Width = 630,
                Height = 650, MinWidth = 520, MinHeight = 450, Background = Brushes.White,
                WindowStartupLocation = WindowStartupLocation.CenterOwner };
            var page = new DockPanel { Margin = new Thickness(18) };
            dialog.Content = page;
            var footer = new DockPanel { Margin = new Thickness(0, 10, 0, 0) };
            var save = new Button { Content = "保存案例", Style = (Style)FindResource("PrimaryButton") };
            DockPanel.SetDock(save, Dock.Right);
            footer.Children.Add(save);
            var message = Ui.Text("", 13, true);
            message.VerticalAlignment = VerticalAlignment.Center;
            footer.Children.Add(message);
            DockPanel.SetDock(footer, Dock.Bottom);
            page.Children.Add(footer);
            var form = new StackPanel();
            page.Children.Add(Ui.Scroll(form));
            form.Children.Add(EditorialHeading("录入案例", "保留可借鉴的写法，并记录能打开的来源。"));
            var name = EditorialField("", "案例名称");
            name.MaxLength = 120;
            var url = EditorialField("", "HTTPS 出处");
            url.MaxLength = 2000;
            var evidence = EditorialField("", "热度依据");
            evidence.MaxLength = 240;
            var keywords = EditorialField("", "关键词");
            keywords.MaxLength = 120;
            var notes = EditorialField("", "写法摘要", 65);
            notes.MaxLength = 600;
            var excerpt = EditorialField("", "参考片段", 85);
            excerpt.MaxLength = 6000;
            form.Children.Add(EditorialLabeled("案例名称", name));
            form.Children.Add(EditorialLabeled("HTTPS 出处", url));
            form.Children.Add(EditorialLabeled("热度依据（指标、时间或榜单）", evidence));
            form.Children.Add(EditorialLabeled("关键词", keywords));
            form.Children.Add(EditorialLabeled("写法摘要", notes));
            form.Children.Add(EditorialLabeled("参考片段（可选，仅由本机模型提炼）", excerpt));
            var actions = EditorialBar();
            var analyze = new Button { Content = "本机 AI 提炼写法", Margin = new Thickness(0, 0, 6, 0) };
            var queue = new Button { Content = "加入本机学习队列" };
            actions.Children.Add(analyze);
            actions.Children.Add(queue);
            form.Children.Add(actions);
            string completion = "";
            Func<string, Task> apply = async stage =>
            {
                if (stage != "cases_add" && excerpt.Text.Trim().Length < 20)
                {
                    message.Text = "请粘贴至少 20 字的参考片段。";
                    excerpt.Focus();
                    return;
                }
                var payload = J.Obj("title", name.Text, "url", url.Text, "evidence", evidence.Text, "keywords", keywords.Text);
                if (stage == "cases_analyze") payload = J.Obj("excerpt", excerpt.Text);
                else if (stage == "cases_queue") payload["excerpt"] = excerpt.Text;
                else payload["notes"] = notes.Text;
                save.IsEnabled = form.IsEnabled = false;
                message.Text = stage == "cases_analyze" ? "本机模型正在提炼…" : "正在保存…";
                try
                {
                    var result = await Worker.RunAsync(stage, platform, payload);
                    if (!dialog.IsVisible) return;
                    if (stage == "cases_analyze")
                    {
                        keywords.Text = J.Text(J.Map(result), "keywords");
                        notes.Text = J.Text(J.Map(result), "notes");
                        message.Text = "提炼完成，可检查后保存。";
                    }
                    else
                    {
                        completion = stage == "cases_queue" ? "片段已加入本机学习队列。" : "案例已保存。";
                        dialog.Close();
                    }
                }
                catch (Exception error) { message.Text = error.Message; }
                finally { save.IsEnabled = form.IsEnabled = true; }
            };
            save.Click += async (sender, args) => await apply("cases_add");
            analyze.Click += async (sender, args) => await apply("cases_analyze");
            queue.Click += async (sender, args) => await apply("cases_queue");
            dialog.Loaded += (sender, args) => name.Focus();
            dialog.ShowDialog();
            return completion;
        }

        private static FrameworkElement WritingCaseDetail(string label, string text)
        {
            var item = new StackPanel { Margin = new Thickness(0, 12, 0, 0) };
            item.Children.Add(EditorialKicker(label));
            item.Children.Add(Ui.Text(text));
            return item;
        }

        private void CopyEditorialText(string value)
        {
            try { Clipboard.SetText(value); Notify("已复制到剪贴板"); }
            catch (Exception error) { Notify(error.Message); }
        }
    }
}
