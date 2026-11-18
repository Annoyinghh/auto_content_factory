using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace Acf.Studio
{
    internal static class Ui
    {
        internal static Brush Color(string key)
        {
            return (Brush)Application.Current.FindResource(key);
        }

        internal static TextBlock Title(string text)
        {
            var label = Text(text, 30);
            label.FontWeight = FontWeights.SemiBold;
            label.LineHeight = 40;
            return label;
        }

        internal static TextBlock Text(string text, double size = 14, bool muted = false)
        {
            var label = new TextBlock
            {
                Text = text,
                Foreground = Color(muted ? "MutedBrush" : "InkBrush"),
                TextWrapping = TextWrapping.Wrap
            };
            StudioTypography.Bind(label, size);
            return label;
        }

        internal static Button Button(string title, Action click, bool primary = false, string glyph = null)
        {
            var button = new Button { Content = title };
            if (glyph != null)
            {
                var content = new StackPanel { Orientation = Orientation.Horizontal };
                content.Children.Add(new TextBlock
                {
                    Text = glyph, FontFamily = new FontFamily("Segoe MDL2 Assets"), FontSize = 14,
                    Margin = new Thickness(0, 0, 7, 0), VerticalAlignment = VerticalAlignment.Center
                });
                content.Children.Add(new TextBlock { Text = title, VerticalAlignment = VerticalAlignment.Center });
                button.Content = content;
                System.Windows.Automation.AutomationProperties.SetName(button, title);
            }
            if (primary) button.Style = (Style)Application.Current.FindResource("PrimaryButton");
            button.Click += delegate { click(); };
            return button;
        }

        internal static Border Card(UIElement content, string surface = "SurfaceBrush")
        {
            return new Border
            {
                Child = content,
                Background = Color(surface),
                BorderBrush = Color("LineBrush"),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(10),
                Padding = new Thickness(18)
            };
        }

        internal static Border Badge(string label, string tone)
        {
            var text = Text(label, 12);
            text.Foreground = Color(tone + "InkBrush");
            text.FontWeight = FontWeights.SemiBold;
            return new Border
            {
                Child = text, Background = Color(tone + "Brush"),
                CornerRadius = new CornerRadius(6), Padding = new Thickness(7, 3, 7, 3),
                HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Center
            };
        }

        internal static Button Filter(string title, Action click)
        {
            var button = Button(title, click);
            button.Style = (Style)Application.Current.FindResource("FilterButton");
            return button;
        }

        internal static TextBox Search(string hint, double width, string value = "")
        {
            var field = new TextBox
            {
                Text = value, Width = width, ToolTip = hint,
                Margin = new Thickness(0, 0, 6, 5),
                Style = (Style)Application.Current.FindResource("SearchBox")
            };
            System.Windows.Automation.AutomationProperties.SetName(field, hint);
            return field;
        }

        internal static ScrollViewer Scroll(UIElement content)
        {
            return new ScrollViewer
            {
                Content = content,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
            };
        }

        internal static string Platform(string key)
        {
            switch (key)
            {
                case "douyin": return "抖音";
                case "bilibili": return "B站";
                case "xiaohongshu": return "小红书";
                default: return key;
            }
        }
    }
}
