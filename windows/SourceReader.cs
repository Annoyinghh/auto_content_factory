using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

// Reads a public article through the same browser engine already bundled for the workbench.
// No cookies are exported, no extension is installed, and nothing is posted to the page.
internal static class SourceReader
{
    private static bool articleCapture;
    private static bool IsCaptureUrl(string value)
    {
        return articleCapture ? value != null && Regex.IsMatch(value,
            @"^https://www\.ithome\.com/\d+/\d+/\d+\.htm$") : IsOfficialUrl(value);
    }

    private static bool IsCaptureResource(string value)
    {
        if (!articleCapture) return IsOfficialUrl(value);
        Uri resource;
        return Uri.TryCreate(value, UriKind.Absolute, out resource) && resource.Scheme == "https" &&
            resource.UserInfo.Length == 0 && resource.IsDefaultPort &&
            (resource.Host == "www.ithome.com" || resource.Host == "img.ithome.com");
    }
    private static bool IsOfficialUrl(string value)
    {
        Uri uri;
        // Match the authority as written, so even an explicit default port is rejected.
        return value != null && !Regex.IsMatch(value, @"[\s\\\x00-\x1f\x7f]") &&
            Regex.IsMatch(value, @"^https://(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*gov\.cn(?:[/?#]|$)", RegexOptions.IgnoreCase) &&
            Uri.TryCreate(value, UriKind.Absolute, out uri) && uri.Host.Length <= 253 && uri.UserInfo.Length == 0;
    }

    private static async Task<string> CaptureNotice(CoreWebView2 browser, string filename)
    {
        // Read layout coordinates only. Never replace the document, change its typography,
        // hide overlays, or reconstruct a notice from extracted text.
        string data = await browser.ExecuteScriptAsync(@"(() => {
            const visible = node => node && node.getBoundingClientRect().width > 0 &&
                node.getBoundingClientRect().height > 0 && getComputedStyle(node).visibility !== 'hidden';
            const text = node => (node?.innerText || '').trim();
            const article = [...document.querySelectorAll('#UCAP-CONTENT, .TRS_Editor, .TRS_UEDITOR, #zoom, #zoomcon,' +
                '.pages_content, .article-content, .article_content, .content-article, #articleContent, #paragraph, article')]
                .find(node => visible(node) && text(node).length >= 120);
            if (!article) return null;
            const heading = [...document.querySelectorAll('h1, .pages-title, .article-title, .article_title, .content-title')]
                .find(node => visible(node) && text(node).length >= 4 && text(node).length <= 250);
            const opening = [...article.querySelectorAll('p')].filter(visible).slice(0,3)
                .filter(node => node.querySelector('strong,b') && text(node).length <= 100);
            const titleNodes = heading ? [heading] : opening;
            if (!titleNodes.length) return null;
            const title = titleNodes.map(text).join('');
            const top = Math.min(...titleNodes.map(node => node.getBoundingClientRect().top + scrollY));
            const metadata = [...document.querySelectorAll('.bd1, .pages-date, .article-time, .article-info,' +
                '.article_info, .pubtime, #pubtime_baidu, .source, .info, .date')].filter(node => {
                    const box = node.getBoundingClientRect();
                    const y = box.top + scrollY;
                    return visible(node) && y >= top - 550 && y <= top + 250 && text(node).length < 1800;
                });
            const detail = metadata.map(text).join('\n');
            const datePattern = /\d{4}\s*(?:年|[-/.])\s*\d{1,2}\s*(?:月|[-/.])\s*\d{1,2}\s*日?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?/;
            const publicationLine = (detail.match(/(?:发布日期|发布时间|发布于)\s*[:：]?\s*([^\n]{6,80})/) || ['',''])[1];
            const published = ((publicationLine || detail || text(article)).match(datePattern) || [''])[0].trim();
            if (!published) return null;
            const source = (detail.match(/(?:来源|发文机关)\s*[:：]?\s*([^\n\t]{2,80})/) || ['',''])[1].trim();
            const boxes = [article, ...titleNodes, ...metadata].map(node => {
                const box = node.getBoundingClientRect();
                return {x:box.left+scrollX,y:box.top+scrollY,right:box.right+scrollX,bottom:box.bottom+scrollY};
            });
            const x = Math.max(0, Math.floor(Math.min(...boxes.map(box=>box.x))-18));
            const y = Math.max(0, Math.floor(Math.min(...boxes.map(box=>box.y))-18));
            const width = Math.min(1600, document.documentElement.scrollWidth-x,
                Math.ceil(Math.max(...boxes.map(box=>box.right))-x+18));
            const height = Math.min(1800, document.documentElement.scrollHeight-y,
                Math.ceil(Math.max(...boxes.map(box=>box.bottom))-y+18));
            const firstParagraph = [...article.querySelectorAll('p')].find(node=>visible(node) && text(node).length>=80);
            const dateVisible = [article, ...metadata].some(root =>
                [root, ...root.querySelectorAll('p,span,time,td,div')].some(node => {
                    const box = node.getBoundingClientRect();
                    return visible(node) && text(node).includes(published) && box.top+scrollY>=y &&
                        box.bottom+scrollY<=y+height;
                }));
            if (width < 320 || height < 200 || !firstParagraph ||
                firstParagraph.getBoundingClientRect().bottom + scrollY > y + height || !dateVisible) return null;
            return {url:location.href, title, summary:text(article).slice(0,7000), published, source,
                crop:{x,y,width,height,scale:1}};
        })()");
        if (data == "null" || String.IsNullOrWhiteSpace(data)) return null;
        var serializer = new JavaScriptSerializer();
        var result = serializer.Deserialize<Dictionary<string, object>>(data);
        if (!IsCaptureUrl(Convert.ToString(result["url"]))) throw new InvalidOperationException("Unexpected capture URL");
        var parameters = new Dictionary<string, object> {
            {"format", "png"}, {"fromSurface", true}, {"captureBeyondViewport", true}, {"clip", result["crop"]}
        };
        string screenshot = await browser.CallDevToolsProtocolMethodAsync("Page.captureScreenshot", serializer.Serialize(parameters));
        var pixels = serializer.Deserialize<Dictionary<string, object>>(screenshot);
        byte[] png = Convert.FromBase64String(Convert.ToString(pixels["data"]));
        if (png.Length == 0 || png.Length > 16000000) throw new InvalidOperationException("Screenshot size out of bounds");
        File.WriteAllBytes(Path.GetFullPath(filename), png);
        result["capture_method"] = "webview2-original-page";
        result["capture_url"] = new Uri(Convert.ToString(result["url"])).GetLeftPart(UriPartial.Path) + new Uri(Convert.ToString(result["url"])).Query;
        result["captured_at"] = DateTime.UtcNow.ToString("o");
        return serializer.Serialize(result);
    }

    private static void WriteResult(string value)
    {
        using (var writer = new StreamWriter(Console.OpenStandardOutput(), new System.Text.UTF8Encoding(false)))
        {
            writer.WriteLine(value);
        }
    }

    [STAThread]
    public static int Main(string[] args)
    {
        Uri target;
        articleCapture = args.Length == 4 && args[2] == "--capture-article";
        bool capture = args.Length == 4 && (args[2] == "--capture" || articleCapture);
        if ((!capture && args.Length != 2) || !Uri.TryCreate(args[0], UriKind.Absolute, out target) ||
            target.Scheme != "https" || !target.IsDefaultPort || target.UserInfo.Length != 0 ||
            (capture ? !IsCaptureUrl(args[0]) : (target.Host != "www.sohu.com" && target.Host != "m.sohu.com"))) return 2;
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        int code = 1;
        using (var form = new Form())
        using (var view = new WebView2())
        using (var timeout = new Timer { Interval = 35000 })
        {
            form.Text = "热采放映室 · 读取同题原文";
            form.ShowInTaskbar = false;
            form.ClientSize = capture ? new Size(1280, 1800) : new Size(1100, 800);
            form.Opacity = 0;
            view.Dock = DockStyle.Fill;
            form.Controls.Add(view);
            timeout.Tick += (sender, e) => { if (capture) Console.Error.WriteLine("Official notice navigation/capture timed out"); form.Close(); };
            form.Shown += async delegate
            {
                timeout.Start();
                try
                {
                    var environment = await CoreWebView2Environment.CreateAsync(null, Path.GetFullPath(args[1]));
                    if (form.IsDisposed) return;
                    await view.EnsureCoreWebView2Async(environment);
                    if (form.IsDisposed) return;
                    var browser = view.CoreWebView2;
                    var playerFiles = new Dictionary<string, long>();
                    browser.WebResourceResponseReceived += (sender, e) => {
                        Uri resource;
                        if (!Uri.TryCreate(e.Request.Uri, UriKind.Absolute, out resource) || resource.Scheme != "https" ||
                            !resource.IsDefaultPort || resource.UserInfo.Length != 0 ||
                            !Regex.IsMatch(resource.Host, @"^\d+-\d+\.vod\.tv\.itc\.cn$") ||
                            !resource.AbsolutePath.EndsWith(".mp4", StringComparison.OrdinalIgnoreCase)) return;
                        var headers = e.Response.Headers;
                        string size = headers.Contains("Content-Range") ? headers.GetHeader("Content-Range") : "";
                        if (size.Contains("/")) size = size.Substring(size.LastIndexOf('/') + 1);
                        else size = headers.Contains("Content-Length") ? headers.GetHeader("Content-Length") : "";
                        long bytes;
                        if ((e.Response.StatusCode == 200 || e.Response.StatusCode == 206) && long.TryParse(size, out bytes))
                            playerFiles[resource.AbsoluteUri] = bytes;
                    };
                    browser.Settings.AreDevToolsEnabled = false;
                    browser.Settings.IsPasswordAutosaveEnabled = false;
                    browser.Settings.IsGeneralAutofillEnabled = false;
                    browser.PermissionRequested += (sender, e) => e.State = CoreWebView2PermissionState.Deny;
                    browser.DownloadStarting += (sender, e) => e.Cancel = true;
                    browser.NewWindowRequested += (sender, e) => e.Handled = true;
                    browser.ScriptDialogOpening += (sender, e) => { };
                    browser.Settings.AreDefaultScriptDialogsEnabled = false;
                    if (capture)
                    {
                        browser.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
                        browser.WebResourceRequested += (sender, e) => {
                            if (!IsCaptureResource(e.Request.Uri))
                                e.Response = environment.CreateWebResourceResponse(new MemoryStream(), 403, "Blocked nonofficial resource", "Content-Type: text/plain");
                        };
                        browser.FrameNavigationStarting += (sender, e) => { e.Cancel = !IsCaptureUrl(e.Uri); };
                    }
                    browser.NavigationStarting += (sender, e) => {
                        Uri next;
                        e.Cancel = !Uri.TryCreate(e.Uri, UriKind.Absolute, out next) || next.Scheme != "https" ||
                            !next.IsDefaultPort || next.UserInfo.Length != 0 || next.Host != target.Host ||
                            (capture && !IsCaptureUrl(e.Uri));
                    };
                    bool reading = false;
                    browser.NavigationCompleted += async (sender, e) => {
                        if (capture && !e.IsSuccess && !form.IsDisposed)
                        {
                            Console.Error.WriteLine("Official notice navigation failed: " + e.WebErrorStatus);
                            form.Close();
                            return;
                        }
                        if (reading || !e.IsSuccess || form.IsDisposed) return;
                        reading = true;
                        try
                        {
                            for (int attempt = 0; attempt < 4 && !form.IsDisposed; attempt++)
                            {
                                await Task.Delay(1000);
                                if (form.IsDisposed) return;
                                if (capture)
                                {
                                    string notice = await CaptureNotice(browser, args[3]);
                                    if (notice == null) continue;
                                    WriteResult(notice);
                                    code = 0;
                                    form.Close();
                                    return;
                                }
                                string data = await browser.ExecuteScriptAsync(@"(() => {
                                    const playerFiles = " + new JavaScriptSerializer().Serialize(playerFiles) + @";
                                    const article = document.querySelector('#mp-editor, .mp-editor, article');
                                    if (!article || article.innerText.trim().length < 300) return null;
                                    const main = article.closest('#main-content, .main-content') || article.parentElement;
                                    const meta = name => document.querySelector('meta[property=""' + name + '""]')?.content || '';
                                    const date = meta('article:published_time') || (main.innerText.match(/\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}/) || [''])[0];
                                    const media = [...article.querySelectorAll(':scope > p img,:scope > figure img,video')].map(node => ({
                                        kind: node.tagName === 'IMG' ? 'image' : 'video',
                                        url: node.getAttribute('data-src') || node.currentSrc || node.src || '', title: node.alt || '',
                                        context: ((node.closest('p,figure')?.nextElementSibling?.innerText || '') + ' ' + (node.closest('p,figure')?.previousElementSibling?.innerText || '')).trim().slice(0,350),
                                        width: node.naturalWidth || node.videoWidth || Number(node.getAttribute('width')) || 0,
                                        height: node.naturalHeight || node.videoHeight || Number(node.getAttribute('height')) || 0
                                    })).filter(row => /^https?:\/\//.test(row.url)).slice(0,64);
                                    const players = [...article.querySelectorAll('video')];
                                    if (players.length === 1 && players[0].currentSrc.startsWith('blob:')) {
                                        // The public player loads a small initialization MP4 before its full movie.
                                        // Keep the largest observed movie response, not the initialization header.
                                        const movie = Object.entries(playerFiles).sort((a,b)=>b[1]-a[1])[0];
                                        if ((!movie || players[0].readyState < 2) && " + attempt + @" < 3) return null;
                                        if (movie) media.unshift({kind:'video',url:movie[0],title:'原文播放器视频',
                                            context:'原文播放器中的视频，请先预览核对内容。',
                                            width:players[0].videoWidth || 0,height:players[0].videoHeight || 0});
                                    }
                                    return {url:location.href, title:(main.querySelector('h1')?.innerText || document.title).trim(),
                                        summary:article.innerText.trim().slice(0,7000), published:date, media_candidates:media};
                                })()");
                                if (data != "null" && !String.IsNullOrWhiteSpace(data))
                                {
                                    WriteResult(data);
                                    code = 0;
                                    form.Close();
                                    return;
                                }
                            }
                            if (capture) Console.Error.WriteLine("No readable official notice heading, publication date and paragraph found");
                            WriteResult(await browser.ExecuteScriptAsync("JSON.stringify({error:'no_article',title:document.title,excerpt:document.body.innerText.slice(0,160)})"));
                            form.Close();
                        }
                        catch (Exception error) { Console.Error.WriteLine(error.GetType().Name + ": " + error.Message); if (!form.IsDisposed) form.Close(); }
                    };
                    browser.Navigate(target.AbsoluteUri);
                }
                catch (Exception error) { Console.Error.WriteLine(error.GetType().Name + ": " + error.Message); if (!form.IsDisposed) form.Close(); }
            };
            Application.Run(form);
        }
        return code;
    }
}
