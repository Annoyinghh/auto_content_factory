using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Effects;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace Acf.Studio
{
    internal sealed class ShelfWork
    {
        internal readonly string Topic, Id, Title, Platform, Kind, Day;
        internal readonly IDictionary<string, object> Data;

        internal ShelfWork(string topic, IDictionary<string, object> data)
        {
            Topic = topic;
            Data = data;
            Id = J.Text(data, "id");
            Title = J.Text(data, "title");
            Platform = J.Text(data, "platform");
            Kind = J.Text(data, "kind");
            Day = J.Text(data, "day");
        }

        internal bool VideoShelf { get { return Kind == "video" || Kind == "preview" || Kind == "production" || Platform == "bilibili"; } }
        internal string Format
        {
            get
            {
                bool historical = J.Text(Data, "state").StartsWith("历史", StringComparison.Ordinal);
                return Kind == "preview" ? historical ? "历史试剪" : "当前试剪" :
                    Kind == "video" && historical ? "历史成片" : WorkbenchWindow.WorkFormat(Kind);
            }
        }
        internal string Label { get { return Ui.Platform(Platform) + " · " + Format; } }
    }

    internal sealed class WorkShelf : Grid
    {
        private const double Gap = 28, EdgeWidth = 45;
        private static readonly Brush Wall = Gradient("#FBF5E8", "#F0E3CD");
        private static readonly Brush Ledge = Gradient("#D4B68B", "#A38463");
        private static readonly Brush CoverTint = Gradient("#75303933", "#E4242C28");
        private static BitmapSource illustration;
        private readonly Canvas books = new Canvas();
        private readonly Grid page;
        private readonly Button recycle;
        private readonly Canvas dragLayer = new Canvas { IsHitTestVisible = false };
        private readonly Border leftEdge, rightEdge;
        private readonly TextBlock empty = Ui.Text("正在读取本机作品…", 14, true);
        private readonly DispatcherTimer glide;
        private readonly Stopwatch clock = new Stopwatch();
        private readonly Dictionary<int, Button> cards = new Dictionary<int, Button>();
        private IReadOnlyList<ShelfWork> items = new ShelfWork[0];
        private double position, speed;
        private int current = -1, direction;
        private long lastFrame;
        private Point press;
        private ShelfWork grabbed;
        private Button grabbedButton;
        private FrameworkElement ghost;
        private int dropState;

        internal event Action<ShelfWork> CurrentChanged;
        internal event Action BrowseEnded;
        internal event Action<ShelfWork> RecycleRequested;
        internal event Action<int> DropStateChanged;
        internal ShelfWork Current { get { return current < 0 ? null : items[current]; } }
        internal bool IsBrowsing { get { return direction != 0; } }
        private double CoverHeight { get { return Math.Min(226, Height - 64); } }
        private double BookWidth { get { return Math.Max(100, Math.Min(142, CoverHeight * .76)); } }
        private double Step { get { return BookWidth + Gap; } }

        internal WorkShelf(Grid page, Button recycle)
        {
            this.page = page;
            this.recycle = recycle;
            Height = 258;
            Background = Wall;
            ClipToBounds = true;
            AutomationProperties.SetName(this, "作品书架，鼠标靠边自动浏览");
            Children.Add(new Border { Height = 5, VerticalAlignment = VerticalAlignment.Bottom,
                Margin = new Thickness(0, 0, 0, 25), Background = Ledge, IsHitTestVisible = false });
            Children.Add(books);
            leftEdge = Edge(HorizontalAlignment.Left);
            rightEdge = Edge(HorizontalAlignment.Right);
            Children.Add(leftEdge);
            Children.Add(rightEdge);
            empty.HorizontalAlignment = HorizontalAlignment.Center;
            empty.VerticalAlignment = VerticalAlignment.Center;
            empty.IsHitTestVisible = false;
            Children.Add(empty);

            Panel.SetZIndex(dragLayer, 20);
            page.Children.Add(dragLayer);
            glide = new DispatcherTimer(DispatcherPriority.Render, Dispatcher) { Interval = TimeSpan.FromMilliseconds(16) };
            glide.Tick += Glide;
            PreviewMouseMove += delegate { CheckEdge(); };
            MouseLeave += delegate { StopGlide(); };
            SizeChanged += delegate
            {
                if (current >= 0 && !IsBrowsing) position = current;
                Draw();
            };
            page.PreviewMouseMove += DragMove;
            page.PreviewMouseLeftButtonUp += ReleaseBook;
            page.LostMouseCapture += (sender, args) => { if (args.OriginalSource == page) EndDrag(); };
            page.PreviewKeyDown += (sender, args) =>
            {
                if (args.Key != Key.Escape || ghost == null) return;
                EndDrag();
                args.Handled = true;
            };
            Unloaded += delegate { StopGlide(); EndDrag(); };
        }

        internal void ShowWorks(IReadOnlyList<ShelfWork> works, string keepId, string emptyMessage)
        {
            StopGlide();
            EndDrag();
            books.Children.Clear();
            cards.Clear();
            items = works;
            current = -1;
            int next = Enumerable.Range(0, items.Count).FirstOrDefault(i => items[i].Id == keepId);
            position = next;
            empty.Text = emptyMessage;
            empty.Visibility = items.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
            Select(items.Count == 0 ? -1 : next, true);
            Draw();
        }

        private double FirstPosition
        {
            get { return Math.Min((items.Count - 1) / 2.0, Math.Max(0, (ActualWidth - BookWidth - 48) / (2 * Step))); }
        }

        private double LastPosition { get { return items.Count - 1 - FirstPosition; } }
        private bool Circular { get { return items.Count > 1 && items.Count >= Math.Ceiling((ActualWidth + BookWidth) / Step); } }
        private static int Wrap(int index, int count) { return (index % count + count) % count; }
        private static int Center(double value) { return (int)Math.Floor(value + .5); }

        private bool CanMove(int side)
        {
            return items.Count > 1 && (Circular || (side < 0 ? position > FirstPosition + .001 : position < LastPosition - .001));
        }

        private void Draw()
        {
            if (items.Count == 0 || ActualWidth <= 0)
            {
                PaintEdges();
                return;
            }
            if (!Circular) position = Math.Min(LastPosition, Math.Max(FirstPosition, position));
            var radius = (int)Math.Ceiling((ActualWidth + BookWidth) / (2 * Step)) + 1;
            int start, end;
            if (Circular)
            {
                int count = Math.Min(items.Count, 2 * radius + 1);
                start = Center(position - (count - 1) / 2.0);
                end = start + count - 1;
            }
            else
            {
                start = Math.Max(0, Center(position) - radius);
                end = Math.Min(items.Count - 1, Center(position) + radius);
            }

            foreach (int index in cards.Keys.Where(i => i < start || i > end).ToArray())
            {
                books.Children.Remove(cards[index]);
                cards.Remove(index);
            }
            for (int index = start; index <= end; index++)
            {
                int itemIndex = Wrap(index, items.Count);
                Button card;
                if (!cards.TryGetValue(index, out card))
                {
                    card = MakeCard(items[itemIndex], itemIndex);
                    cards.Add(index, card);
                    books.Children.Add(card);
                }
                double left = (ActualWidth - BookWidth) / 2 + (index - position) * Step;
                bool visible = left + BookWidth > 1 && left < ActualWidth - 1;
                card.IsHitTestVisible = visible;
                card.IsTabStop = visible && itemIndex == current;
                var cardPanel = (StackPanel)card.Content;
                var cover = (FrameworkElement)cardPanel.Children[0];
                var label = (TextBlock)cardPanel.Children[1];
                ((Border)cover.Tag).Visibility = itemIndex == current ? Visibility.Visible : Visibility.Collapsed;
                label.Foreground = Ui.Color(itemIndex == current ? "AccentBrush" : "InkBrush");
                label.FontWeight = itemIndex == current ? FontWeights.SemiBold : FontWeights.Normal;
                cover.Height = CoverHeight;
                cover.Width = card.Width = BookWidth;
                Canvas.SetLeft(card, left);
                Canvas.SetBottom(card, 10);
            }
            PaintEdges();
        }

        private void Select(int index, bool notify = false)
        {
            if (index == current && !notify) return;
            current = index;
            var changed = CurrentChanged;
            if (changed != null) changed(Current);
        }

        private void CheckEdge()
        {
            if (grabbed != null || Mouse.LeftButton == MouseButtonState.Pressed)
            {
                StopGlide();
                return;
            }
            var pointer = Mouse.GetPosition(this);
            int side = pointer.X < EdgeWidth ? -1 : pointer.X > ActualWidth - EdgeWidth ? 1 : 0;
            if (side == 0 || !CanMove(side))
            {
                bool wasMoving = direction != 0;
                StopGlide();
                if (wasMoving) LiftPointerCard(true);
                return;
            }
            double depth = side < 0 ? 1 - pointer.X / EdgeWidth : 1 - (ActualWidth - pointer.X) / EdgeWidth;
            speed = .82 + Math.Min(1, depth) * 2.05;
            if (side == direction) return;
            direction = side;
            lastFrame = 0;
            clock.Restart();
            LiftPointerCard(false);
            glide.Start();
            PaintEdges();
        }

        private void Glide(object sender, EventArgs args)
        {
            var owner = Window.GetWindow(this);
            if (!IsMouseOver || owner == null || !owner.IsActive || !CanMove(direction))
            {
                StopGlide();
                return;
            }
            long elapsed = clock.ElapsedMilliseconds;
            long dt = Math.Min(40, elapsed - lastFrame);
            lastFrame = elapsed;
            if (elapsed < 140) return;
            double ease = Math.Min(1, (elapsed - 140) / 270.0);
            position += direction * speed * ease * dt / 1000;
            if (!Circular) position = Math.Min(LastPosition, Math.Max(FirstPosition, position));
            Select(Wrap(Center(position), items.Count));
            Draw();
        }

        private void StopGlide()
        {
            if (direction == 0) return;
            glide.Stop();
            direction = 0;
            PaintEdges();
            var stopped = BrowseEnded;
            if (stopped != null) stopped();
        }

        private void PaintEdges()
        {
            leftEdge.Opacity = !CanMove(-1) || ghost != null ? 0 : direction == -1 ? 1 : .55;
            rightEdge.Opacity = !CanMove(1) || ghost != null ? 0 : direction == 1 ? 1 : .55;
            leftEdge.Background = direction == -1 ? Ui.Color("AccentBrush") : Ledge;
            rightEdge.Background = direction == 1 ? Ui.Color("AccentBrush") : Ledge;
        }

        private Button MakeCard(ShelfWork work, int index)
        {
            var cover = MakeCover(work);
            var panel = new StackPanel();
            panel.Children.Add(cover);
            var label = Ui.Text(work.Label, 12);
            label.HorizontalAlignment = HorizontalAlignment.Center;
            label.Margin = new Thickness(0, 8, 0, 0);
            panel.Children.Add(label);
            var frame = new FrameworkElementFactory(typeof(Border));
            frame.SetValue(Border.BackgroundProperty, Brushes.Transparent);
            frame.AppendChild(new FrameworkElementFactory(typeof(ContentPresenter)));
            var button = new Button { Width = BookWidth, Content = panel, Cursor = Cursors.Hand,
                Template = new ControlTemplate(typeof(Button)) { VisualTree = frame } };
            AutomationProperties.SetName(button, work.Title + "，" + work.Label);
            button.ToolTip = work.Label + " · 点击查看此版本";
            AutomationProperties.SetHelpText(button, "点击查看此版本；带边框的是当前预览，也可按 Delete 移入回收站");
            button.Click += delegate
            {
                Select(index, true);
                Draw();
            };
            button.MouseEnter += delegate { if (direction == 0 && grabbed == null) Lift(button, cover, true); };
            button.MouseLeave += delegate { Lift(button, cover, false); };
            button.IsKeyboardFocusedChanged += delegate { Lift(button, cover, button.IsKeyboardFocused); };
            button.PreviewMouseLeftButtonDown += (sender, args) =>
            {
                StopGlide();
                press = args.GetPosition(page);
                grabbed = work;
                grabbedButton = button;
            };
            button.PreviewKeyDown += (sender, args) =>
            {
                if (args.Key == Key.Delete && !args.IsRepeat)
                {
                    var remove = RecycleRequested;
                    if (remove != null) remove(work);
                }
                else if (args.Key == Key.Left || args.Key == Key.Right)
                {
                    int requested = index + (args.Key == Key.Left ? -1 : 1);
                    int next = Circular ? Wrap(requested, items.Count) : Math.Max(0, Math.Min(items.Count - 1, requested));
                    position = Circular ? cards.First(pair => pair.Value == button).Key + requested - index : next;
                    Select(next);
                    Draw();
                    var focus = cards.FirstOrDefault(pair => Wrap(pair.Key, items.Count) == next).Value;
                    if (focus != null) focus.Focus();
                }
                else return;
                args.Handled = true;
            };
            return button;
        }

        private static void Lift(Button button, FrameworkElement cover, bool lifted)
        {
            Panel.SetZIndex(button, lifted ? 2 : 0);
            var transforms = (TransformGroup)cover.RenderTransform;
            var scale = (ScaleTransform)transforms.Children[0];
            var offset = (TranslateTransform)transforms.Children[1];
            Animate(scale, ScaleTransform.ScaleXProperty, lifted ? 1.045 : 1);
            Animate(scale, ScaleTransform.ScaleYProperty, lifted ? 1.045 : 1);
            Animate(offset, TranslateTransform.XProperty, lifted ? 6 : 0);
            Animate(offset, TranslateTransform.YProperty, lifted ? -22 : 0);
        }

        private void LiftPointerCard(bool lifted)
        {
            var card = cards.Values.FirstOrDefault(button => button.IsMouseOver);
            if (card != null) Lift(card, CardCover(card), lifted);
        }

        private static FrameworkElement CardCover(Button card)
        {
            return (FrameworkElement)((StackPanel)card.Content).Children[0];
        }

        private static void Animate(Animatable element, DependencyProperty property, double value)
        {
            if (!SystemParameters.ClientAreaAnimation)
            {
                element.SetValue(property, value);
                return;
            }
            element.BeginAnimation(property, new DoubleAnimation(value, TimeSpan.FromMilliseconds(160))
            {
                EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut }
            });
        }

        private void DragMove(object sender, MouseEventArgs args)
        {
            if (grabbed == null) return;
            if (args.LeftButton != MouseButtonState.Pressed) { EndDrag(); return; }
            var point = args.GetPosition(page);
            if (ghost == null)
            {
                if (Math.Abs(point.X - press.X) < SystemParameters.MinimumHorizontalDragDistance &&
                    Math.Abs(point.Y - press.Y) < SystemParameters.MinimumVerticalDragDistance) return;
                if (!Mouse.Capture(page)) { EndDrag(); return; }
                StopGlide();
                ghost = MakeCover(grabbed);
                ghost.RenderTransform = new ScaleTransform(.69, .69);
                ghost.RenderTransformOrigin = new Point(0, 0);
                dragLayer.Children.Add(ghost);
                grabbedButton.Opacity = .35;
                page.Cursor = Cursors.SizeAll;
                PaintEdges();
            }
            bool inBin = BinBounds().Contains(point);
            SetDropState(inBin ? 2 : 1);
            double x = Math.Min(point.X + 12, page.ActualWidth - BookWidth * .69 - 10);
            double y = inBin ? BinBounds().Bottom + 10 : point.Y + 14;
            if (y + CoverHeight * .69 > page.ActualHeight) y = point.Y - CoverHeight * .69 - 10;
            Canvas.SetLeft(ghost, Math.Max(0, x));
            Canvas.SetTop(ghost, Math.Max(0, y));
            args.Handled = true;
        }

        private Rect BinBounds()
        {
            return new Rect(recycle.TranslatePoint(new Point(), page), recycle.RenderSize);
        }

        private void ReleaseBook(object sender, MouseButtonEventArgs args)
        {
            if (grabbed == null) return;
            var work = grabbed;
            bool wasDragging = ghost != null;
            bool remove = wasDragging && BinBounds().Contains(args.GetPosition(page));
            EndDrag();
            if (wasDragging) args.Handled = true;
            var action = RecycleRequested;
            if (remove && action != null) action(work);
        }

        private void SetDropState(int value)
        {
            if (dropState == value) return;
            dropState = value;
            var changed = DropStateChanged;
            if (changed != null) changed(value);
        }

        private void EndDrag()
        {
            grabbed = null;
            if (ghost == null) { grabbedButton = null; return; }
            dragLayer.Children.Clear();
            ghost = null;
            grabbedButton.Opacity = 1;
            grabbedButton = null;
            page.ClearValue(CursorProperty);
            if (Mouse.Captured == page) Mouse.Capture(null);
            SetDropState(0);
            PaintEdges();
        }

        private Grid MakeCover(ShelfWork work)
        {
            var cover = new Grid { Width = BookWidth, Height = CoverHeight,
                RenderTransformOrigin = new Point(.1, .96),
                RenderTransform = new TransformGroup
                {
                    Children = new TransformCollection { new ScaleTransform(), new TranslateTransform() }
                }
            };
            var paper = new Border { Margin = new Thickness(0, 4, 0, 4), CornerRadius = new CornerRadius(5),
                Background = Gradient("#F5EAD4", "#BFB19A"),
                Effect = new DropShadowEffect { Color = Color.FromRgb(49, 31, 26), BlurRadius = 8, ShadowDepth = 3, Opacity = .20 } };
            cover.Children.Add(paper);
            var painting = WorkbenchWindow.ReadWorkImage(J.Text(work.Data, "preview"), 360);
            var image = painting == null ? IllustratedCover(work) : new ImageBrush(painting) { Stretch = Stretch.UniformToFill };
            var artwork = new Border { Margin = new Thickness(0, 0, 6, 0), Background = image, CornerRadius = new CornerRadius(4, 6, 6, 4) };
            cover.Children.Add(artwork);
            if (painting != null)
                cover.Children.Add(new Border { Margin = artwork.Margin, CornerRadius = artwork.CornerRadius, Background = CoverTint });
            cover.Children.Add(new Border { Width = 5, HorizontalAlignment = HorizontalAlignment.Left,
                CornerRadius = new CornerRadius(3, 0, 0, 3), Background = BrushOf("#44251426") });
            var typography = new Grid { Margin = new Thickness(12, 10, 15, 11) };
            var format = Print(work.Format, 11, painting != null);
            format.VerticalAlignment = VerticalAlignment.Top;
            typography.Children.Add(format);
            var title = Print(work.Title, 16, painting != null);
            title.FontWeight = FontWeights.SemiBold;
            title.TextTrimming = TextTrimming.CharacterEllipsis;
            title.MaxHeight = 71;
            title.VerticalAlignment = VerticalAlignment.Center;
            typography.Children.Add(title);
            var edition = Print(work.Day, 11, painting != null);
            edition.VerticalAlignment = VerticalAlignment.Bottom;
            typography.Children.Add(edition);
            cover.Children.Add(typography);
            var selected = new Border { Margin = artwork.Margin, CornerRadius = artwork.CornerRadius,
                BorderBrush = Ui.Color("AccentBrush"), BorderThickness = new Thickness(2),
                Visibility = Visibility.Collapsed, IsHitTestVisible = false };
            cover.Children.Add(selected);
            cover.Tag = selected;
            return cover;
        }

        private static BitmapSource Illustration()
        {
            if (illustration == null)
            {
                using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("Studio.WorksIllustration.jpg"))
                    illustration = BitmapFrame.Create(stream, BitmapCreateOptions.None, BitmapCacheOption.OnLoad);
                illustration.Freeze();
            }
            return illustration;
        }

        internal static Brush IllustratedCover(ShelfWork work)
        {
            return Ui.Color(work.Platform == "xiaohongshu" ? "BlushBrush" :
                work.Platform == "bilibili" ? "SkyBrush" : "SageBrush");
        }

        private static TextBlock Print(string text, double size, bool overImage)
        {
            var label = Ui.Text(text, size);
            if (overImage) label.Foreground = BrushOf("#FFF4E7");
            return label;
        }

        private static Border Edge(HorizontalAlignment alignment)
        {
            return new Border { Width = 3, Height = 137, HorizontalAlignment = alignment, VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(6, 0, 6, 21), CornerRadius = new CornerRadius(2), IsHitTestVisible = false };
        }

        private static Brush BrushOf(string value)
        {
            var brush = new SolidColorBrush((Color)ColorConverter.ConvertFromString(value));
            brush.Freeze();
            return brush;
        }

        private static Brush Gradient(string top, string bottom)
        {
            var brush = new LinearGradientBrush((Color)ColorConverter.ConvertFromString(top), (Color)ColorConverter.ConvertFromString(bottom), 90);
            brush.Freeze();
            return brush;
        }
    }
}
