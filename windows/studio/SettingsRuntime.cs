using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Media;
using Forms = System.Windows.Forms;

namespace Acf.Studio
{
    internal sealed partial class WorkbenchWindow
    {
        private StudioPreferences preferences;
        private FrameworkElement shell;
        private Forms.NotifyIcon tray;
        private bool explicitExit;
        private bool showedTrayTip;
        private WindowState restoreState = WindowState.Normal;
        private string preferencesError;

        private void InitializePreferences()
        {
            preferences = StudioPreferences.Load(Worker.DataRoot, out preferencesError);
            StudioTypography.Apply(preferences.TextScale);
            SetResourceReference(FontSizeProperty, "Studio.BodyFontSize");
            ApplyDisplayPreferences(true);
            restoreState = WindowState;
            Closing += OnPreferencesClosing;
            StateChanged += delegate
            {
                if (WindowState == WindowState.Minimized && preferences.MinimizeToTray) HideInTray();
                else if (WindowState != WindowState.Minimized) restoreState = WindowState;
            };
            Application.Current.SessionEnding += delegate { explicitExit = true; };
            Closed += delegate { if (tray != null) { tray.Visible = false; tray.Dispose(); tray = null; } };
            if (preferencesError != null) Notify(preferencesError);
        }

        private void ApplyDisplayPreferences(bool resize)
        {
            StudioTypography.Apply(preferences.TextScale);
            Rect area = SystemParameters.WorkArea;
            double scale = Math.Min(preferences.InterfaceScale / 100.0,
                Math.Min((area.Width - 24) / 960, (area.Height - 24) / 620));
            scale = Math.Max(.5, scale);
            shell.LayoutTransform = new ScaleTransform(scale, scale);
            MinWidth = Math.Min(960 * scale, area.Width);
            MinHeight = Math.Min(620 * scale, area.Height);
            if (!resize) return;
            double width = preferences.WindowSize == "compact" ? 1040 : preferences.WindowSize == "spacious" ? 1440 : 1260;
            double height = preferences.WindowSize == "compact" ? 720 : preferences.WindowSize == "spacious" ? 900 : 850;
            bool saved = preferences.RememberWindow && preferences.SavedWidth > 0;
            if (saved) { width = preferences.SavedWidth; height = preferences.SavedHeight; }
            WindowState = WindowState.Normal;
            Width = Math.Max(MinWidth, Math.Min(width, area.Width - 24));
            Height = Math.Max(MinHeight, Math.Min(height, area.Height - 24));
            if (saved)
            {
                WindowStartupLocation = WindowStartupLocation.Manual;
                Left = Math.Max(area.Left, Math.Min(preferences.SavedLeft, area.Right - Width));
                Top = Math.Max(area.Top, Math.Min(preferences.SavedTop, area.Bottom - Height));
            }
            if (preferences.WindowSize == "maximized" || (saved && preferences.SavedMaximized)) WindowState = WindowState.Maximized;
        }

        private bool CommitPreferences(StudioPreferences candidate)
        {
            bool resize = candidate.WindowSize != preferences.WindowSize || candidate.InterfaceScale != preferences.InterfaceScale;
            if (resize) { candidate.SavedWidth = candidate.SavedHeight = 0; candidate.SavedMaximized = false; }
            try { candidate.Save(Worker.DataRoot); }
            catch (Exception ex)
            {
                if (!(ex is IOException || ex is UnauthorizedAccessException)) throw;
                Notify("设置没有保存成功：" + ex.Message);
                return false;
            }
            preferences = candidate;
            preferencesError = null;
            ApplyDisplayPreferences(resize);
            if (preferences.AutoRefresh && !verification) collectionTimer.Start();
            else collectionTimer.Stop();
            Notify("设置已保存。视频清晰度将在下次生成或导出时生效。");
            return true;
        }

        private void SaveWindowPlacement()
        {
            if (!preferences.RememberWindow || preferencesError != null) return;
            Rect bounds = WindowState == WindowState.Normal ? new Rect(Left, Top, ActualWidth, ActualHeight) : RestoreBounds;
            if (bounds.IsEmpty) return;
            preferences.SavedWidth = bounds.Width;
            preferences.SavedHeight = bounds.Height;
            preferences.SavedLeft = bounds.Left;
            preferences.SavedTop = bounds.Top;
            preferences.SavedMaximized = WindowState == WindowState.Maximized || (WindowState == WindowState.Minimized && restoreState == WindowState.Maximized);
            try { preferences.Save(Worker.DataRoot); }
            catch (IOException) { Notify("本次窗口位置未能保存。"); }
            catch (UnauthorizedAccessException) { Notify("本次窗口位置未能保存。"); }
        }

        private void OnPreferencesClosing(object sender, CancelEventArgs args)
        {
            SaveWindowPlacement();
            if (!explicitExit && preferences.CloseToTray)
            {
                args.Cancel = true;
                HideInTray();
            }
        }

        private bool HideInTray()
        {
            try
            {
                if (tray == null)
                {
                    tray = new Forms.NotifyIcon { Text = "热采放映室 · 后台运行",
                        Icon = System.Drawing.Icon.ExtractAssociatedIcon(Process.GetCurrentProcess().MainModule.FileName) };
                    var menu = new Forms.ContextMenuStrip();
                    menu.Items.Add("打开工作台", null, (sender, args) => Dispatcher.Invoke(new Action(RestoreFromTray)));
                    menu.Items.Add("设置", null, (sender, args) => Dispatcher.Invoke(new Action(() => { RestoreFromTray(); Navigate("settings"); })));
                    menu.Items.Add(new Forms.ToolStripSeparator());
                    menu.Items.Add("退出热采放映室", null, (sender, args) => Dispatcher.Invoke(new Action(() => { explicitExit = true; Close(); })));
                    tray.ContextMenuStrip = menu;
                    tray.DoubleClick += (sender, args) => Dispatcher.Invoke(new Action(RestoreFromTray));
                }
                tray.Visible = true;
                Hide();
                if (!showedTrayTip)
                {
                    tray.ShowBalloonTip(4000, "热采放映室仍在运行", "双击托盘图标可返回；右键菜单可以退出。电脑休眠会暂停运行。", Forms.ToolTipIcon.Info);
                    showedTrayTip = true;
                }
                return true;
            }
            catch (Exception ex)
            {
                if (!(ex is Win32Exception || ex is IOException || ex is ArgumentException)) throw;
                Notify("系统托盘暂时不可用，窗口保持打开。");
                Show();
                WindowState = WindowState.Normal;
                return false;
            }
        }

        private void RestoreFromTray()
        {
            Show();
            WindowState = restoreState == WindowState.Maximized ? WindowState.Maximized : WindowState.Normal;
            Activate();
            if (tray != null) tray.Visible = false;
        }

        private void OpenSettingsFolder(string path)
        {
            try { Directory.CreateDirectory(path); Process.Start(new ProcessStartInfo(path) { UseShellExecute = true }); }
            catch (Exception ex)
            {
                if (!(ex is IOException || ex is UnauthorizedAccessException || ex is Win32Exception)) throw;
                Notify("无法打开文件夹：" + ex.Message);
            }
        }
    }
}
