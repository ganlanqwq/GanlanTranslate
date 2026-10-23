using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Text;
using System.Threading;

namespace GanlanSetup
{
    /// <summary>
    /// 找、关正在运行的甘岚翻译。按 exe 所在的目录找，不按进程名：
    /// 在别的目录测试安装时，不会把本机正在用的那份关掉。
    /// </summary>
    static class RunningApps
    {
        static List<string> Roots(IEnumerable<string> dirs) =>
            dirs.Where(d => !string.IsNullOrWhiteSpace(d)).Select(Paths.Normalize).Distinct(StringComparer.OrdinalIgnoreCase).ToList();

        /// <summary>exe 在这些目录里的进程（路径, 进程）。</summary>
        public static List<KeyValuePair<string, Process>> Under(IEnumerable<string> dirs)
        {
            var roots = Roots(dirs);
            var result = new List<KeyValuePair<string, Process>>();
            if (roots.Count == 0) return result;
            int self = Process.GetCurrentProcess().Id;
            foreach (Process p in Process.GetProcesses())
            {
                string path = p.Id == self ? null : Native.ProcessPath(p.Id);
                if (path != null && roots.Any(r => Paths.IsInside(path, r)))
                    result.Add(new KeyValuePair<string, Process>(path, p));
                else
                    p.Dispose();
            }
            return result;
        }

        public static bool AnyUnder(params string[] dirs)
        {
            var list = Under(dirs);
            foreach (var item in list) item.Value.Dispose();
            return list.Count > 0;
        }

        static bool IsMainExe(string path) =>
            string.Equals(Path.GetFileName(path), AppInfo.ExeName, StringComparison.OrdinalIgnoreCase);

        /// <summary>
        /// 先请主程序自己退出（POST /api/quit，1.1.0 起才有），让网页存储（Key、设置）写完，最多等 10 秒；
        /// 还在的（VR 附属程序、旧版程序、卡住的）强制结束。
        /// </summary>
        public static void Close(IEnumerable<string> dirs, bool askQuit, Action<string> log)
        {
            var roots = Roots(dirs);
            var running = Under(roots);
            if (running.Count == 0) return;
            log(Strings.T("log.closing"));
            bool mainRunning = running.Any(item => IsMainExe(item.Key));
            foreach (var item in running) item.Value.Dispose();

            if (askQuit && mainRunning && AskQuit())
            {
                log(Strings.T("log.askedQuit"));
                var deadline = DateTime.UtcNow.AddSeconds(10);
                while (DateTime.UtcNow < deadline)
                {
                    Thread.Sleep(300);
                    var still = Under(roots);
                    bool main = still.Any(item => IsMainExe(item.Key));
                    foreach (var item in still) item.Value.Dispose();
                    if (!main)
                    {
                        log(Strings.T("log.exited"));
                        break;
                    }
                }
            }

            foreach (var item in Under(roots))
            {
                using (Process p = item.Value)
                {
                    try
                    {
                        p.Kill();
                        p.WaitForExit(5000);
                        log(Strings.T("log.killed", Path.GetFileName(item.Key)));
                    }
                    catch (Exception)
                    {
                        // 已经自己退了，或者没权限（之后写文件时会报「被占用」）
                    }
                }
            }
            Thread.Sleep(300);
        }

        static bool AskQuit()
        {
            try
            {
                var request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:5001/api/quit");
                request.Method = "POST";
                request.ContentType = "application/json";
                request.Timeout = 3000;
                request.Proxy = null;
                byte[] body = Encoding.UTF8.GetBytes("{}");
                request.ContentLength = body.Length;
                using (Stream s = request.GetRequestStream()) s.Write(body, 0, body.Length);
                using (var response = (HttpWebResponse)request.GetResponse())
                using (var reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
                    return reader.ReadToEnd().Replace(" ", "").Contains("\"success\":true");
            }
            catch (Exception)
            {
                return false;
            }
        }
    }
}
