using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;

namespace Acf.Studio
{
    internal sealed partial class WorkbenchWindow
    {
        private static readonly string[] CutMotionKeys = { "", "cut", "fade", "push", "pull", "wipe", "slide", "dissolve", "path", "scan" };
        private static readonly string[] CutMotionNames = { "自动", "直接切入", "淡入", "缓慢推进", "缓慢拉远", "擦拭转场",
                                                            "滑移转场", "交叉溶解", "动态路径图", "动态观测图" };

        private sealed class CutScene
        {
            public IDictionary<string, object> Original, Marker, Preview;
            public string Title, Text, Media, Motion;
            public int OriginalIndex;
            public bool Zoom, Confirmed;
        }

        private static HashSet<int> ConfirmedCut(IDictionary<string, object> draft, bool saved, int count)
        {
            return new HashSet<int>(draft.ContainsKey("confirmed_scenes")
                ? J.Items(J.Get(draft, "confirmed_scenes")).OfType<int>()
                : saved ? Enumerable.Range(0, count) : Enumerable.Empty<int>());
        }

        private FrameworkElement ProductionTimeline(IDictionary<string, object> draft, int seconds, bool saved, Action<int> open)
        {
            var scenes = J.Rows(J.Get(draft, "scenes")).ToList();
            var confirmed = ConfirmedCut(draft, saved, scenes.Count);
            var chapters = J.Rows(J.Get(draft, "chapter_starts")).ToList();
            var starts = chapters.Select(chapter =>
            {
                int index;
                return Int32.TryParse(J.Text(chapter, "scene"), out index) ? index : 0;
            }).Where(index => index > 0 && index < scenes.Count).OrderBy(index => index).ToArray();
            int ending = starts.Length > 0 ? starts.Last() : Math.Max(1, scenes.Count - 1);
            int opening = starts.Length > 1 ? starts[0] : 1;
            var box = new StackPanel();
            var summary = EditorialBar();
            var heading = EditorialStrong("全片分镜 · " + scenes.Count + " 镜", 18);
            heading.Margin = new Thickness(0, 0, 9, 0);
            summary.Children.Add(heading);
            summary.Children.Add(Ui.Badge("目标 " + TimeSpan.FromSeconds(seconds).ToString(@"mm\:ss"), "Honey"));
            var confirmations = Ui.Badge("已确认 " + confirmed.Count + " / " + scenes.Count, "Sage");
            confirmations.Margin = new Thickness(6, 0, 0, 0);
            summary.Children.Add(confirmations);
            box.Children.Add(summary);
            box.Children.Add(Ui.Text("位置按口播字数估算。选择分镜可编辑画面和口播。", 13, true));
            foreach (string warning in J.Items(J.Get(J.Map(J.Get(draft, "editorial_plan")), "visual_warnings")).OfType<string>())
                box.Children.Add(Ui.Text(warning, 13, true));
            var grid = new DockPanel { Margin = new Thickness(0, 12, 0, 2) };
            var labels = new StackPanel { Width = 48 };
            foreach (string label in new[] { "分镜", "位置", "段落" })
            {
                var text = Ui.Text(label, 12, true);
                text.Height = 24;
                labels.Children.Add(text);
            }
            DockPanel.SetDock(labels, Dock.Left);
            grid.Children.Add(labels);
            var track = new StackPanel { Orientation = Orientation.Horizontal };
            var lengths = scenes.Select(scene => Math.Max(1, J.Text(scene, "text").Length)).ToList();
            int total = Math.Max(1, lengths.Sum());
            int elapsed = 0;
            for (int i = 0; i < scenes.Count; i++)
            {
                int index = i;
                var scene = scenes[i];
                string title = J.Text(scene, "title");
                string start = TimeSpan.FromSeconds(seconds * (double)elapsed / total).ToString(@"mm\:ss");
                elapsed += lengths[i];
                string phase = i < opening ? "开场" : i >= ending ? "片尾" : "正文";
                var tint = Ui.Color(i < opening ? "HoneyBrush" : i >= ending ? "SkyBrush" : "SageBrush");
                var cell = new Button
                {
                    Content = new StackPanel(),
                    Padding = new Thickness(0), Margin = new Thickness(0, 0, 3, 0),
                    HorizontalContentAlignment = HorizontalAlignment.Stretch,
                    Width = Math.Max(78, 710d * lengths[i] / total), IsEnabled = open != null,
                    ToolTip = phase + " · " + start + " · " + title + (confirmed.Contains(index) ? " · 已确认" : " · 待确认")
                };
                var tracks = (StackPanel)cell.Content;
                foreach (var entry in new[] {
                    new { Text = title, Fill = tint },
                    new { Text = start, Fill = Ui.Color("SurfaceBrush") },
                    new { Text = (i + 1).ToString("00") + " " + phase, Fill = tint }
                })
                {
                    var caption = Ui.Text(entry.Text, 11, false);
                    caption.TextWrapping = TextWrapping.NoWrap;
                    caption.TextTrimming = TextTrimming.CharacterEllipsis;
                    tracks.Children.Add(new Border { Height = 24, Background = entry.Fill, Padding = new Thickness(5, 2, 5, 0), Child = caption });
                }
                AutomationProperties.SetName(cell, "分镜 " + (i + 1) + " " + phase + " " + title +
                    (confirmed.Contains(index) ? " 已确认" : " 待确认"));
                cell.Click += (sender, args) => { if (open != null) open(index); };
                track.Children.Add(cell);
            }
            grid.Children.Add(new ScrollViewer { HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
                VerticalScrollBarVisibility = ScrollBarVisibility.Disabled, Content = track });
            box.Children.Add(grid);
            if (chapters.Count > 0)
            {
                var details = new StackPanel { Margin = new Thickness(10, 5, 0, 0) };
                foreach (var chapter in chapters)
                {
                    details.Children.Add(EditorialStrong(J.Text(chapter, "title"), 14));
                    var reference = J.Map(J.Get(chapter, "style_reference"));
                    string link = J.Text(reference, "url");
                    if (IsEditorialWebLink(link)) details.Children.Add(EditorialLink("写法案例 · " + J.Text(reference, "title"), link));
                    if (reference.Count > 0)
                    {
                        details.Children.Add(Ui.Text(J.Text(reference, "match_kind") + " · " + J.Text(reference, "evidence"), 12, true));
                        details.Children.Add(Ui.Text("本章借鉴 · " + J.Text(reference, "notes"), 13, true));
                    }
                    var style = J.Map(J.Get(chapter, "video_style"));
                    var speech = J.Map(J.Get(style, "speech"));
                    if (speech.Count > 0)
                    {
                        details.Children.Add(EditorialStrong(J.Text(speech, "name"), 14));
                        details.Children.Add(Ui.Text("口播 · " + J.Text(speech, "sentence"), 13, true));
                        details.Children.Add(Ui.Text("画面 · " + J.Text(speech, "animation"), 13, true));
                    }
                    var videoReference = J.Map(J.Get(style, "reference"));
                    if (IsEditorialWebLink(J.Text(videoReference, "url")))
                    {
                        details.Children.Add(Ui.Text(J.Text(style, "name") + " · " + J.Text(videoReference, "adapt"), 13, true));
                        details.Children.Add(VideoReferenceLink(videoReference));
                    }
                }
                box.Children.Add(new Expander { Header = "口播参考、平台写法与画面安排", Content = details, IsExpanded = true });
            }
            return EditorialRow(box);
        }

        private void OpenCutEditor(string platform, string day, IDictionary<string, object> item,
                                   IDictionary<string, object> draft, int initial, bool suggestion, bool manual = false)
        {
            var markers = J.Rows(J.Get(draft, "chapter_starts")).ToList();
            var originals = J.Rows(J.Get(draft, "scenes")).ToArray();
            var confirmed = ConfirmedCut(draft, !suggestion && J.Map(J.Get(item, "edit")).Count > 0, originals.Length);
            var scenes = originals.Select((row, index) => new CutScene {
                Original = row, Marker = markers.FirstOrDefault(marker => J.Text(marker, "scene") == index.ToString()),
                OriginalIndex = index, Confirmed = confirmed.Contains(index),
                Title = J.Text(row, "title"), Text = J.Text(row, "text"), Media = J.Text(row, "media"),
                Motion = J.Text(row, "motion"), Zoom = Equals(J.Get(row, "zoom"), true)
            }).ToList();
            if (scenes.Count == 0) { Notify("没有可编辑的分镜。"); return; }
            var media = J.Rows(J.Get(item, "media")).Where(asset => ProductionMediaState(item, asset) == "approved").ToList();
            var mediaLabels = new[] { "文字卡片" }.Concat(media.Select((asset, index) =>
                (index + 1).ToString("00") + " · " + EditorialTruncate(J.Text(asset, "title"), 48))).ToArray();
            var mediaKeys = new[] { "" }.Concat(media.Select(asset => J.Text(asset, "page_url"))).ToArray();
            var plan = J.Map(J.Get(draft, "editorial_plan"));
            var dialog = new Window { Title = manual ? "新建自拟选题" : "编辑分镜 · " + EditorialPlatformName(platform),
                Owner = this, Width = 900, Height = 620, MinWidth = 710, MinHeight = 500,
                Background = Ui.Color("PaperBrush"), WindowStartupLocation = WindowStartupLocation.CenterOwner, Icon = Icon };
            var page = new DockPanel { Margin = new Thickness(22) };
            dialog.Content = page;
            var top = EditorialHeading(manual ? "自拟分镜" : "逐镜精修口播与画面",
                "左侧点选镜头，右侧改口播或换画面素材。点「锁定此镜」后，后续 AI 自动重剪会保留该镜不覆盖；改完点右下角保存。");
            var storyTitle = EditorialField("", "选题标题");
            storyTitle.MaxLength = 72;
            var throughline = EditorialField(J.Text(plan, "throughline"), "全片主线");
            throughline.MaxLength = 180;
            if (manual) top.Children.Add(EditorialLabeled("选题标题", storyTitle));
            if (plan.Count > 0) top.Children.Add(EditorialLabeled("全片主线", throughline));
            DockPanel.SetDock(top, Dock.Top);
            page.Children.Add(top);

            var footer = new DockPanel { Margin = new Thickness(0, 12, 0, 0) };
            var footerRight = new StackPanel { Orientation = Orientation.Horizontal };
            var lockAllSave = new Button { Content = "全部锁定并保存", Margin = new Thickness(0, 0, 8, 0) };
            var save = new Button { Content = "保存分镜草稿", Style = (Style)FindResource("PrimaryButton") };
            footerRight.Children.Add(lockAllSave);
            footerRight.Children.Add(save);
            DockPanel.SetDock(footerRight, Dock.Right);
            footer.Children.Add(footerRight);
            var message = Ui.Text("", 13, true);
            message.VerticalAlignment = VerticalAlignment.Center;
            footer.Children.Add(message);
            DockPanel.SetDock(footer, Dock.Bottom);
            page.Children.Add(footer);
            var outline = new DockPanel { Width = 235, Margin = new Thickness(0, 0, 16, 0) };
            var list = new ListBox { BorderBrush = EditorialLine };
            AutomationProperties.SetName(list, "分镜列表");
            ScrollViewer.SetHorizontalScrollBarVisibility(list, ScrollBarVisibility.Disabled);
            var moves = EditorialBar();
            var up = new Button { Content = "上移" };
            var down = new Button { Content = "下移", Margin = new Thickness(5, 0, 0, 0) };
            var remove = new Button { Content = "删除分镜", Foreground = Ui.Color("BlushInkBrush"), Margin = new Thickness(5, 0, 0, 0) };
            var undo = new Button { Content = "撤销", Margin = new Thickness(0, 5, 0, 0) };
            var history = new Stack<Action>();
            moves.Children.Add(up);
            moves.Children.Add(down);
            moves.Children.Add(remove);
            moves.Children.Add(undo);
            ToolTipService.SetShowOnDisabled(remove, true);
            DockPanel.SetDock(moves, Dock.Bottom);
            outline.Children.Add(moves);
            outline.Children.Add(list);
            DockPanel.SetDock(outline, Dock.Left);
            page.Children.Add(outline);

            var form = new StackPanel();
            var caption = EditorialKicker("");
            var title = EditorialField("", "分镜标题");
            title.MaxLength = 36;
            var text = EditorialField("", "分镜口播", 126);
            text.MaxLength = 280;
            var picture = EditorialChoice(mediaLabels, 0, "分镜画面");
            var zoom = new CheckBox { Content = "照片缓慢推近", Margin = new Thickness(0, 5, 0, 8) };
            var motion = EditorialChoice(CutMotionNames, 0, "镜头动效");
            var visual = Ui.Text("", 13, true);
            var shotStatus = Ui.Text("", 13, true);
            var shotActions = EditorialBar();
            shotActions.Margin = new Thickness(0, 8, 0, 6);
            var direct = new Button { Content = "AI 剪这一镜", Margin = new Thickness(0, 0, 6, 4),
                ToolTip = "使用你选的素材。画面留空时，AI 可从已批准素材中选镜。" };
            var preview = new Button { Content = "播放这镜试剪", Margin = new Thickness(0, 0, 6, 4) };
            var confirm = new Button { Content = "锁定此镜（AI 重剪保留）", Style = (Style)FindResource("PrimaryButton"),
                Margin = new Thickness(0, 0, 6, 4) };
            shotActions.Children.Add(direct);
            shotActions.Children.Add(preview);
            shotActions.Children.Add(confirm);
            form.Children.Add(caption);
            form.Children.Add(EditorialLabeled("分镜标题", title));
            form.Children.Add(EditorialLabeled("口播", text));
            form.Children.Add(EditorialLabeled("画面", picture));
            form.Children.Add(zoom);
            if (markers.Count > 0 || scenes.Any(scene => scene.Motion.Length > 0))
                form.Children.Add(EditorialLabeled("镜头动效", motion));
            form.Children.Add(visual);
            form.Children.Add(shotActions);
            form.Children.Add(shotStatus);
            page.Children.Add(Ui.Scroll(form));
            bool updating = false;
            CutScene selected = null;
            Func<CutScene, bool> hasText = scene => !String.IsNullOrWhiteSpace(scene.Title) && !String.IsNullOrWhiteSpace(scene.Text);
            Func<CutScene, int, string> sceneName = (scene, index) =>
                (index + 1).ToString("00") + "   " + scene.Title + (scene.Confirmed ? " · 已确认" : "");
            Action showShot = () =>
            {
                var visualPlan = J.Map(J.Get(J.Map(CutPayload(selected)), "visual"));
                visual.Text = visualPlan.Count == 0 ? "" : J.Text(visualPlan, "intent") + " · " + J.Text(visualPlan, "reason")
                    + (selected.Media.Length > 0 ? " · 入点 " + J.Text(visualPlan, "start") + " 秒" : "")
                    + (visualPlan.ContainsKey("end") ? " · 出点 " + J.Text(visualPlan, "end") + " 秒" : "");
                direct.IsEnabled = !manual && hasText(selected);
                direct.Content = selected.Preview == null ? "AI 剪这一镜" : "AI 重剪这一镜";
                preview.IsEnabled = selected.Preview != null;
                confirm.IsEnabled = hasText(selected) && !selected.Confirmed;
                shotStatus.Text = selected.Confirmed ? "已确认，保存草稿后整片剪辑会保留此镜。" :
                    manual ? "保存草稿后可使用 AI 剪辑。" : selected.Preview != null ? "试剪已就绪，可以播放并确认此镜。" : "待确认";
                var label = list.SelectedItem as TextBlock;
                if (label != null) label.Text = sceneName(selected, list.SelectedIndex);
            };
            Action editedShot = () =>
            {
                selected.Confirmed = false;
                selected.Preview = null;
                showShot();
            };
            Func<int, bool> canMove = offset =>
            {
                int index = scenes.IndexOf(selected);
                int next = index + offset;
                return index >= 0 && next >= 0 && next < scenes.Count && selected.Marker == null && scenes[next].Marker == null;
            };
            Action<CutScene> fill = scene =>
            {
                updating = true;
                selected = scene;
                caption.Text = scene.Marker == null ? "分镜 " + (scenes.IndexOf(scene) + 1).ToString("00")
                    : "章节  ·  " + J.Text(scene.Marker, "title");
                title.Text = scene.Title;
                text.Text = scene.Text;
                picture.SelectedIndex = Math.Max(0, Array.IndexOf(mediaKeys, scene.Media));
                zoom.IsChecked = scene.Zoom;
                motion.SelectedIndex = Math.Max(0, Array.IndexOf(CutMotionKeys, scene.Motion));
                showShot();
                up.IsEnabled = canMove(-1);
                down.IsEnabled = canMove(1);
                remove.IsEnabled = CanDeleteCutScene(scenes, scenes.IndexOf(scene));
                remove.ToolTip = remove.IsEnabled ? "从草稿删除此镜，可撤销。" :
                    scenes.Count <= 2 ? "草稿至少保留两个分镜。" : "本章至少保留一个分镜。";
                undo.IsEnabled = history.Count > 0;
                updating = false;
            };
            Action<int> populate = index =>
            {
                updating = true;
                list.Items.Clear();
                for (int i = 0; i < scenes.Count; i++)
                    list.Items.Add(Ui.Text(sceneName(scenes[i], i), 14));
                list.SelectedIndex = index;
                fill(scenes[index]);
                list.ScrollIntoView(list.SelectedItem);
            };
            list.SelectionChanged += (sender, args) => { if (!updating && list.SelectedIndex >= 0) fill(scenes[list.SelectedIndex]); };
            title.TextChanged += (sender, args) =>
            {
                if (updating || selected == null) return;
                selected.Title = title.Text;
                editedShot();
            };
            text.TextChanged += (sender, args) =>
            {
                if (updating || selected == null) return;
                selected.Text = text.Text;
                editedShot();
            };
            picture.SelectionChanged += (sender, args) =>
            {
                if (updating || selected == null || picture.SelectedIndex < 0) return;
                selected.Media = mediaKeys[picture.SelectedIndex];
                editedShot();
            };
            RoutedEventHandler zoomChanged = (sender, args) =>
            {
                if (updating || selected == null) return;
                selected.Zoom = zoom.IsChecked == true;
                editedShot();
            };
            zoom.Checked += zoomChanged;
            zoom.Unchecked += zoomChanged;
            motion.SelectionChanged += (sender, args) =>
            {
                if (updating || selected == null || motion.SelectedIndex < 0) return;
                selected.Motion = CutMotionKeys[motion.SelectedIndex];
                editedShot();
            };
            direct.Click += async (sender, args) =>
            {
                var scene = selected;
                if (manual || !hasText(scene)) return;
                page.IsEnabled = false;
                message.Text = "正在剪辑当前分镜并渲染试剪…";
                try
                {
                    var result = J.Map(await Worker.RunAsync("direct_scene", platform,
                        J.Obj("id", J.Get(item, "id"), "revision", J.Get(item, "edit_revision"),
                            "suggestion_revision", J.Get(item, "suggestion_revision"),
                            "source", suggestion ? "suggestion" : "edit", "index", scene.OriginalIndex,
                            "scene", CutPayload(scene)), day));
                    if (!dialog.IsVisible) return;
                    scene.Original = J.Map(J.Get(result, "scene"));
                    scene.Preview = J.Map(J.Get(result, "preview"));
                    scene.Title = J.Text(scene.Original, "title");
                    scene.Text = J.Text(scene.Original, "text");
                    scene.Media = J.Text(scene.Original, "media");
                    scene.Motion = J.Text(scene.Original, "motion");
                    scene.Zoom = Equals(J.Get(scene.Original, "zoom"), true);
                    scene.Confirmed = false;
                    fill(scene);
                    message.Text = "当前分镜试剪已就绪。";
                }
                catch (Exception error) { if (dialog.IsVisible) message.Text = error.Message; }
                finally { if (dialog.IsVisible) page.IsEnabled = true; }
            };
            preview.Click += delegate
            {
                if (selected.Preview != null) ShowProductionVideo(selected.Preview, selected.Title, "播放试剪", dialog);
            };
            confirm.Click += delegate
            {
                if (!hasText(selected)) return;
                selected.Confirmed = true;
                showShot();
            };
            Action remember = () =>
            {
                var order = scenes.ToArray();
                var originalMarkers = order.Select(scene => scene.Marker).ToArray();
                int index = scenes.IndexOf(selected);
                history.Push(() =>
                {
                    scenes.Clear();
                    scenes.AddRange(order);
                    for (int i = 0; i < order.Length; i++) order[i].Marker = originalMarkers[i];
                    populate(index);
                });
            };
            Action<int> move = delta =>
            {
                if (!canMove(delta)) return;
                remember();
                int index = scenes.IndexOf(selected);
                var value = selected;
                scenes.RemoveAt(index);
                scenes.Insert(index + delta, value);
                populate(index + delta);
            };
            up.Click += (sender, args) => move(-1);
            down.Click += (sender, args) => move(1);
            remove.Click += delegate
            {
                int index = scenes.IndexOf(selected);
                if (!CanDeleteCutScene(scenes, index)) return;
                remember();
                DeleteCutScene(scenes, index);
                populate(Math.Min(index, scenes.Count - 1));
                message.Text = "分镜已从当前草稿移除，可撤销。";
            };
            undo.Click += delegate
            {
                if (history.Count == 0) return;
                history.Pop()();
                message.Text = "已撤销上一次顺序改动。";
            };
            save.Click += async (sender, args) =>
            {
                var blank = scenes.FirstOrDefault(scene => String.IsNullOrWhiteSpace(scene.Title) || String.IsNullOrWhiteSpace(scene.Text));
                if (blank != null)
                {
                    populate(scenes.IndexOf(blank));
                    message.Text = "每个分镜都需要标题和口播。";
                    return;
                }
                if (manual && String.IsNullOrWhiteSpace(storyTitle.Text)) { message.Text = "请填写选题标题。"; return; }
                if (plan.Count > 0 && String.IsNullOrWhiteSpace(throughline.Text)) { message.Text = "请填写全片主线。"; return; }
                var cut = J.Obj("scenes", scenes.Select(CutPayload).ToList(),
                    "confirmed_scenes", Enumerable.Range(0, scenes.Count).Where(index => scenes[index].Confirmed).ToArray());
                if (markers.Count > 0)
                    cut["chapter_starts"] = scenes.Select((scene, index) => scene.Marker == null ? null
                        : J.Obj("scene", index, "title", J.Text(scene.Marker, "title"))).Where(marker => marker != null).ToList();
                if (plan.Count > 0)
                {
                    var revised = new Dictionary<string, object>(plan);
                    revised["throughline"] = throughline.Text.Trim();
                    cut["editorial_plan"] = revised;
                }
                object payload = cut;
                if (manual) payload = J.Obj("title", storyTitle.Text.Trim(), "cut", cut);
                else
                {
                    cut["episode_id"] = J.Get(item, "id");
                    cut["expected_revision"] = J.Get(item, "edit_revision");
                    cut["expected_suggestion_revision"] = J.Get(item, "suggestion_revision");
                    cut["draft_source"] = suggestion ? "suggestion" : "edit";
                }
                page.IsEnabled = false;
                message.Text = "正在保存…";
                try
                {
                    await Worker.RunAsync(manual ? "create" : "save", platform, payload, day);
                    if (ProductionChanged != null) ProductionChanged(platform, day);
                    Notify("分镜草稿已保存");
                    if (dialog.IsVisible) dialog.Close();
                }
                catch (Exception error) { message.Text = error.Message; page.IsEnabled = true; }
            };
            lockAllSave.Click += (sender, args) =>
            {
                foreach (var s in scenes) if (hasText(s)) s.Confirmed = true;
                save.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            };
            populate(Math.Max(0, Math.Min(initial, scenes.Count - 1)));
            dialog.ShowDialog();
        }

        private static bool CanDeleteCutScene(List<CutScene> scenes, int index)
        {
            if (scenes.Count <= 2 || index < 0 || index >= scenes.Count) return false;
            return scenes[index].Marker == null || (index + 1 < scenes.Count && scenes[index + 1].Marker == null);
        }

        private static void DeleteCutScene(List<CutScene> scenes, int index)
        {
            if (!CanDeleteCutScene(scenes, index)) throw new InvalidOperationException("当前分镜不能删除。");
            var marker = scenes[index].Marker;
            if (marker != null) scenes[index + 1].Marker = marker;
            scenes.RemoveAt(index);
        }

        private static object CutPayload(CutScene scene)
        {
            var revised = J.Obj("title", scene.Title.Trim(), "text", scene.Text.Trim(), "media", scene.Media, "zoom", scene.Zoom);
            if (scene.Motion.Length > 0) revised["motion"] = scene.Motion;
            if (J.Get(scene.Original, "visual") != null && new[] { "text", "media", "zoom", "motion" }
                .All(key => Equals(J.Get(scene.Original, key), J.Get(revised, key))))
                revised["visual"] = J.Get(scene.Original, "visual");
            return revised;
        }
    }
}
