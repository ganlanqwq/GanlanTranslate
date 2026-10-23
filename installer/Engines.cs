using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using Microsoft.Win32;

namespace GanlanSetup
{
    /// <summary>安装 / 卸载过程的日志（同时写进临时目录的日志文件）和进度。</summary>
    sealed class Reporter : IDisposable
    {
        public readonly string LogPath;
        readonly StreamWriter _file;
        readonly object _lock = new object();

        public Action<string> OnLog;
        /// <summary>阶段、总进度 0~1、当前第几个、一共几个</summary>
        public Action<string, double, int, int> OnProgress;

        public Reporter(string name)
        {
            // 沙盒测试的日志放在沙盒里，不和真正安装的混在一起
            LogPath = Path.Combine(Env.Current.Sandbox ? Env.Current.SandboxRoot : Path.GetTempPath(), "GanlanTranslate-" + name + ".log");
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(LogPath));
                _file = new StreamWriter(LogPath, false, new UTF8Encoding(true));
            }
            catch (Exception)
            {
                _file = null;   // 写不了日志文件也照样装
            }
        }

        public void Log(string line)
        {
            lock (_lock)
            {
                _file?.WriteLine("[" + DateTime.Now.ToString("HH:mm:ss") + "] " + line);
                _file?.Flush();
            }
            OnLog?.Invoke(line);
        }

        public void Progress(string phase, double fraction, int done = 0, int total = 0) =>
            OnProgress?.Invoke(phase, Math.Max(0, Math.Min(1, fraction)), done, total);

        public void CopyTo(string path)
        {
            lock (_lock)
            {
                if (_file == null) return;
                _file.Flush();
                try { File.Copy(LogPath, path, true); }
                catch (Exception) { }
            }
        }

        public void Dispose()
        {
            lock (_lock) _file?.Dispose();
        }
    }

    static class Manifest
    {
        public static List<string> Read(string dir)
        {
            string path = Path.Combine(dir, AppInfo.ManifestName);
            if (!File.Exists(path)) return null;
            return File.ReadAllLines(path, Encoding.UTF8)
                .Select(l => l.Trim())
                .Where(l => l.Length > 0 && !l.StartsWith("#"))
                .ToList();
        }

        public static void Write(string dir, string version, IEnumerable<string> files)
        {
            var lines = new List<string> { "# " + AppInfo.DisplayName + " " + version + " 安装的程序文件（更新、卸载时按这个删，用户数据不在里面）" };
            lines.AddRange(files);
            File.WriteAllLines(Path.Combine(dir, AppInfo.ManifestName), lines, new UTF8Encoding(false));
        }

        /// <summary>相对路径是不是用户数据（预设、插件、自定义词语、模型、VR 设置）。</summary>
        public static bool IsUserData(string relative)
        {
            string r = relative.Replace('/', '\\').TrimStart('\\');
            return AppInfo.UserData.Any(u =>
                string.Equals(r, u, StringComparison.OrdinalIgnoreCase) ||
                r.StartsWith(u + "\\", StringComparison.OrdinalIgnoreCase));
        }
    }

    static class Shell
    {
        public static void OpenUrl(string url)
        {
            if (url == null || !(url.StartsWith("https://") || url.StartsWith("http://"))) return;
            try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
            catch (Exception) { }
        }

        public static void OpenFolder(string dir)
        {
            if (!Directory.Exists(dir)) return;
            try { Process.Start(new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "explorer.exe"), Paths.Quote(dir))); }
            catch (Exception) { }
        }

        public static void OpenFile(string path)
        {
            if (!File.Exists(path)) return;
            try { Process.Start(new ProcessStartInfo(path) { UseShellExecute = true }); }
            catch (Exception) { }
        }

        /// <summary>
        /// 安装程序是管理员身份，直接启动的话甘岚翻译也是管理员身份（之后从快捷方式打开的普通实例读不到它的单实例锁）。
        /// 经 explorer 启动就是普通权限（和 NSIS 版一样）。
        /// </summary>
        public static void LaunchAsUser(string exe)
        {
            try
            {
                Process.Start(new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "explorer.exe"), Paths.Quote(exe)));
            }
            catch (Exception)
            {
            }
        }

        /// <summary>出错原因给用户看的那一句。</summary>
        public static string Describe(Exception e)
        {
            switch (e)
            {
                case SetupException _:
                case CorruptPackageException _:
                    return e.Message;
                case UnauthorizedAccessException _:
                    return Strings.T("err.access", e.Message);
                case InvalidDataException _:
                    return Strings.T("err.corrupt");
                default:
                    return e.Message;
            }
        }
    }

    /// <summary>安装：校验 → 关程序 → 解压 → 删旧文件 → 搬旧版数据 → 权限 → 卸载程序 → 注册表 → 快捷方式。</summary>
    sealed class InstallEngine
    {
        readonly Payload _payload;
        readonly Reporter _r;
        readonly Env _env = Env.Current;
        int _files;
        long _bytes;

        public InstallEngine(Payload payload, Reporter reporter)
        {
            _payload = payload;
            _r = reporter;
        }

        public void Run(string dir, bool desktop, bool startMenu)
        {
            var clock = Stopwatch.StartNew();
            dir = Paths.Normalize(dir);
            string installedDir = _env.InstalledDir();
            string installedVersion = _env.InstalledVersion();
            string oldDir = _env.ReadString(AppInfo.OldKey, "InstallDir");

            _r.Log(Strings.T("log.start", Strings.AppName, _payload.Version));
            if (_env.Sandbox) _r.Log(Strings.T("log.sandbox", _env.SandboxRoot));
            _r.Log(Strings.T("log.target", dir));
            if (installedDir != null && Paths.Same(installedDir, dir) && !string.IsNullOrEmpty(installedVersion))
                _r.Log(Strings.T("log.upgrade", installedVersion));

            // 1. 校验（安装目录还没碰）
            _r.Log(Strings.T("log.verify"));
            _payload.Verify(f => _r.Progress("verify", 0.04 * f));
            _r.Log(Strings.T("log.verifyOk", _payload.Files.Count, Paths.FormatSize(_payload.TotalSize)));

            // 2. 关掉正在运行的程序
            _r.Progress("close", 0.04);
            RunningApps.Close(new[] { dir, oldDir }, !_env.Sandbox, _r.Log);

            // 3. 解压
            _r.Progress("extract", 0.06, 0, _payload.Files.Count);
            Directory.CreateDirectory(dir);
            _r.Log(Strings.T("log.extracting", dir));
            var extractClock = Stopwatch.StartNew();
            FileOps.Log = _r.Log;
            FileOps.RemoveLockedLeftovers(dir);
            _payload.Extract(dir,
                entry =>
                {
                    _files++;
                    _r.Log(Strings.T("log.file", entry.Path.Replace('/', '\\')));
                    ReportExtract();
                },
                bytes =>
                {
                    _bytes = bytes;
                    ReportExtract();
                });
            _r.Log(Strings.T("log.extracted", _payload.Files.Count, extractClock.Elapsed.TotalSeconds));

            // 4. 旧版有、新版没有的程序文件
            _r.Progress("finish", 0.9);
            RemoveStaleFiles(dir);

            // 5. 改名前的旧版（GanLan）
            MigrateOldInstall(dir, oldDir);

            // 6. 插件文件夹、Users 写权限（程序在安装目录里写 presets.json、VR 设置、下载的模型）
            _r.Log(Strings.T("log.plugins"));
            Directory.CreateDirectory(Path.Combine(dir, "plugins"));
            _r.Progress("finish", 0.92);
            GrantUsers(dir);

            // 7. 卸载程序、文件清单
            _r.Progress("finish", 0.97);
            _r.Log(Strings.T("log.uninstaller"));
            FileOps.Guard(Path.Combine(dir, AppInfo.UninstallerName), () => _payload.WriteStub(Path.Combine(dir, AppInfo.UninstallerName)));
            Manifest.Write(dir, _payload.Version, _payload.Files.Select(f => f.Path.Replace('/', '\\')));

            // 8. 注册表
            WriteRegistry(dir);

            // 9. 快捷方式
            _r.Progress("finish", 0.99);
            Shortcuts(dir, desktop, startMenu);

            _r.Log(Strings.T("log.done", clock.Elapsed.TotalSeconds));
            _r.Progress("done", 1);
            _r.CopyTo(Path.Combine(dir, AppInfo.LogName));
        }

        void ReportExtract() =>
            _r.Progress("extract", 0.06 + 0.84 * (_payload.TotalSize > 0 ? _bytes / (double)_payload.TotalSize : 1), _files, _payload.Files.Count);

        void RemoveStaleFiles(string dir)
        {
            List<string> old = Manifest.Read(dir);
            if (old == null) return;   // 从 NSIS 版升级的那一次没有清单
            var now = new HashSet<string>(_payload.Files.Select(f => f.Path.Replace('/', '\\')), StringComparer.OrdinalIgnoreCase);
            int removed = 0;
            foreach (string rel in old)
            {
                if (now.Contains(rel) || Manifest.IsUserData(rel)) continue;
                string full;
                try { full = Paths.Under(dir, rel); }
                catch (InvalidDataException) { continue; }
                if (!File.Exists(full)) continue;
                if (FileOps.TryDelete(full))
                {
                    removed++;
                    _r.Log(Strings.T("log.stale", rel));
                    FileOps.RemoveEmptyParents(Path.GetDirectoryName(full), dir);
                }
            }
            if (removed > 0) _r.Log(Strings.T("log.staleCount", removed));
        }

        /// <summary>和 installer.nsi 的 MigrateOldInstall 一样：旧版的数据搬过来，删旧目录、旧开始菜单、旧注册表键。</summary>
        void MigrateOldInstall(string dir, string oldDir)
        {
            if (string.IsNullOrWhiteSpace(oldDir)) return;
            try
            {
                if (Paths.Same(oldDir, dir))
                {
                    // 自己选回了旧目录：数据已经在这里，只删旧名字的程序
                    FileOps.TryDelete(Path.Combine(dir, AppInfo.OldExeName));
                    FileOps.TryDelete(Path.Combine(dir, AppInfo.OldVrExeName));
                }
                else if (File.Exists(Path.Combine(oldDir, AppInfo.OldExeName)))
                {
                    _r.Log(Strings.T("log.migrate", oldDir));
                    string presets = Path.Combine(oldDir, "presets.json");
                    if (File.Exists(presets)) File.Copy(presets, Path.Combine(dir, "presets.json"), true);
                    foreach (string name in new[] { "hot_words_private", "terminology_private", "models", "local_asr_models" })
                    {
                        string from = Path.Combine(oldDir, name);
                        if (Directory.Exists(from)) FileOps.CopyTree(from, Path.Combine(dir, name));
                    }
                    string vr = Path.Combine(oldDir, "vr_overlay", "settings.json");
                    if (File.Exists(vr))
                    {
                        Directory.CreateDirectory(Path.Combine(dir, "vr_overlay"));
                        File.Copy(vr, Path.Combine(dir, "vr_overlay", "settings.json"), true);
                    }
                    // 只删确实是旧版安装目录的文件夹（里面有 GanLan.exe）
                    FileOps.DeleteTree(oldDir, null);
                    _r.Log(Strings.T("log.migrateRemoved", oldDir));
                }
                FileOps.TryDelete(Path.Combine(_env.UserDesktop, AppInfo.DisplayName + ".lnk"));
                string oldMenu = Path.Combine(_env.UserPrograms, AppInfo.OldKey);
                if (Directory.Exists(oldMenu)) Directory.Delete(oldMenu, true);
                _env.DeleteKey(AppInfo.OldUninstallKey);
                _env.DeleteKey(AppInfo.OldKey);
            }
            catch (Exception e)
            {
                _r.Log(Strings.T("log.failed", e.Message));
            }
        }

        void GrantUsers(string dir)
        {
            _r.Log(Strings.T("log.acl"));
            try
            {
                // S-1-5-32-545 = Users 组，不受系统语言影响
                var psi = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, "icacls.exe"),
                    Paths.Quote(dir) + " /grant *S-1-5-32-545:(OI)(CI)M /T /Q")
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                };
                using (Process p = Process.Start(psi))
                {
                    var error = p.StandardError.ReadToEndAsync();
                    p.StandardOutput.ReadToEnd();
                    p.WaitForExit();
                    if (p.ExitCode != 0)
                        _r.Log(Strings.T("log.aclFailed", (error.Result ?? "").Trim() + " (" + p.ExitCode + ")"));
                }
            }
            catch (Exception e)
            {
                _r.Log(Strings.T("log.aclFailed", e.Message));
            }
        }

        void WriteRegistry(string dir)
        {
            _r.Log(Strings.T("log.registry"));
            using (RegistryKey key = _env.CreateKey(AppInfo.Key))
                key.SetValue("InstallDir", dir);
            string uninstaller = Path.Combine(dir, AppInfo.UninstallerName);
            using (RegistryKey key = _env.CreateKey(AppInfo.UninstallKey))
            {
                key.SetValue("DisplayName", AppInfo.DisplayName);
                key.SetValue("DisplayVersion", _payload.Version);
                key.SetValue("Publisher", AppInfo.Publisher);
                key.SetValue("InstallLocation", dir);
                key.SetValue("UninstallString", "\"" + uninstaller + "\"");
                key.SetValue("QuietUninstallString", "\"" + uninstaller + "\" /S");
                key.SetValue("DisplayIcon", Path.Combine(dir, AppInfo.ExeName));
                key.SetValue("URLInfoAbout", AppInfo.Homepage);
                key.SetValue("EstimatedSize", (int)Math.Min(int.MaxValue, _payload.TotalSize / 1024), RegistryValueKind.DWord);
                key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            }
        }

        void Shortcuts(string dir, bool desktop, bool startMenu)
        {
            string exe = Path.Combine(dir, AppInfo.ExeName);
            string desktopLink = DesktopLink(_env);
            if (desktop)
            {
                Native.CreateShortcut(desktopLink, exe, Strings.AppName);
                _r.Log(Strings.T("log.shortcut", desktopLink));
            }
            else if (File.Exists(desktopLink) && FileOps.TryDelete(desktopLink))
            {
                _r.Log(Strings.T("log.shortcutRemoved", desktopLink));
            }

            // 开始菜单整个文件夹重建（「卸载」快捷方式的名字跟着界面语言，换了语言不要留两个）
            string menu = MenuFolder(_env);
            bool had = Directory.Exists(menu);
            if (had) Directory.Delete(menu, true);
            if (startMenu)
            {
                Directory.CreateDirectory(menu);
                Native.CreateShortcut(Path.Combine(menu, AppInfo.DisplayName + ".lnk"), exe, Strings.AppName);
                Native.CreateShortcut(Path.Combine(menu, Strings.T("shortcut.uninstall") + ".lnk"),
                    Path.Combine(dir, AppInfo.UninstallerName), Strings.T("title.uninstall", Strings.AppName));
                _r.Log(Strings.T("log.shortcut", menu));
            }
            else if (had)
            {
                _r.Log(Strings.T("log.shortcutRemoved", menu));
            }
        }

        public static string DesktopLink(Env env) => Path.Combine(env.UserDesktop, AppInfo.DisplayName + ".lnk");
        public static string MenuFolder(Env env) => Path.Combine(env.UserPrograms, AppInfo.DirName);
    }

    /// <summary>卸载：关程序 → 删程序文件（或整个目录） → 快捷方式 → 注册表。</summary>
    sealed class UninstallEngine
    {
        readonly Reporter _r;
        readonly Env _env = Env.Current;

        public UninstallEngine(Reporter reporter)
        {
            _r = reporter;
        }

        /// <summary>返回留下来的目录（保留了用户数据时），全删干净了返回 null。</summary>
        public string Run(string dir, bool removeData)
        {
            dir = Paths.Normalize(dir);
            _r.Log(Strings.T("log.uStart", Strings.AppName, dir));
            if (_env.Sandbox) _r.Log(Strings.T("log.sandbox", _env.SandboxRoot));
            _r.Progress("close", 0.02);
            RunningApps.Close(new[] { dir }, !_env.Sandbox, _r.Log);

            int locked;
            if (removeData)
            {
                _r.Log(Strings.T("log.uAll"));
                int total = Directory.Exists(dir) ? Directory.EnumerateFiles(dir, "*", SearchOption.AllDirectories).Count() : 0;
                locked = FileOps.DeleteTree(dir, n => _r.Progress("remove", 0.05 + 0.85 * n / Math.Max(1, total), n, total));
                _r.Log(Strings.T("log.uRemoved", total - locked));
            }
            else
            {
                locked = RemoveProgramFiles(dir);
            }
            if (locked > 0) _r.Log(Strings.T("log.uLocked", locked));

            _r.Progress("finish", 0.92);
            foreach (string link in new[] { Path.Combine(_env.UserDesktop, AppInfo.DisplayName + ".lnk"), Path.Combine(_env.CommonDesktop, AppInfo.DisplayName + ".lnk") })
                if (File.Exists(link) && FileOps.TryDelete(link)) _r.Log(Strings.T("log.shortcutRemoved", link));
            foreach (string menu in new[] { Path.Combine(_env.UserPrograms, AppInfo.DirName), Path.Combine(_env.CommonPrograms, AppInfo.DirName) })
            {
                if (!Directory.Exists(menu)) continue;
                try
                {
                    Directory.Delete(menu, true);
                    _r.Log(Strings.T("log.shortcutRemoved", menu));
                }
                catch (Exception)
                {
                }
            }

            _r.Log(Strings.T("log.uRegistry"));
            _env.DeleteKey(AppInfo.UninstallKey);
            _env.DeleteKey(AppInfo.Key);

            _r.Log(Strings.T("log.uDone"));
            _r.Progress("done", 1);
            return Directory.Exists(dir) ? dir : null;
        }

        /// <summary>按清单删程序文件，用户数据留下；返回删不掉（安排重启后删）的数量。</summary>
        int RemoveProgramFiles(string dir)
        {
            _r.Log(Strings.T("log.uFiles"));
            if (!Directory.Exists(dir)) return 0;
            List<string> files = Manifest.Read(dir);
            if (files == null)
            {
                // 没有清单：除了用户数据都删
                files = Directory.EnumerateFiles(dir, "*", SearchOption.AllDirectories)
                    .Select(f => f.Substring(dir.Length).TrimStart('\\')).ToList();
            }
            files.AddRange(AppInfo.Generated);

            int removed = 0, locked = 0, done = 0;
            foreach (string rel in files)
            {
                done++;
                if (Manifest.IsUserData(rel)) continue;
                string full;
                try { full = Paths.Under(dir, rel); }
                catch (InvalidDataException) { continue; }
                if (!File.Exists(full)) continue;
                if (FileOps.TryDelete(full)) removed++;
                else
                {
                    locked++;
                    Native.MoveFileExW(full, null, Native.MOVEFILE_DELAY_UNTIL_REBOOT);
                }
                if (done % 25 == 0) _r.Progress("remove", 0.05 + 0.85 * done / files.Count, done, files.Count);
            }
            _r.Log(Strings.T("log.uRemoved", removed));
            FileOps.RemoveLockedLeftovers(dir);

            // 删空文件夹（从最深的开始），安装目录空了也删掉
            foreach (string sub in Directory.EnumerateDirectories(dir, "*", SearchOption.AllDirectories).OrderByDescending(d => d.Length).ToList())
            {
                try
                {
                    if (!Directory.EnumerateFileSystemEntries(sub).Any()) Directory.Delete(sub);
                }
                catch (Exception)
                {
                }
            }
            try
            {
                if (!Directory.EnumerateFileSystemEntries(dir).Any()) Directory.Delete(dir);
            }
            catch (Exception)
            {
            }

            var kept = AppInfo.UserData.Where(u => File.Exists(Path.Combine(dir, u)) || Directory.Exists(Path.Combine(dir, u))).ToList();
            if (kept.Count > 0) _r.Log(Strings.T("log.uKept", string.Join(", ", kept)));
            return locked;
        }
    }
}
