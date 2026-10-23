using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;

namespace GanlanSetup
{
    /// <summary>用到的 Windows 接口：解压（cabinet.dll）、查进程路径、快捷方式、选文件夹、窗口。</summary>
    static class Native
    {
        // ---------- 解压：Windows 自带的 Compression API，LZMS ----------
        public const uint COMPRESS_ALGORITHM_LZMS = 5;

        [DllImport("cabinet.dll", SetLastError = true)]
        public static extern bool CreateDecompressor(uint algorithm, IntPtr allocationRoutines, out IntPtr handle);

        [DllImport("cabinet.dll", SetLastError = true)]
        public static extern bool Decompress(IntPtr handle, byte[] compressed, UIntPtr compressedSize,
                                             byte[] uncompressed, UIntPtr uncompressedSize, out UIntPtr written);

        [DllImport("cabinet.dll")]
        public static extern bool CloseDecompressor(IntPtr handle);

        // ---------- 进程 ----------
        const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;

        [DllImport("kernel32.dll", SetLastError = true)]
        static extern IntPtr OpenProcess(uint access, bool inherit, int pid);

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        static extern bool QueryFullProcessImageNameW(IntPtr process, uint flags, StringBuilder name, ref uint size);

        [DllImport("kernel32.dll")]
        static extern bool CloseHandle(IntPtr handle);

        /// <summary>进程的 exe 完整路径；32 / 64 位、别的用户的进程都能查（查不到返回 null）。</summary>
        public static string ProcessPath(int pid)
        {
            IntPtr h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid);
            if (h == IntPtr.Zero) return null;
            try
            {
                var sb = new StringBuilder(1024);
                uint size = (uint)sb.Capacity;
                return QueryFullProcessImageNameW(h, 0, sb, ref size) ? sb.ToString(0, (int)size) : null;
            }
            finally
            {
                CloseHandle(h);
            }
        }

        // ---------- 文件 ----------
        public const uint MOVEFILE_DELAY_UNTIL_REBOOT = 4;

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern bool MoveFileExW(string existing, string newName, uint flags);

        // ---------- 谁占着文件：重启管理器 ----------
        [StructLayout(LayoutKind.Sequential)]
        struct RM_UNIQUE_PROCESS
        {
            public int dwProcessId;
            public System.Runtime.InteropServices.ComTypes.FILETIME ProcessStartTime;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        struct RM_PROCESS_INFO
        {
            public RM_UNIQUE_PROCESS Process;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)] public string strAppName;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)] public string strServiceShortName;
            public int ApplicationType;
            public uint AppStatus;
            public uint TSSessionId;
            [MarshalAs(UnmanagedType.Bool)] public bool bRestartable;
        }

        const int ERROR_MORE_DATA = 234;

        [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)]
        static extern int RmStartSession(out uint session, int flags, StringBuilder sessionKey);

        [DllImport("rstrtmgr.dll")]
        static extern int RmEndSession(uint session);

        [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)]
        static extern int RmRegisterResources(uint session, uint fileCount, string[] files, uint appCount, IntPtr apps, uint serviceCount, IntPtr services);

        [DllImport("rstrtmgr.dll")]
        static extern int RmGetList(uint session, out uint needed, ref uint count, [In, Out] RM_PROCESS_INFO[] infos, out uint rebootReasons);

        /// <summary>
        /// 占着这个文件的程序，写成「显示名（进程名.exe）」。不只是甘岚翻译自己：
        /// 比如游戏的反作弊扫过甘岚翻译加载的 DLL 后会一直开着它，游戏不退就不放。查不到返回空列表。
        /// </summary>
        public static System.Collections.Generic.List<string> FileLockers(string path)
        {
            var names = new System.Collections.Generic.List<string>();
            if (RmStartSession(out uint session, 0, new StringBuilder(64)) != 0) return names;
            try
            {
                if (RmRegisterResources(session, 1, new[] { path }, 0, IntPtr.Zero, 0, IntPtr.Zero) != 0) return names;
                uint count = 0;
                RM_PROCESS_INFO[] infos = null;
                for (int attempt = 0; ; attempt++)
                {
                    int rc = RmGetList(session, out uint needed, ref count, infos, out _);
                    if (rc == 0) break;
                    if (rc != ERROR_MORE_DATA || attempt >= 3) return names;
                    infos = new RM_PROCESS_INFO[needed];
                    count = needed;
                }
                for (int i = 0; infos != null && i < count; i++)
                {
                    string exe = null;
                    try
                    {
                        using (var p = System.Diagnostics.Process.GetProcessById(infos[i].Process.dwProcessId))
                            exe = p.ProcessName + ".exe";
                    }
                    catch (Exception)
                    {
                    }
                    string app = (infos[i].strAppName ?? "").Trim();
                    string name = app.Length == 0 ? exe : exe == null || string.Equals(app, exe, StringComparison.OrdinalIgnoreCase) ? app : app + " (" + exe + ")";
                    if (name != null && !names.Contains(name)) names.Add(name);
                }
            }
            finally
            {
                RmEndSession(session);
            }
            return names;
        }

        // ---------- 窗口 ----------
        public const int WM_NCLBUTTONDOWN = 0xA1;
        public const int HTCAPTION = 2;
        public const int DWMWA_WINDOW_CORNER_PREFERENCE = 33;
        public const int DWMWCP_ROUND = 2;

        [DllImport("user32.dll")]
        public static extern bool ReleaseCapture();

        [DllImport("user32.dll")]
        public static extern IntPtr SendMessageW(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam);

        public const int WM_SIZE = 0x0005;

        [DllImport("dwmapi.dll")]
        public static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);

        [StructLayout(LayoutKind.Sequential)]
        public struct RECT { public int Left, Top, Right, Bottom; }

        [DllImport("user32.dll")]
        public static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);

        [DllImport("user32.dll")]
        public static extern uint GetDpiForWindow(IntPtr hwnd);

        [DllImport("gdi32.dll")]
        static extern IntPtr CreateRoundRectRgn(int left, int top, int right, int bottom, int widthEllipse, int heightEllipse);

        [DllImport("user32.dll")]
        static extern int SetWindowRgn(IntPtr hwnd, IntPtr region, bool redraw);

        /// <summary>Win11 的系统自己给窗口画圆角和边框；Win10 要自己用 SetWindowRgn 裁。</summary>
        public static bool IsWindows11 => Environment.OSVersion.Version.Build >= 22000;

        /// <summary>把窗口裁成圆角矩形（radius 是 96 DPI 下的像素，按窗口当前 DPI 换算）。</summary>
        public static void RoundWindow(IntPtr hwnd, double radius)
        {
            if (hwnd == IntPtr.Zero || !GetWindowRect(hwnd, out RECT r)) return;
            uint dpi = 96;
            try { dpi = GetDpiForWindow(hwnd); }
            catch (EntryPointNotFoundException) { }
            int d = (int)Math.Round(radius * 2 * dpi / 96.0);
            // 区域交给系统后由系统释放，不用 DeleteObject
            SetWindowRgn(hwnd, CreateRoundRectRgn(0, 0, r.Right - r.Left + 1, r.Bottom - r.Top + 1, d, d), true);
        }

        [DllImport("kernel32.dll")]
        public static extern ushort GetUserDefaultUILanguage();

        // ---------- 快捷方式（IShellLinkW） ----------
        [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
        class ShellLink { }

        [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("000214F9-0000-0000-C000-000000000046")]
        interface IShellLinkW
        {
            void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder file, int cch, IntPtr findData, uint flags);
            void GetIDList(out IntPtr pidl);
            void SetIDList(IntPtr pidl);
            void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder name, int cch);
            void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string name);
            void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder dir, int cch);
            void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string dir);
            void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder args, int cch);
            void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string args);
            void GetHotkey(out short hotkey);
            void SetHotkey(short hotkey);
            void GetShowCmd(out int showCmd);
            void SetShowCmd(int showCmd);
            void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int cch, out int index);
            void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string path, int index);
            void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string path, uint reserved);
            void Resolve(IntPtr hwnd, uint flags);
            void SetPath([MarshalAs(UnmanagedType.LPWStr)] string file);
        }

        public static void CreateShortcut(string lnkPath, string target, string description)
        {
            var link = (IShellLinkW)new ShellLink();
            try
            {
                link.SetPath(target);
                link.SetWorkingDirectory(Path.GetDirectoryName(target));
                link.SetIconLocation(target, 0);
                link.SetDescription(description);
                ((IPersistFile)link).Save(lnkPath, true);
            }
            finally
            {
                Marshal.ReleaseComObject(link);
            }
        }

        // ---------- 选文件夹（系统的新式对话框） ----------
        [ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")]
        class FileOpenDialog { }

        [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("d57c7288-d4ad-4768-be02-9d969532d960")]
        interface IFileOpenDialog
        {
            [PreserveSig] int Show(IntPtr parent);
            void SetFileTypes(uint count, IntPtr filterSpec);
            void SetFileTypeIndex(uint index);
            void GetFileTypeIndex(out uint index);
            void Advise(IntPtr events, out uint cookie);
            void Unadvise(uint cookie);
            void SetOptions(uint options);
            void GetOptions(out uint options);
            void SetDefaultFolder(IShellItem item);
            void SetFolder(IShellItem item);
            void GetFolder(out IShellItem item);
            void GetCurrentSelection(out IShellItem item);
            void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
            void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string name);
            void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
            void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string text);
            void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
            void GetResult(out IShellItem item);
            void AddPlace(IShellItem item, int place);
            void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string extension);
            void Close(int hr);
            void SetClientGuid(ref Guid guid);
            void ClearClientData();
            void SetFilter(IntPtr filter);
            void GetResults(out IntPtr items);
            void GetSelectedItems(out IntPtr items);
        }

        [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE")]
        interface IShellItem
        {
            void BindToHandler(IntPtr bindCtx, ref Guid handler, ref Guid riid, out IntPtr result);
            void GetParent(out IShellItem parent);
            void GetDisplayName(uint sigdn, out IntPtr name);
            void GetAttributes(uint mask, out uint attributes);
            void Compare(IShellItem other, uint hint, out int order);
        }

        [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
        static extern void SHCreateItemFromParsingName(string path, IntPtr bindCtx, [In] ref Guid riid, out IShellItem item);

        const uint FOS_PICKFOLDERS = 0x20;
        const uint FOS_FORCEFILESYSTEM = 0x40;
        const uint FOS_PATHMUSTEXIST = 0x800;
        const uint SIGDN_FILESYSPATH = 0x80058000;

        /// <summary>弹出系统的选文件夹对话框；取消返回 null。</summary>
        public static string PickFolder(IntPtr owner, string title, string startDir)
        {
            var dialog = (IFileOpenDialog)new FileOpenDialog();
            try
            {
                dialog.SetOptions(FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST);
                if (!string.IsNullOrEmpty(title)) dialog.SetTitle(title);
                string start = ExistingParent(startDir);
                if (start != null)
                {
                    try
                    {
                        Guid iid = typeof(IShellItem).GUID;
                        SHCreateItemFromParsingName(start, IntPtr.Zero, ref iid, out IShellItem folder);
                        dialog.SetFolder(folder);
                    }
                    catch (Exception)
                    {
                        // 起始文件夹设不上不要紧
                    }
                }
                if (dialog.Show(owner) != 0) return null;
                dialog.GetResult(out IShellItem item);
                item.GetDisplayName(SIGDN_FILESYSPATH, out IntPtr ptr);
                try
                {
                    return Marshal.PtrToStringUni(ptr);
                }
                finally
                {
                    Marshal.FreeCoTaskMem(ptr);
                }
            }
            finally
            {
                Marshal.ReleaseComObject(dialog);
            }
        }

        static string ExistingParent(string dir)
        {
            try
            {
                while (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir)) dir = Path.GetDirectoryName(dir);
                return string.IsNullOrEmpty(dir) ? null : dir;
            }
            catch (Exception)
            {
                return null;
            }
        }
    }
}
