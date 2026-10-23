using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Shell;
using System.Windows.Threading;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;

namespace GanlanSetup
{
    /// <summary>WebView2 的 DLL 打在 exe 资源里：解压到临时目录，再让 .NET 从那里加载。</summary>
    static class WebViewLoader
    {
        static readonly string[] Files = { "Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.Wpf.dll", "WebView2Loader.dll" };
        static string _dir;

        public static void Extract(string dir)
        {
            Directory.CreateDirectory(dir);
            foreach (string name in Files)
            {
                using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream(name))
                using (var f = new FileStream(Path.Combine(dir, name), FileMode.Create, FileAccess.Write))
                    s.CopyTo(f);
            }
            _dir = dir;
            AppDomain.CurrentDomain.AssemblyResolve += (sender, e) =>
            {
                string path = Path.Combine(_dir, new AssemblyName(e.Name).Name + ".dll");
                return File.Exists(path) ? Assembly.LoadFrom(path) : null;
            };
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        public static bool RuntimeAvailable()
        {
            try
            {
                CoreWebView2Environment.SetLoaderDllFolderPath(_dir);
                return !string.IsNullOrEmpty(CoreWebView2Environment.GetAvailableBrowserVersionString());
            }
            catch (Exception)
            {
                return false;
            }
        }
    }

    /// <summary>安装界面的网页文件（build.py 打成 ui.zip 放进 exe 资源）。</summary>
    static class UiFiles
    {
        static Dictionary<string, byte[]> _files;

        public static byte[] Get(string path)
        {
            if (_files == null)
            {
                _files = new Dictionary<string, byte[]>(StringComparer.OrdinalIgnoreCase);
                using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream("ui.zip"))
                using (var zip = new ZipArchive(s, ZipArchiveMode.Read))
                {
                    foreach (ZipArchiveEntry entry in zip.Entries)
                    {
                        if (entry.FullName.EndsWith("/")) continue;
                        using (Stream es = entry.Open())
                        using (var ms = new MemoryStream())
                        {
                            es.CopyTo(ms);
                            _files[entry.FullName] = ms.ToArray();
                        }
                    }
                }
            }
            return _files.TryGetValue(path.Replace('\\', '/'), out byte[] data) ? data : null;
        }

        public static string Mime(string path)
        {
            switch (Path.GetExtension(path).ToLowerInvariant())
            {
                case ".html": return "text/html; charset=utf-8";
                case ".css": return "text/css; charset=utf-8";
                case ".js": case ".mjs": return "text/javascript; charset=utf-8";
                case ".json": return "application/json; charset=utf-8";
                case ".txt": case ".md": return "text/plain; charset=utf-8";
                case ".svg": return "image/svg+xml";
                case ".png": case ".apng": return "image/png";
                case ".jpg": case ".jpeg": return "image/jpeg";
                case ".gif": return "image/gif";
                case ".webp": return "image/webp";
                case ".avif": return "image/avif";
                case ".ico": return "image/x-icon";
                case ".mp4": return "video/mp4";
                case ".webm": return "video/webm";
                case ".ogg": return "video/ogg";
                case ".mp3": return "audio/mpeg";
                case ".wav": return "audio/wav";
                case ".woff": return "font/woff";
                case ".woff2": return "font/woff2";
                case ".ttf": return "font/ttf";
                default: return "application/octet-stream";
            }
        }
    }

    static class Json
    {
        static readonly JavaScriptSerializer Serializer = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };

        public static string Serialize(object value) => Serializer.Serialize(value);

        public static Dictionary<string, object> Parse(string json) =>
            Serializer.DeserializeObject(json) as Dictionary<string, object> ?? new Dictionary<string, object>();
    }

    /// <summary>
    /// 安装程序的窗口：没有系统标题栏，整个窗口铺一个 WebView2，界面全在网页里（installer/ui）。
    /// 网页从 https://setup.ganlan/ 加载，请求在这里拦下来直接从 ui.zip 返回，不起本地服务器。
    /// </summary>
    sealed class HostWindow : Window, IHost
    {
        public const string Origin = "https://setup.ganlan/";
        public const double PageWidth = 760, PageHeight = 540;
        static readonly System.Drawing.Color PageColor = System.Drawing.Color.FromArgb(255, 0xFF, 0xFF, 0xFF);

        readonly SetupController _controller;
        readonly WebView2 _web;
        readonly bool _devTools;
        bool _revealed;

        /// <summary>窗口圆角（96 DPI 下的像素，和 setup.css 里 Win10 边框的圆角一样）。</summary>
        const double CornerRadiusPx = 8;

        public HostWindow(SetupController controller, string userDataFolder, bool devTools)
        {
            _controller = controller;
            _devTools = devTools;
            controller.Window = this;

            Title = Strings.T(controller.IsUninstall ? "title.uninstall" : "title.setup", Strings.AppName);
            Width = PageWidth;
            Height = PageHeight;
            ResizeMode = ResizeMode.CanMinimize;
            WindowStyle = WindowStyle.None;
            // 网页加载完再挪到屏幕中间，免得先闪一下空白
            WindowStartupLocation = WindowStartupLocation.Manual;
            Left = -32000;
            Top = -32000;
            Background = new SolidColorBrush(Color.FromRgb(PageColor.R, PageColor.G, PageColor.B));
            WindowChrome.SetWindowChrome(this, new WindowChrome
            {
                CaptionHeight = 0,
                ResizeBorderThickness = new Thickness(0),
                GlassFrameThickness = new Thickness(0, 0, 0, 1),   // 让系统画窗口阴影
                CornerRadius = new CornerRadius(0),
                UseAeroCaptionButtons = false,
            });

            _web = new WebView2
            {
                DefaultBackgroundColor = PageColor,
                CreationProperties = new CoreWebView2CreationProperties
                {
                    UserDataFolder = userDataFolder,
                    Language = Strings.Lang,
                },
            };
            Content = _web;

            // 圆角：Win11 让系统画（有抗锯齿和阴影）；Win10 用 SetWindowRgn 裁，大小或 DPI 变了重新裁，1px 边框网页里画
            SourceInitialized += (s, e) =>
            {
                IntPtr hwnd = new WindowInteropHelper(this).Handle;
                if (Native.IsWindows11)
                {
                    int round = Native.DWMWCP_ROUND;
                    Native.DwmSetWindowAttribute(hwnd, Native.DWMWA_WINDOW_CORNER_PREFERENCE, ref round, sizeof(int));
                    return;
                }
                Native.RoundWindow(hwnd, CornerRadiusPx);
                HwndSource.FromHwnd(hwnd).AddHook((IntPtr h, int msg, IntPtr w, IntPtr l, ref bool handled) =>
                {
                    if (msg == Native.WM_SIZE) Native.RoundWindow(h, CornerRadiusPx);
                    return IntPtr.Zero;
                });
            };
            Loaded += OnLoaded;
            Closing += (s, e) =>
            {
                if (!_controller.CanClose)
                {
                    e.Cancel = true;
                    _controller.RequestClose();
                }
            };
            Closed += (s, e) => _web.Dispose();

            // 网页一直没加载出来也要露面（最多等 3 秒）
            var timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(3) };
            timer.Tick += (s, e) =>
            {
                timer.Stop();
                Reveal();
            };
            timer.Start();
        }

        async void OnLoaded(object sender, RoutedEventArgs e)
        {
            try
            {
                await _web.EnsureCoreWebView2Async();
            }
            catch (Exception ex)
            {
                Reveal();
                MessageBox.Show(this, ex.Message, Title, MessageBoxButton.OK, MessageBoxImage.Error);
                _controller.ForceClose();
                return;
            }

            CoreWebView2 core = _web.CoreWebView2;
            CoreWebView2Settings settings = core.Settings;
            settings.AreDevToolsEnabled = _devTools;
            settings.AreDefaultContextMenusEnabled = _devTools;
            settings.AreBrowserAcceleratorKeysEnabled = _devTools;
            settings.IsZoomControlEnabled = false;
            settings.IsStatusBarEnabled = false;
            settings.IsGeneralAutofillEnabled = false;
            settings.IsPasswordAutosaveEnabled = false;
            TrySet(() => settings.IsPinchZoomEnabled = false);
            TrySet(() => settings.IsSwipeNavigationEnabled = false);
            // 网页里标了 app-region: drag 的地方（页首）当标题栏用：拖动窗口、右键系统菜单
            NativeDrag = TrySet(() => settings.IsNonClientRegionSupportEnabled = true);

            core.AddWebResourceRequestedFilter(Origin + "*", CoreWebView2WebResourceContext.All);
            core.WebResourceRequested += OnResource;
            core.WebMessageReceived += OnMessage;
            core.NewWindowRequested += (s, a) =>
            {
                a.Handled = true;
                Shell.OpenUrl(a.Uri);
            };
            core.NavigationStarting += (s, a) =>
            {
                if (a.Uri.StartsWith(Origin, StringComparison.OrdinalIgnoreCase)) return;
                a.Cancel = true;
                Shell.OpenUrl(a.Uri);
            };
            core.NavigationCompleted += (s, a) => Reveal();
            core.Navigate(Origin + "setup.html");
        }

        public bool NativeDrag { get; private set; }

        static bool TrySet(Action set)
        {
            try
            {
                set();
                return true;
            }
            catch (Exception)
            {
                return false;   // 运行库太旧没有这个设置
            }
        }

        void Reveal()
        {
            if (_revealed) return;
            _revealed = true;
            Rect area = SystemParameters.WorkArea;
            Left = area.Left + Math.Max(0, (area.Width - Width) / 2);
            Top = area.Top + Math.Max(0, (area.Height - Height) / 2);
            Activate();
        }

        void OnResource(object sender, CoreWebView2WebResourceRequestedEventArgs e)
        {
            CoreWebView2Environment env = _web.CoreWebView2.Environment;
            string path = Uri.UnescapeDataString(new Uri(e.Request.Uri).AbsolutePath).TrimStart('/');
            if (path.Length == 0) path = "setup.html";
            byte[] data = UiFiles.Get(path);
            e.Response = data == null
                ? env.CreateWebResourceResponse(null, 404, "Not Found", "Content-Type: text/plain")
                : env.CreateWebResourceResponse(new MemoryStream(data), 200, "OK",
                    "Content-Type: " + UiFiles.Mime(path) + "\r\nCache-Control: no-store");
        }

        void OnMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            Dictionary<string, object> message;
            try
            {
                message = Json.Parse(e.WebMessageAsJson);
            }
            catch (Exception)
            {
                return;
            }
            _controller.OnMessage(message);
        }

        /// <summary>发消息给网页（任何线程都可以调）。</summary>
        public void Send(object message)
        {
            string json = Json.Serialize(message);
            Dispatcher.BeginInvoke(new Action(() => _web.CoreWebView2?.PostWebMessageAsJson(json)));
        }

        public IntPtr Handle => new WindowInteropHelper(this).Handle;

        /// <summary>运行库不支持 app-region 时的退路：网页按下页首时发 drag，这里交给系统拖动。</summary>
        public void DragMoveFromPage()
        {
            Native.ReleaseCapture();
            Native.SendMessageW(Handle, Native.WM_NCLBUTTONDOWN, (IntPtr)Native.HTCAPTION, IntPtr.Zero);
        }
    }
}
