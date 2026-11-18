using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal static class CaseReader
{
    public sealed class Request
    {
        public string topic { get; set; }
        public int limit = 2;
        public bool photo_notes { get; set; }
        public bool interactive { get; set; }
    }
    public sealed class Candidate { public string id { get; set; } public string type { get; set; } public string token { get; set; } }
    public sealed class Search { public bool login { get; set; } public List<Candidate> items { get; set; } }

    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 200000 };
    private static bool Official(string value)
    {
        Uri url;
        return Uri.TryCreate(value, UriKind.Absolute, out url) && url.Scheme == "https" &&
            url.Host == "www.xiaohongshu.com" && url.IsDefaultPort && url.UserInfo.Length == 0;
    }

    [STAThread]
    public static int Main(string[] args)
    {
        if (args.Length != 3 || !File.Exists(args[0]) || new FileInfo(args[0]).Length > 10000) return 2;
        Request request;
        try { request = Json.Deserialize<Request>(File.ReadAllText(args[0], Encoding.UTF8)); }
        catch { return 2; }
        if (request == null || String.IsNullOrWhiteSpace(request.topic) || request.topic.Length > 160 ||
            request.limit < 1 || request.limit > 3) return 2;
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        int code = 1;
        using (var form = new Form { Text = "热采放映室 · 小红书官网参考采集", ClientSize = new Size(1200,800),
            Opacity = request.interactive ? 1 : 0, ShowInTaskbar = request.interactive })
        using (var view = new WebView2 { Dock = DockStyle.Fill })
        using (var timeout = new Timer { Interval = request.interactive ? 600000 : 90000 })
        {
            form.Controls.Add(view);
            timeout.Tick += (s,e) => form.Close();
            form.Shown += async delegate {
                timeout.Start();
                try {
                    var environment = await CoreWebView2Environment.CreateAsync(null, Path.GetFullPath(args[1]));
                    if (form.IsDisposed) return;
                    await view.EnsureCoreWebView2Async(environment);
                    if (form.IsDisposed) return;
                    var browser = view.CoreWebView2;
                    browser.Settings.AreDevToolsEnabled = false;
                    browser.Settings.IsPasswordAutosaveEnabled = false;
                    browser.Settings.IsGeneralAutofillEnabled = false;
                    browser.Settings.AreDefaultScriptDialogsEnabled = false;
                    browser.PermissionRequested += (s,e) => e.State = CoreWebView2PermissionState.Deny;
                    browser.DownloadStarting += (s,e) => e.Cancel = true;
                    browser.NewWindowRequested += (s,e) => e.Handled = true;
                    browser.NavigationStarting += (s,e) => e.Cancel = !Official(e.Uri);
                    browser.Navigate("https://www.xiaohongshu.com/search_result/?keyword=" + Uri.EscapeDataString(request.topic));
                    Search found = null;
                    for (int attempt = 0; attempt < (request.interactive ? 540 : 25); attempt++) {
                        await Task.Delay(1000);
                        if (form.IsDisposed) return;
                        string data = await browser.ExecuteScriptAsync(@"(() => {
                            if (!location.pathname.startsWith('/search_result')) return null;
                            const state=window.__INITIAL_STATE__?.search || {};
                            const feeds=state.feeds?._value || state.feeds || [];
                            return {login:document.body.innerText.includes('登录后查看搜索结果'),
                                items:Array.isArray(feeds)?feeds.filter(n=>n.noteCard && n.id && n.xsecToken).map(n=>({
                                    id:n.id,type:n.noteCard?.type,token:n.xsecToken})).slice(0,24):[]};
                        })()");
                        found = data == "null" ? null : Json.Deserialize<Search>(data);
                        if (found != null && found.login && !request.interactive) {
                            File.WriteAllText(args[2], "{\"works\":[],\"requires_login\":true}", new UTF8Encoding(false));
                            code = 0;
                            form.Close();
                            return;
                        }
                        if (found != null && found.items != null && found.items.Count > 0) break;
                    }
                    var works = new List<object>();
                    if (found != null && found.items != null) {
                        var candidates = found.items.Where(n => n != null && Regex.IsMatch(n.id ?? "", @"^[a-f0-9]{24}$") &&
                            !String.IsNullOrEmpty(n.token) && n.token.Length <= 1800 && n.type == (request.photo_notes ? "normal" : "video"));
                        foreach (var note in candidates.Take(6)) {
                            if (form.IsDisposed) return;
                            string url = "https://www.xiaohongshu.com/explore/" + note.id + "?xsec_token=" +
                                Uri.EscapeDataString(note.token) + "&xsec_source=pc_search";
                            browser.Navigate(url);
                            for (int attempt = 0; attempt < 10; attempt++) {
                                await Task.Delay(750);
                                if (form.IsDisposed) return;
                                string data = await browser.ExecuteScriptAsync(@"(() => {
                                    const id='" + note.id + @"';
                                    if (!location.pathname.endsWith('/'+id)) return null;
                                    const state=window.__INITIAL_STATE__?.note || {};
                                    const map=state.noteDetailMap?._value || state.noteDetailMap || {};
                                    const item=map[id]?.note;
                                    if (!item || !item.title || !item.desc || item.desc.length<20) return null;
                                    return {id,url:location.href,type:item.type,title:item.title,desc:item.desc.slice(0,6000),
                                        time:item.time || null,author:item.user?.nickname || '',stats:item.interactInfo || {},
                                        video_url:document.querySelector('video')?.currentSrc || document.querySelector('video')?.src || '',
                                        images:(item.imageList || []).slice(0,20).map(i=>({url:i.urlDefault || i.url || '',width:i.width,height:i.height}))};
                                })()");
                                if (data == "null") continue;
                                works.Add(Json.DeserializeObject(data));
                                break;
                            }
                            if (works.Count >= request.limit) break;
                        }
                    }
                    File.WriteAllText(args[2], Json.Serialize(new { works = works,
                        requires_login = found != null && found.login }), new UTF8Encoding(false));
                    code = 0;
                } catch (Exception error) {
                    File.WriteAllText(args[2], Json.Serialize(new { works = new object[0], error = error.GetType().Name }), new UTF8Encoding(false));
                }
                if (!form.IsDisposed) form.Close();
            };
            Application.Run(form);
        }
        return code;
    }
}
