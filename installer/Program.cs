using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Threading;
using System.Windows;

namespace GanlanSetup
{
    /// <summary>
    /// 入口。exe 后面带着安装数据就是安装程序，没带（安装目录里的 Uninstall.exe）就是卸载程序。
    /// </summary>
    static class Program
    {
        public static Options Opt;
        public static string SelfPath;
        static readonly List<string> DeleteAfterExit = new List<string>();
        static bool _uninstallMode;

        [STAThread]
        static int Main(string[] args)
        {
            Opt = Options.Parse(args);
            Strings.Lang = Opt.Lang ?? Strings.SystemLanguage();
            SelfPath = Assembly.GetExecutingAssembly().Location;
            try
            {
                Payload payload;
                try
                {
                    payload = Payload.TryOpen(SelfPath);
                }
                catch (CorruptPackageException e)
                {
                    if (!Opt.Silent) Message(e.Message, MessageBoxImage.Error);
                    return 2;
                }
                _uninstallMode = payload == null;
                return payload != null ? Install(payload) : Uninstall();
            }
            finally
            {
                CleanupAfterExit();
            }
        }

        // ---------------- 安装 ----------------

        static int Install(Payload payload)
        {
            if (NeedElevation()) return RelaunchElevated();
            if (Opt.Silent) return SilentInstall(payload, false);
            return RunWindow(new SetupController(payload, null), () =>
            {
                var choice = MessageBox.Show(Strings.T("dlg.noWebView2"), Strings.T("title.setup", Strings.AppName),
                    MessageBoxButton.YesNoCancel, MessageBoxImage.Warning);
                if (choice == MessageBoxResult.Yes) Shell.OpenUrl("https://developer.microsoft.com/microsoft-edge/webview2/");
                return choice == MessageBoxResult.No ? SilentInstall(payload, true) : 1;
            });
        }

        public static string DefaultDir()
        {
            if (Opt.Dir != null)
            {
                try { return Paths.Normalize(Opt.Dir); }
                catch (Exception) { }
            }
            return Env.Current.InstalledDir() ?? Env.Current.DefaultInstallDir;
        }

        /// <summary>快捷方式默认：新装两个都建；更新时照现在有没有来。</summary>
        public static void ShortcutDefaults(string dir, out bool desktop, out bool startMenu)
        {
            string installed = Env.Current.InstalledDir();
            bool upgrade = installed != null && Paths.Same(installed, dir);
            desktop = !upgrade || File.Exists(InstallEngine.DesktopLink(Env.Current));
            startMenu = !upgrade || Directory.Exists(InstallEngine.MenuFolder(Env.Current));
        }

        static int SilentInstall(Payload payload, bool showResult)
        {
            string dir = DefaultDir();
            ShortcutDefaults(dir, out bool desktop, out bool startMenu);
            using (var reporter = new Reporter("Setup"))
            {
                try
                {
                    new InstallEngine(payload, reporter).Run(dir, desktop, startMenu);
                    if (showResult) Message(Strings.T("dlg.installed", Strings.AppName, dir), MessageBoxImage.Information);
                    return 0;
                }
                catch (Exception e)
                {
                    reporter.Log(Strings.T("log.failed", Shell.Describe(e)));
                    if (showResult) Message(Strings.T("dlg.failed", Shell.Describe(e), reporter.LogPath), MessageBoxImage.Error);
                    return 1;
                }
            }
        }

        // ---------------- 卸载 ----------------

        static int Uninstall()
        {
            string dir = Opt.Dir;
            if (dir == null)
            {
                string here = Path.GetDirectoryName(SelfPath);
                dir = File.Exists(Path.Combine(here, AppInfo.ExeName)) ? here : Env.Current.InstalledDir();
            }
            if (dir == null || !Directory.Exists(dir))
            {
                if (!Opt.Silent) Message(Strings.T("dlg.notInstalled", Strings.AppName), MessageBoxImage.Warning);
                return 1;
            }
            Opt.Dir = dir = Path.GetFullPath(dir);
            // 当前目录不能留在安装目录里（在资源管理器里双击 Uninstall.exe、从软件里点「卸载」时就是），
            // 不然临时目录里那份和提权后的都继承它，最后删不掉安装目录本身
            Directory.SetCurrentDirectory(Path.GetTempPath());

            if (!Opt.FromTemp)
            {
                // 复制到临时目录再跑：安装目录（包括 Uninstall.exe 自己）才删得掉
                string temp = Path.Combine(Path.GetTempPath(), "GanlanTranslate-Uninstall-" + Guid.NewGuid().ToString("N").Substring(0, 8) + ".exe");
                File.Copy(SelfPath, temp);
                Process.Start(new ProcessStartInfo(temp, Opt.CommandLine("--from-temp")) { UseShellExecute = false });
                return 0;
            }

            DeleteAfterExit.Add(SelfPath);
            if (NeedElevation()) return RelaunchElevated();
            if (Opt.Silent) return SilentUninstall(dir, Opt.RemoveData, false);
            return RunWindow(new SetupController(null, dir), () =>
            {
                var choice = MessageBox.Show(Strings.T("dlg.noWebView2Uninstall"), Strings.T("title.uninstall", Strings.AppName),
                    MessageBoxButton.YesNo, MessageBoxImage.Warning);
                return choice == MessageBoxResult.Yes ? SilentUninstall(dir, false, true) : 1;
            });
        }

        static int SilentUninstall(string dir, bool removeData, bool showResult)
        {
            using (var reporter = new Reporter("Uninstall"))
            {
                try
                {
                    new UninstallEngine(reporter).Run(dir, removeData);
                    if (showResult) Message(Strings.T("dlg.uninstalled", Strings.AppName), MessageBoxImage.Information);
                    return 0;
                }
                catch (Exception e)
                {
                    reporter.Log(Strings.T("log.failed", Shell.Describe(e)));
                    if (showResult) Message(Strings.T("dlg.failed", Shell.Describe(e), reporter.LogPath), MessageBoxImage.Error);
                    return 1;
                }
            }
        }

        // ---------------- 权限 ----------------

        static bool NeedElevation() => !Env.Current.Sandbox && !Env.IsAdmin();

        static int RelaunchElevated()
        {
            if (!Opt.Elevated)
            {
                try
                {
                    using (Process p = Process.Start(new ProcessStartInfo(SelfPath, Opt.FromTemp ? Opt.CommandLine("--elevated", "--from-temp") : Opt.CommandLine("--elevated"))
                    {
                        UseShellExecute = true,
                        Verb = "runas",
                    }))
                    {
                        // 等它结束：静默安装时调用方拿得到结果；卸载时它结束了才能删临时目录里的这个 exe
                        p.WaitForExit();
                        return p.ExitCode;
                    }
                }
                catch (Win32Exception)
                {
                    // 用户在 UAC 里点了「否」
                }
            }
            if (!Opt.Silent) Message(Strings.T("dlg.needAdmin"), MessageBoxImage.Warning);
            return 1;
        }

        // ---------------- 窗口 ----------------

        static int RunWindow(SetupController controller, Func<int> noWebView)
        {
            string temp = Path.Combine(Path.GetTempPath(), "GanlanSetup-" + Process.GetCurrentProcess().Id);
            DeleteAfterExit.Add(temp);
            WebViewLoader.Extract(temp);
            if (!WebViewLoader.RuntimeAvailable()) return noWebView();
            return RunApp(controller, Path.Combine(temp, "WebView2"));
        }

        // 用到 WebView2 的代码放在单独的方法里：WebView2 的 DLL 解压好以后才 JIT
        [MethodImpl(MethodImplOptions.NoInlining)]
        static int RunApp(SetupController controller, string userDataFolder)
        {
            var app = new Application { ShutdownMode = ShutdownMode.OnMainWindowClose };
            var window = new HostWindow(controller, userDataFolder, Opt.DevTools);
            app.Run(window);
            return controller.ExitCode;
        }

        public static void Message(string text, MessageBoxImage icon)
        {
            string title = Strings.T(_uninstallMode ? "title.uninstall" : "title.setup", Strings.AppName);
            MessageBox.Show(text, title, MessageBoxButton.OK, icon);
        }

        /// <summary>WebView2 的数据目录、临时目录里的卸载程序：退出后删掉（WebView2 的进程还要一会儿才退）。</summary>
        static void CleanupAfterExit()
        {
            var left = new List<string>();
            foreach (string path in DeleteAfterExit.Where(p => !string.Equals(p, SelfPath, StringComparison.OrdinalIgnoreCase)))
            {
                bool gone = false;
                for (int i = 0; i < 15 && !gone; i++)
                {
                    try
                    {
                        if (Directory.Exists(path)) Directory.Delete(path, true);
                        gone = true;
                    }
                    catch (Exception)
                    {
                        Thread.Sleep(200);
                    }
                }
                if (!gone) left.Add(path);
            }
            bool deleteSelf = DeleteAfterExit.Any(p => string.Equals(p, SelfPath, StringComparison.OrdinalIgnoreCase));
            if (left.Count == 0 && !deleteSelf) return;
            var commands = new List<string> { "ping 127.0.0.1 -n 3 >nul" };
            commands.AddRange(left.Select(d => "rmdir /s /q " + Paths.Quote(d)));
            if (deleteSelf) commands.Add("del /f /q " + Paths.Quote(SelfPath));
            try
            {
                Process.Start(new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, "cmd.exe"), "/c " + string.Join(" & ", commands))
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WorkingDirectory = Path.GetTempPath(),
                });
            }
            catch (Exception)
            {
            }
        }
    }
}
