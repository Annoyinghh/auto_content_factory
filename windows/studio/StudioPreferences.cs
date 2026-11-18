using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;

namespace Acf.Studio
{
    internal sealed class StudioPreferences
    {
        public string WindowSize { get; set; }
        public int InterfaceScale { get; set; }
        public int TextScale { get; set; }
        public int VideoResolution { get; set; }
        public bool RememberWindow { get; set; }
        public bool CloseToTray { get; set; }
        public bool MinimizeToTray { get; set; }
        public bool AutoRefresh { get; set; }
        public double SavedWidth { get; set; }
        public double SavedHeight { get; set; }
        public double SavedLeft { get; set; }
        public double SavedTop { get; set; }
        public bool SavedMaximized { get; set; }

        public StudioPreferences()
        {
            WindowSize = "comfortable";
            InterfaceScale = TextScale = 100;
            VideoResolution = 1080;
            RememberWindow = AutoRefresh = true;
        }

        internal StudioPreferences Copy() { return (StudioPreferences)MemberwiseClone(); }

        internal void Normalize()
        {
            if (!new[] { "compact", "comfortable", "spacious", "maximized" }.Contains(WindowSize)) WindowSize = "comfortable";
            if (!new[] { 90, 100, 110, 125 }.Contains(InterfaceScale)) InterfaceScale = 100;
            if (!new[] { 100, 110, 120 }.Contains(TextScale)) TextScale = 100;
            if (!new[] { 720, 1080, 1440 }.Contains(VideoResolution)) VideoResolution = 1080;
            if (!Finite(SavedWidth) || !Finite(SavedHeight) || !Finite(SavedLeft) || !Finite(SavedTop) ||
                SavedWidth < 960 || SavedWidth > 16000 || SavedHeight < 620 || SavedHeight > 16000)
            { SavedWidth = SavedHeight = SavedLeft = SavedTop = 0; SavedMaximized = false; }
        }

        private static bool Finite(double value) { return !Double.IsNaN(value) && !Double.IsInfinity(value); }

        internal static StudioPreferences Load(string directory, out string error)
        {
            error = null;
            try
            {
                string path = Path.Combine(directory, "studio-settings.json");
                if (!File.Exists(path)) return new StudioPreferences();
                if (new FileInfo(path).Length > 65536) throw new InvalidDataException("设置文件过大。");
                var value = new JavaScriptSerializer().Deserialize<StudioPreferences>(File.ReadAllText(path, Encoding.UTF8));
                if (value == null) throw new InvalidDataException("设置文件为空。");
                value.Normalize();
                return value;
            }
            catch (Exception ex)
            {
                if (!(ex is IOException || ex is UnauthorizedAccessException || ex is ArgumentException || ex is InvalidOperationException)) throw;
                error = "设置未能读取，已使用默认显示。原文件已保留，可在设置页重新保存。";
                return new StudioPreferences();
            }
        }

        internal void Save(string directory)
        {
            Normalize();
            Directory.CreateDirectory(directory);
            string path = Path.Combine(directory, "studio-settings.json");
            string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                File.WriteAllText(temporary, new JavaScriptSerializer().Serialize(this), new UTF8Encoding(false));
                if (File.Exists(path)) File.Replace(temporary, path, null);
                else File.Move(temporary, path);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }
    }

    internal static class StudioTypography
    {
        private static readonly HashSet<double> Sizes = new HashSet<double>();
        private static double factor = 1;

        internal static void Bind(TextBlock label, double size)
        {
            string key = "Studio.Text." + size.ToString(CultureInfo.InvariantCulture);
            if (Sizes.Add(size)) Application.Current.Resources[key] = size * factor;
            label.SetResourceReference(TextBlock.FontSizeProperty, key);
        }

        internal static void Apply(int percent)
        {
            factor = percent / 100.0;
            Application.Current.Resources["Studio.ControlFontSize"] = 13 * factor;
            Application.Current.Resources["Studio.BodyFontSize"] = 14 * factor;
            foreach (double size in Sizes)
                Application.Current.Resources["Studio.Text." + size.ToString(CultureInfo.InvariantCulture)] = size * factor;
        }
    }
}
