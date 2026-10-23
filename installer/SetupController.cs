using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Threading;

namespace GanlanSetup
{
    /// <summary>窗口这边给控制器用的几样东西（不直接引用 HostWindow：它依赖的 WebView2 DLL 要等解压后才能加载）。</summary>
    interface IHost
    {
        Dispatcher Dispatcher { get; }
        IntPtr Handle { get; }
        /// <summary>WebView2 运行库支持网页里的 app-region: drag（不支持时网页按下页首发 drag 消息）。</summary>
        bool NativeDrag { get; }
        void Send(object message);
        void DragMoveFromPage();
        void Close();
    }

    /// <summary>
    /// 网页和安装 / 卸载之间的那一层。网页发来的消息（type）：
    /// ready、browse、checkDir、install、uninstall、launch、openFolder、openLog、copy、openUrl、drag、minimize、close。
    /// 界面语言跟 Windows 显示语言（Strings.Lang，init 里告诉网页），网页上不能切。
    /// 发给网页：init、dir、progress、log、done、closeRequest。
    /// </summary>
    sealed class SetupController
    {
        public readonly Payload Payload;
        readonly string _uninstallDir;
        public bool IsUninstall => Payload == null;
        public IHost Host;
        public int ExitCode = 1;

        bool _busy, _finished, _closeConfirmed, _pageReady;
        string _installedDir, _lastDir, _logPath;
        readonly object _lock = new object();
        readonly List<string> _pendingLogs = new List<string>();
        Dictionary<string, object> _pendingProgress;
        DispatcherTimer _flushTimer;

        public SetupController(Payload payload, string uninstallDir)
        {
            Payload = payload;
            _uninstallDir = uninstallDir;
        }

        public object Window
        {
            set => Host = (IHost)value;
        }

        /// <summary>安装中不能关；还没装完要先问一下（网页弹确认框）。</summary>
        public bool CanClose => !_busy && (_closeConfirmed || _finished || !_pageReady);

        public void RequestClose()
        {
            if (_busy) return;
            Host.Send(new Dictionary<string, object> { ["type"] = "closeRequest" });
        }

        public void ForceClose()
        {
            _closeConfirmed = true;
            Host.Close();
        }

        static string Str(Dictionary<string, object> m, string key) => m.TryGetValue(key, out object v) ? v as string : null;
        static bool Bool(Dictionary<string, object> m, string key) => m.TryGetValue(key, out object v) && v is bool b && b;

        public void OnMessage(Dictionary<string, object> m)
        {
            switch (Str(m, "type"))
            {
                case "ready":
                    _pageReady = true;
                    Host.Send(InitState());
                    break;
                case "browse":
                    Browse(Str(m, "dir"));
                    break;
                case "checkDir":
                    Host.Send(DirState(Str(m, "dir"), false));
                    break;
                case "install":
                    StartInstall(Str(m, "dir"), Bool(m, "desktop"), Bool(m, "startMenu"));
                    break;
                case "uninstall":
                    StartUninstall(Bool(m, "removeData"));
                    break;
                case "launch":
                    if (_finished && !IsUninstall && _lastDir != null && !Env.Current.Sandbox)
                        Shell.LaunchAsUser(Path.Combine(_lastDir, AppInfo.ExeName));
                    break;
                case "openFolder":
                    Shell.OpenFolder(Str(m, "dir") ?? _lastDir);
                    break;
                case "openLog":
                    Shell.OpenFile(_logPath);
                    break;
                case "copy":
                    try { Clipboard.SetText(Str(m, "text") ?? ""); }
                    catch (Exception) { }
                    break;
                case "openUrl":
                    Shell.OpenUrl(Str(m, "url"));
                    break;
                case "drag":
                    Host.DragMoveFromPage();
                    break;
                case "minimize":
                    if (Host is Window w) w.WindowState = WindowState.Minimized;
                    break;
                case "close":
                    if (!_busy) ForceClose();
                    break;
            }
        }

        Dictionary<string, object> InitState()
        {
            Env env = Env.Current;
            var state = new Dictionary<string, object>
            {
                ["type"] = "init",
                ["mode"] = IsUninstall ? "uninstall" : "install",
                ["lang"] = Strings.Lang,
                ["sandbox"] = env.Sandbox,
                ["nativeDrag"] = Host.NativeDrag,
                ["frame"] = Native.IsWindows11 ? "system" : "region",   // Win10 的圆角边框网页自己画
            };
            string installedVersion = env.InstalledVersion();
            _installedDir = env.InstalledDir();
            state["installedVersion"] = installedVersion;
            state["installedDir"] = _installedDir;

            if (IsUninstall)
            {
                state["version"] = installedVersion;
                state["dir"] = _uninstallDir;
                state["running"] = RunningApps.AnyUnder(_uninstallDir);
                state["keep"] = AppInfo.UserData
                    .Where(u => File.Exists(Path.Combine(_uninstallDir, u)) || Directory.Exists(Path.Combine(_uninstallDir, u)))
                    .ToList();
                return state;
            }

            string dir = Program.DefaultDir();
            Program.ShortcutDefaults(dir, out bool desktop, out bool startMenu);
            bool upgrade = _installedDir != null && Paths.Same(_installedDir, dir);
            state["version"] = Payload.Version;
            state["upgrade"] = upgrade;
            state["dirLocked"] = upgrade && Program.Opt.Dir == null;
            state["newer"] = upgrade && IsNewer(installedVersion, Payload.Version);
            state["running"] = RunningApps.AnyUnder(dir);
            state["desktop"] = desktop;
            state["startMenu"] = startMenu;
            foreach (var kv in DirState(dir, false)) if (kv.Key != "type") state[kv.Key] = kv.Value;
            return state;
        }

        static bool IsNewer(string installed, string package) =>
            Version.TryParse(installed ?? "", out Version a) && Version.TryParse(package ?? "", out Version b) && a > b;

        /// <summary>检查安装位置；picked=true 时是刚在对话框里选的文件夹，不是空的普通文件夹就在后面加「Ganlan Translate」。</summary>
        Dictionary<string, object> DirState(string raw, bool picked)
        {
            var result = new Dictionary<string, object>
            {
                ["type"] = "dir",
                ["dir"] = raw ?? "",
                ["requiredBytes"] = Payload?.TotalSize ?? 0,
                ["freeBytes"] = -1L,
                ["dirError"] = null,
            };
            string error = ValidateDir(raw, out string dir);
            if (error == null && picked && Directory.Exists(dir)
                && !File.Exists(Path.Combine(dir, AppInfo.ExeName))
                && !string.Equals(Path.GetFileName(dir), AppInfo.DirName, StringComparison.OrdinalIgnoreCase)
                && Directory.EnumerateFileSystemEntries(dir).Any())
            {
                dir = Path.Combine(dir, AppInfo.DirName);
            }
            if (error == null) result["dir"] = dir;
            result["dirError"] = error;
            try
            {
                if (error == null) result["freeBytes"] = new DriveInfo(Path.GetPathRoot(dir)).AvailableFreeSpace;
            }
            catch (Exception)
            {
                // 网络路径之类查不到剩余空间
            }
            result["existing"] = error == null && File.Exists(Path.Combine(dir, AppInfo.ExeName));
            return result;
        }

        /// <summary>返回出错代码（网页按代码显示文字），没问题返回 null。</summary>
        static string ValidateDir(string raw, out string dir)
        {
            dir = null;
            if (string.IsNullOrWhiteSpace(raw)) return "empty";
            try
            {
                string trimmed = raw.Trim().Trim('"');
                if (!Path.IsPathRooted(trimmed) || trimmed.StartsWith("\\") && !trimmed.StartsWith("\\\\")) return "relative";
                dir = Paths.Normalize(trimmed);
            }
            catch (Exception)
            {
                return "invalid";
            }
            if (string.Equals(dir, Path.GetPathRoot(dir), StringComparison.OrdinalIgnoreCase)) return "root";
            string windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
            if (Paths.Same(dir, windows) || Paths.IsInside(dir, windows)) return "system";
            if (File.Exists(dir)) return "file";
            return null;
        }

        void Browse(string current)
        {
            string picked = Native.PickFolder(Host.Handle, null, current);
            if (picked != null) Host.Send(DirState(picked, true));
        }

        // ---------------- 安装 / 卸载 ----------------

        void StartInstall(string rawDir, bool desktop, bool startMenu)
        {
            if (_busy || IsUninstall) return;
            if (ValidateDir(rawDir, out string dir) is string error)
            {
                Host.Send(new Dictionary<string, object> { ["type"] = "done", ["ok"] = false, ["error"] = Strings.T("err.badDir", rawDir) });
                return;
            }
            Run(reporter =>
            {
                _lastDir = dir;
                new InstallEngine(Payload, reporter).Run(dir, desktop, startMenu);
                return null;
            }, "Setup");
        }

        void StartUninstall(bool removeData)
        {
            if (_busy || !IsUninstall) return;
            _lastDir = _uninstallDir;
            Run(reporter => new UninstallEngine(reporter).Run(_uninstallDir, removeData), "Uninstall");
        }

        void Run(Func<Reporter, string> work, string logName)
        {
            _busy = true;
            StartFlushing();
            Task.Run(() =>
            {
                var done = new Dictionary<string, object> { ["type"] = "done" };
                using (var reporter = new Reporter(logName))
                {
                    _logPath = reporter.LogPath;
                    reporter.OnLog = line =>
                    {
                        lock (_lock) _pendingLogs.Add(line);
                    };
                    reporter.OnProgress = (phase, fraction, current, total) =>
                    {
                        lock (_lock)
                            _pendingProgress = new Dictionary<string, object>
                            {
                                ["type"] = "progress", ["phase"] = phase, ["percent"] = Math.Round(fraction * 100, 1),
                                ["done"] = current, ["total"] = total,
                            };
                    };
                    try
                    {
                        done["leftover"] = work(reporter);
                        done["ok"] = true;
                    }
                    catch (Exception e)
                    {
                        string reason = Shell.Describe(e);
                        reporter.Log(Strings.T("log.failed", reason));
                        if (!(e is SetupException) && !(e is CorruptPackageException)) reporter.Log(e.ToString());
                        done["ok"] = false;
                        done["error"] = reason;
                    }
                }
                done["logPath"] = _logPath;
                done["dir"] = _lastDir;
                Host.Dispatcher.BeginInvoke(new Action(() =>
                {
                    Flush();
                    _flushTimer?.Stop();
                    _busy = false;
                    bool ok = (bool)done["ok"];
                    if (ok)
                    {
                        _finished = true;
                        ExitCode = 0;
                    }
                    Host.Send(done);
                }));
            });
        }

        void StartFlushing()
        {
            if (_flushTimer != null)
            {
                _flushTimer.Start();
                return;
            }
            _flushTimer = new DispatcherTimer(TimeSpan.FromMilliseconds(80), DispatcherPriority.Background, (s, e) => Flush(), Host.Dispatcher);
            _flushTimer.Start();
        }

        /// <summary>日志和进度攒一下再发（解压时一秒几百个文件）。</summary>
        void Flush()
        {
            List<string> lines = null;
            Dictionary<string, object> progress;
            lock (_lock)
            {
                if (_pendingLogs.Count > 0)
                {
                    lines = new List<string>(_pendingLogs);
                    _pendingLogs.Clear();
                }
                progress = _pendingProgress;
                _pendingProgress = null;
            }
            if (lines != null) Host.Send(new Dictionary<string, object> { ["type"] = "log", ["lines"] = lines });
            if (progress != null) Host.Send(progress);
        }
    }
}
