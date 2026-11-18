using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace Acf.Studio
{
    internal static class J
    {
        internal static Dictionary<string, object> Obj(params object[] pairs)
        {
            var result = new Dictionary<string, object>();
            for (int i = 0; i < pairs.Length; i += 2)
                result.Add((string)pairs[i], pairs[i + 1]);
            return result;
        }

        internal static IDictionary<string, object> Map(object value)
        {
            return value as IDictionary<string, object> ?? new Dictionary<string, object>();
        }

        internal static object Get(IDictionary<string, object> row, string key)
        {
            object value;
            return row.TryGetValue(key, out value) ? value : null;
        }

        internal static string Text(IDictionary<string, object> row, string key)
        {
            return Convert.ToString(Get(row, key), CultureInfo.InvariantCulture) ?? "";
        }

        internal static IEnumerable<object> Items(object value)
        {
            var sequence = value as IEnumerable;
            return sequence == null || value is string || value is IDictionary
                ? Enumerable.Empty<object>() : sequence.Cast<object>();
        }

        internal static IEnumerable<IDictionary<string, object>> Rows(object value)
        {
            return Items(value).OfType<IDictionary<string, object>>();
        }
    }

    internal sealed class WorkerBridge
    {
        internal string Root { get; private set; }
        internal string DataRoot { get; private set; }
        internal bool CpuOnly { get; private set; }
        private readonly string executable;

        internal WorkerBridge()
        {
            Root = AppDomain.CurrentDomain.BaseDirectory;
            var local = Environment.GetEnvironmentVariable("LOCALAPPDATA");
            if (String.IsNullOrWhiteSpace(local))
                local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            DataRoot = Path.Combine(local, "AutoContentFactory");
            CpuOnly = Environment.GetEnvironmentVariable("CONTENT_AI_GPU") == "0";
            try
            {
                string mode = Path.Combine(DataRoot, "compute-mode.txt");
                if (File.Exists(mode)) CpuOnly = File.ReadAllText(mode, Encoding.UTF8).Trim() == "cpu";
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            executable = Path.Combine(Root, "_engine", "AutoContentFactory-Worker.exe");
        }

        internal void SetCpuOnly(bool enabled)
        {
            Directory.CreateDirectory(DataRoot);
            File.WriteAllText(Path.Combine(DataRoot, "compute-mode.txt"), enabled ? "cpu" : "auto", new UTF8Encoding(false));
            CpuOnly = enabled;
        }

        internal Task<object> RunAsync(string stage, string platform = null, object payload = null, string day = null)
        {
            return Task.Run(() => Run(stage, platform, payload, day ?? DateTime.Today.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)));
        }

        internal static string Seal(object value)
        {
            byte[] bytes = Encoding.UTF8.GetBytes(Json().Serialize(value));
            try
            {
                return Convert.ToBase64String(ProtectedData.Protect(bytes, null, DataProtectionScope.CurrentUser));
            }
            finally
            {
                Array.Clear(bytes, 0, bytes.Length);
            }
        }

        internal int RunCommand(string[] args)
        {
            using (var process = Start(args))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }

        private object Run(string stage, string platform, object payload, string day)
        {
            string output = Path.Combine(DataRoot, "output");
            string job = Path.Combine(output, ".jobs", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(job);
            string request = Path.Combine(job, "request.json");
            string result = Path.Combine(job, "result.json");
            try
            {
                File.WriteAllText(request, Json().Serialize(J.Obj("stage", stage, "day", day, "platform", platform,
                    "payload", payload, "output_dir", output, "now", null)), new UTF8Encoding(false));
                using (var process = Start(new[] { "--worker", request, result }))
                {
                    if (!process.WaitForExit(TimeoutFor(stage)))
                    {
                        if (!process.HasExited)
                            process.Kill();
                        process.WaitForExit();
                        throw new TimeoutException("此操作等待超时，可刷新状态后重试。");
                    }
                    if (!File.Exists(result))
                        throw new InvalidOperationException("后台任务提前结束。错误码 " + process.ExitCode + "。");
                    var response = J.Map(Json().DeserializeObject(File.ReadAllText(result, Encoding.UTF8)));
                    if (process.ExitCode != 0 || response.ContainsKey("error"))
                        throw new InvalidOperationException(J.Text(response, "error"));
                    return J.Get(response, "value");
                }
            }
            catch (Exception error)
            {
                // Keep one bounded diagnostic, without payloads or login data.
                // Job request/result files are still removed below.
                try
                {
                    Directory.CreateDirectory(DataRoot);
                    File.WriteAllText(Path.Combine(DataRoot, "last-worker-error.json"),
                        Json().Serialize(J.Obj("time", DateTimeOffset.Now.ToString("o"), "stage", stage,
                            "day", day, "platform", platform, "error", error.Message,
                            "build_time", File.GetLastWriteTimeUtc(executable).ToString("o"))), new UTF8Encoding(false));
                }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
                throw;
            }
            finally
            {
                try
                {
                    File.Delete(request);
                    File.Delete(result);
                    File.Delete(Path.ChangeExtension(result, ".tmp"));
                    Directory.Delete(job);
                }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
            }
        }

        private Process Start(string[] args)
        {
            if (!File.Exists(executable))
                throw new FileNotFoundException("找不到工作台后台组件，请完整解压工作台后重试。");
            var info = new ProcessStartInfo(executable, String.Join(" ", args.Select(Quote)))
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                WorkingDirectory = Root
            };
            info.EnvironmentVariables["ACF_STUDIO_ROOT"] = Root;
            info.EnvironmentVariables["CONTENT_AI_GPU"] = CpuOnly ? "0" : "auto";
            return Process.Start(info);
        }

        private static JavaScriptSerializer Json()
        {
            return new JavaScriptSerializer { MaxJsonLength = 32 * 1024 * 1024, RecursionLimit = 96 };
        }

        private static int TimeoutFor(string stage)
        {
            switch (stage)
            {
                case "topic_produce": return 14400000;
                case "topic_prepare": return 1200000;
                case "schedule_run_today": return 36000000;
                case "editorial_export":
                case "auto_cut":
                case "full_preview":
                case "render": return 10800000;
                case "direct_scene": return 1800000;
                case "editorial_preview":
                case "visual_direct":
                case "preview":
                case "copywriting":
                case "xiaohongshu_notes":
                case "ai": return 5400000;
                case "workspace_collection":
                case "editorial_collect":
                case "workspace_media_search":
                case "team_compute_once":
                case "cases_analyze":
                case "cases_collect":
                case "cases_learn":
                case "media":
                case "publish_prepare":
                case "publish_submit":
                case "publish_douyin_login":
                case "publish_personal_login":
                case "publish_personal_note":
                case "team_share":
                case "team_flag":
                case "workspace_quality_decide":
                case "workspace_ai_start":
                case "team_review_video": return 1200000;
                default: return 120000;
            }
        }

        private static string Quote(string argument)
        {
            if (argument.Length > 0 && argument.All(c => c != '"' && !Char.IsWhiteSpace(c)))
                return argument;
            var value = new StringBuilder().Append('"');
            int slashes = 0;
            foreach (char c in argument)
            {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') value.Append('\\', slashes + 1);
                value.Append('\\', slashes).Append(c);
                slashes = 0;
            }
            return value.Append('\\', slashes * 2).Append('"').ToString();
        }
    }
}
