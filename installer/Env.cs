using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Principal;
using Microsoft.Win32;

namespace GanlanSetup
{
    /// <summary>和 NSIS 版安装包一致的名字（升级时要认得出旧的安装）。</summary>
    static class AppInfo
    {
        public const string Key = "GanlanTranslate";            // 注册表 Software\ 和 Uninstall\ 下的键名
        public const string DirName = "Ganlan Translate";       // Program Files 下的文件夹、开始菜单文件夹
        public const string DisplayName = "甘岚翻译";            // 「应用和功能」、快捷方式的名字（不跟界面语言变）
        public const string Publisher = "甘岚";
        public const string Homepage = "https://github.com/ganlanqwq/GanlanTranslate";
        public const string ExeName = "GanlanTranslate.exe";
        public const string VrExeName = "GanlanTranslateVR.exe";
        public const string UninstallerName = "Uninstall.exe";
        public const string ManifestName = "install-manifest.txt";
        public const string LogName = "install.log";

        // 改英文名之前的测试版（2.x）：GanLan.exe，装在 GanLan 目录，注册表键 GanLan
        public const string OldKey = "GanLan";
        public const string OldExeName = "GanLan.exe";
        public const string OldVrExeName = "GanLanVR.exe";

        public const string UninstallKey = @"Microsoft\Windows\CurrentVersion\Uninstall\" + Key;
        public const string OldUninstallKey = @"Microsoft\Windows\CurrentVersion\Uninstall\" + OldKey;

        /// <summary>卸载时默认留下的用户数据（相对安装目录）。</summary>
        public static readonly string[] UserData =
        {
            "presets.json", "plugins", "hot_words_private", "terminology_private",
            "models", "local_asr_models", @"vr_overlay\settings.json",
        };

        /// <summary>程序运行时自己生成、不算用户数据的文件，卸载时一起删。</summary>
        public static readonly string[] Generated =
        {
            UninstallerName, ManifestName, LogName, @"vr_overlay\ganlan_vr.vrmanifest", @"vr_overlay\overlay.log",
        };
    }

    /// <summary>
    /// 注册表、桌面、开始菜单、默认安装目录的位置。
    /// 设了环境变量 GANLAN_SETUP_SANDBOX=目录 时全部换到沙盒里（测试用：不提权，不碰本机已装的那份）。
    /// </summary>
    sealed class Env
    {
        public static readonly Env Current = new Env();

        public readonly string SandboxRoot;
        public bool Sandbox => SandboxRoot != null;

        Env()
        {
            string root = Environment.GetEnvironmentVariable("GANLAN_SETUP_SANDBOX");
            if (!string.IsNullOrWhiteSpace(root)) SandboxRoot = Path.GetFullPath(root.Trim());
        }

        public static bool IsAdmin()
        {
            using (var id = WindowsIdentity.GetCurrent())
                return new WindowsPrincipal(id).IsInRole(WindowsBuiltInRole.Administrator);
        }

        /// <summary>HKLM\SOFTWARE 的 32 位视图（NSIS 是 32 位程序，以前的键都在 WOW6432Node 下）。</summary>
        RegistryKey SoftwareRoot(bool writable)
        {
            // 每个沙盒目录各用一份（不然换个沙盒目录测试会把上一个沙盒当成已安装的版本）
            if (Sandbox)
                return Registry.CurrentUser.CreateSubKey(@"Software\GanlanSetupSandbox\"
                    + SandboxRoot.ToLowerInvariant().GetHashCode().ToString("x8") + @"\SOFTWARE");
            var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry32);
            return hklm.OpenSubKey("SOFTWARE", writable);
        }

        public string ReadString(string subKey, string name)
        {
            using (var root = SoftwareRoot(false))
            using (var key = root?.OpenSubKey(subKey))
                return key?.GetValue(name) as string;
        }

        public RegistryKey CreateKey(string subKey)
        {
            using (var root = SoftwareRoot(true))
                return root.CreateSubKey(subKey);
        }

        public void DeleteKey(string subKey)
        {
            using (var root = SoftwareRoot(true))
                root?.DeleteSubKeyTree(subKey, false);
        }

        string SandboxDir(string name)
        {
            string dir = Path.Combine(SandboxRoot, name);
            Directory.CreateDirectory(dir);
            return dir;
        }

        // NSIS 默认 SetShellVarContext current：快捷方式在当前用户的桌面和开始菜单
        public string UserDesktop => Sandbox ? SandboxDir("Desktop") : Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
        public string UserPrograms => Sandbox ? SandboxDir("StartMenu") : Environment.GetFolderPath(Environment.SpecialFolder.Programs);
        public string CommonDesktop => Sandbox ? SandboxDir("CommonDesktop") : Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory);
        public string CommonPrograms => Sandbox ? SandboxDir("CommonStartMenu") : Environment.GetFolderPath(Environment.SpecialFolder.CommonPrograms);

        public string DefaultInstallDir => Sandbox
            ? Path.Combine(SandboxRoot, "Program")
            : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), AppInfo.DirName);

        /// <summary>已经装过的目录（注册表里记的，且目录里有主程序）。</summary>
        public string InstalledDir()
        {
            string dir = ReadString(AppInfo.Key, "InstallDir");
            if (string.IsNullOrWhiteSpace(dir)) return null;
            try
            {
                dir = Paths.Normalize(dir);
                return File.Exists(Path.Combine(dir, AppInfo.ExeName)) ? dir : null;
            }
            catch (Exception)
            {
                return null;
            }
        }

        public string InstalledVersion() => ReadString(AppInfo.UninstallKey, "DisplayVersion");
    }

    static class Paths
    {
        public static string Normalize(string path)
        {
            string full = Path.GetFullPath(path.Trim().Trim('"'));
            string root = Path.GetPathRoot(full);
            return full.Length > root.Length ? full.TrimEnd('\\', '/') : full;
        }

        public static bool Same(string a, string b) =>
            string.Equals(Normalize(a), Normalize(b), StringComparison.OrdinalIgnoreCase);

        /// <summary>path 在 dir 里面（不含 dir 本身）。</summary>
        public static bool IsInside(string path, string dir)
        {
            string d = Normalize(dir).TrimEnd('\\') + "\\";
            return Path.GetFullPath(path).StartsWith(d, StringComparison.OrdinalIgnoreCase);
        }

        /// <summary>安装包里的相对路径 → 安装目录下的完整路径；越出安装目录的直接报错。</summary>
        public static string Under(string dir, string relative)
        {
            if (string.IsNullOrEmpty(relative) || Path.IsPathRooted(relative) || relative.Split('/', '\\').Contains(".."))
                throw new InvalidDataException("bad path: " + relative);
            string full = Path.GetFullPath(Path.Combine(dir, relative.Replace('/', '\\')));
            if (!IsInside(full, dir)) throw new InvalidDataException("bad path: " + relative);
            return full;
        }

        public static string Quote(string arg)
        {
            if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return arg;
            // CommandLineToArgvW 的规则：引号前的反斜杠要成对
            var sb = new System.Text.StringBuilder("\"");
            int slashes = 0;
            foreach (char c in arg)
            {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') sb.Append('\\', slashes * 2 + 1).Append('"');
                else sb.Append('\\', slashes).Append(c);
                slashes = 0;
            }
            return sb.Append('\\', slashes * 2).Append('"').ToString();
        }

        public static string FormatSize(long bytes)
        {
            if (bytes >= 1L << 30) return (bytes / (double)(1L << 30)).ToString("0.0") + " GB";
            return (bytes / (double)(1L << 20)).ToString("0") + " MB";
        }
    }

    /// <summary>
    /// 命令行：/S 静默；/D=目录（和 NSIS 一样放在最后，可以带空格）；--remove-data 卸载时连数据一起删；
    /// --lang 界面语言；--devtools 打开网页开发者工具。--elevated / --from-temp / --dir 是自己重启时用的。
    /// </summary>
    sealed class Options
    {
        public bool Silent, Elevated, FromTemp, RemoveData, DevTools;
        public string Dir, Lang;
        public readonly List<string> Passthrough = new List<string>();

        public static Options Parse(string[] args)
        {
            var o = new Options();
            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i];
                if (a.StartsWith("/D=", StringComparison.OrdinalIgnoreCase))
                {
                    o.Dir = string.Join(" ", new[] { a.Substring(3) }.Concat(args.Skip(i + 1)));
                    break;
                }
                switch (a.ToLowerInvariant())
                {
                    case "/s": o.Silent = true; o.Passthrough.Add(a); break;
                    case "--remove-data": o.RemoveData = true; o.Passthrough.Add(a); break;
                    case "--devtools": o.DevTools = true; o.Passthrough.Add(a); break;
                    case "--elevated": o.Elevated = true; break;
                    case "--from-temp": o.FromTemp = true; break;
                    case "--lang": if (i + 1 < args.Length) o.Lang = args[++i]; break;
                    case "--dir": if (i + 1 < args.Length) o.Dir = args[++i]; break;
                }
            }
            return o;
        }

        /// <summary>自己重启时的命令行（目录用 --dir 传，免得 /D= 的空格出问题）。</summary>
        public string CommandLine(params string[] extra)
        {
            var parts = new List<string>(Passthrough);
            if (Lang != null) { parts.Add("--lang"); parts.Add(Paths.Quote(Lang)); }
            if (Dir != null) { parts.Add("--dir"); parts.Add(Paths.Quote(Dir)); }
            parts.AddRange(extra);
            return string.Join(" ", parts);
        }
    }
}
