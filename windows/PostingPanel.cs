using System;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal static class PostingPanel
{
    private const string DouyinHome = "https://creator.douyin.com/";
    private const string XiaohongshuHome = "https://creator.xiaohongshu.com/";
    private const string XiaohongshuPublish = XiaohongshuHome + "publish/publish?source=official&target=";

    public sealed class Draft
    {
        public string title { get; set; }
        public string body { get; set; }
        public string[] files { get; set; }
    }

    [DllImport("user32.dll")]
    private static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);

    private sealed class WindowOwner : IWin32Window
    {
        public IntPtr Handle { get; private set; }
        public WindowOwner(IntPtr handle) { Handle = handle; }
    }

    private static bool IsPlatformPage(string address, string domain)
    {
        Uri uri;
        return Uri.TryCreate(address, UriKind.Absolute, out uri) && uri.Scheme == Uri.UriSchemeHttps &&
            uri.IsDefaultPort && uri.UserInfo.Length == 0 &&
            (uri.Host == domain || uri.Host.EndsWith("." + domain, StringComparison.Ordinal));
    }

    [STAThread]
    public static int Main(string[] args)
    {
        long owner;
        var request = Console.ReadLine() ?? "";
        if ((args.Length != 4 && args.Length != 5) || !long.TryParse(args[0], out owner))
            return 2;
        string platform = args.Length == 5 ? args[4] : "douyin";
        if (platform != "douyin" && platform != "xiaohongshu") return 2;
        bool xhs = platform == "xiaohongshu";
        bool loginOnly = request == "login";
        bool note = xhs && (request == XiaohongshuPublish + "image" ||
                            request.StartsWith(XiaohongshuPublish + "image#", StringComparison.Ordinal));
        string home = xhs ? XiaohongshuHome : DouyinHome;
        string upload = xhs ? XiaohongshuPublish + (note ? "image" : "video") : DouyinHome + "creator-micro/content/upload";
        if (!loginOnly && !note && !request.StartsWith(upload, StringComparison.Ordinal))
            return 2;
        string ticket = loginOnly ? "" : request.Substring(upload.Length);
        if (!loginOnly && !(note && ticket.Length == 0) &&
            !Regex.IsMatch(ticket, @"\A#acf-post=[0-9]{1,5}\.[a-f0-9]{64}&panel=1\z"))
            return 2;
        var metadata = Console.ReadLine();
        var draft = String.IsNullOrWhiteSpace(metadata) ? null : new JavaScriptSerializer().Deserialize<Draft>(metadata);

        string profile = Path.GetFullPath(args[1]).TrimEnd(Path.DirectorySeparatorChar).ToUpperInvariant();
        using (var hash = SHA256.Create())
        using (var windowLock = new Mutex(false, @"Local\AutoContentFactory.WebView." +
            BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(profile))).Replace("-", "")))
        {
            try { if (!windowLock.WaitOne(0)) return 3; }
            catch (AbandonedMutexException) { }
            try { return ShowPanel(args, owner, platform, home, upload, loginOnly, ticket, draft); }
            finally { windowLock.ReleaseMutex(); }
        }
    }

    private static int ShowPanel(string[] args, long owner, string platform, string home, string upload,
        bool loginOnly, string ticket, Draft draft)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        int result = 0;
        using (var form = new Form())
        using (var view = new WebView2())
        using (var toolbar = new FlowLayoutPanel())
        using (var banner = new Label())
        {
            string platformName = platform == "douyin" ? "抖音" : "小红书";
            string action = platformName + (loginOnly ? "账号" : "投稿");
            form.Text = "热采放映室 · " + action + " · " + args[3];
            form.StartPosition = FormStartPosition.CenterParent;
            form.ShowInTaskbar = false;
            form.ClientSize = new Size(1120, 760);
            form.MinimumSize = new Size(740, 520);
            banner.Dock = DockStyle.Top;
            banner.Height = 44;
            banner.TextAlign = ContentAlignment.MiddleLeft;
            banner.Padding = new Padding(14, 0, 0, 0);
            banner.BackColor = Color.FromArgb(31, 32, 36);
            banner.ForeColor = Color.FromArgb(244, 235, 222);
            banner.Font = new Font("Microsoft YaHei UI", 10);
            banner.Text = args[3] + "   ·   正在打开" + platformName + "…";
            toolbar.Dock = DockStyle.Top;
            toolbar.AutoSize = true;
            toolbar.Padding = new Padding(9, 3, 9, 3);
            var returnToUpload = AddButton(toolbar, "打开投稿页", () => view.CoreWebView2.Navigate(upload));
            returnToUpload.Enabled = false;
            if (draft != null && !loginOnly)
            {
                AddCopyButton(toolbar, form, "复制标题", draft.title);
                AddCopyButton(toolbar, form, "复制文案", draft.body);
                if (draft.files != null && draft.files.Length > 0)
                {
                    string paths = draft.files.Length == 1 ? draft.files[0] :
                        "\"" + String.Join("\" \"", draft.files) + "\"";
                    AddCopyButton(toolbar, form, "复制素材路径", paths);
                }
            }
            view.Dock = DockStyle.Fill;
            form.Controls.Add(view);
            form.Controls.Add(toolbar);
            form.Controls.Add(banner);
            form.Shown += async delegate
            {
                try
                {
                    var options = new CoreWebView2EnvironmentOptions();
                    options.AreBrowserExtensionsEnabled = true;
                    var environment = await CoreWebView2Environment.CreateAsync(null, args[1], options);
                    if (form.IsDisposed) return;
                    await view.EnsureCoreWebView2Async(environment);
                    if (form.IsDisposed) return;
                    var browser = view.CoreWebView2;
                    string domain = platform + ".com";
                    browser.NavigationStarting += (sender, e) => { e.Cancel = !IsPlatformPage(e.Uri, domain); };
                    browser.NewWindowRequested += (sender, e) =>
                    {
                        e.Handled = true;
                        if (IsPlatformPage(e.Uri, domain)) browser.Navigate(e.Uri);
                    };
                    browser.DownloadStarting += (sender, e) => { e.Cancel = true; };
                    browser.PermissionRequested += (sender, e) => { e.State = CoreWebView2PermissionState.Deny; };
                    browser.Settings.AreDevToolsEnabled = false;
                    returnToUpload.Enabled = true;
                    if (loginOnly)
                    {
                        banner.Text = args[3] + "   ·   请登录对应账号。核对头像和昵称后可关闭窗口。";
                        browser.Navigate(home);
                        return;
                    }
                    banner.Text = args[3] + "   ·   如需登录，请扫码。上传后核对账号、素材和文案，再点发布。";
                    if (ticket.Length > 0)
                    {
                        try
                        {
                            await browser.Profile.AddBrowserExtensionAsync(args[2]);
                            if (form.IsDisposed) return;
                            var route = new Uri(upload);
                            await browser.AddScriptToExecuteOnDocumentCreatedAsync(
                                "if (window === window.top && location.origin === '" + route.GetLeftPart(UriPartial.Authority) + "' && " +
                                "location.pathname.replace(/\\/$/, '') === '" + route.AbsolutePath + "' && !location.hash" +
                                (platform == "xiaohongshu" ? " && new URLSearchParams(location.search).get('target') === '" +
                                    (route.Query.EndsWith("target=image", StringComparison.Ordinal) ? "image" : "video") + "'" : "") + ") " +
                                "history.replaceState(history.state, '', location.pathname + location.search + '" + ticket + "');"
                            );
                        }
                        catch (Exception)
                        {
                            if (form.IsDisposed) return;
                            if (draft == null) throw;
                            banner.Text = args[3] + "   ·   自动填稿不可用。可复制素材路径和文案，在下方页面投稿。";
                        }
                    }
                    else
                    {
                        banner.Text = args[3] + "   ·   选择上传图片后，将素材路径粘贴到文件名框。核对笔记后自行发布。";
                    }
                    if (!form.IsDisposed) browser.Navigate(upload);
                }
                catch (Exception error)
                {
                    if (form.IsDisposed) return;
                    result = 1;
                    MessageBox.Show(form, error.Message, "无法打开" + platformName + "面板",
                                    MessageBoxButtons.OK, MessageBoxIcon.Error);
                    form.Close();
                }
            };
            var ownerWindow = GetAncestor(new IntPtr(owner), 2);
            if (ownerWindow == IntPtr.Zero) return 2;
            form.ShowDialog(new WindowOwner(ownerWindow));
        }
        return result;
    }

    private static Button AddButton(FlowLayoutPanel toolbar, string text, Action action)
    {
        var button = new Button { Text = text, AutoSize = true };
        button.Click += delegate { action(); };
        toolbar.Controls.Add(button);
        return button;
    }

    private static void AddCopyButton(FlowLayoutPanel toolbar, Form form, string text, string value)
    {
        if (String.IsNullOrWhiteSpace(value)) return;
        AddButton(toolbar, text, () =>
        {
            try { Clipboard.SetText(value); }
            catch (ExternalException)
            {
                MessageBox.Show(form, "剪贴板正被占用，请再试一次。", text, MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
        });
    }
}
