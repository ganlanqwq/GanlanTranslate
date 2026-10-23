using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace GanlanSetup
{
    /// <summary>
    /// 接在安装程序 exe 后面的安装数据（installer\build.py 生成，格式要和那边一致）：
    /// <code>
    /// exe = 外壳 | "GLPKG001" | 头长度 u32 | 头 | 头的 SHA-256 | 各个压缩块 | 安装数据起点 u64 | "GLSETUP1"
    /// 头 = 版本号 | 总大小 u64 | 块大小 u32 | 文件数 u32 | 文件 [路径, 大小 u64, 修改时间 i64 FILETIME]
    ///      | 块数 u32 | 块 [原始大小 u32, 压缩后大小 u32, 压缩后的 SHA-256]
    /// </code>
    /// 字符串是 u16 字节数 + UTF-8；整数都是小端。文件按路径排好首尾相接，切成块，每块单独用 LZMS 压缩。
    /// </summary>
    sealed class Payload
    {
        static readonly byte[] FooterMagic = Encoding.ASCII.GetBytes("GLSETUP1");
        static readonly byte[] HeaderMagic = Encoding.ASCII.GetBytes("GLPKG001");

        public sealed class Entry
        {
            public string Path;
            public long Size;
            public long FileTime;
        }

        sealed class Block
        {
            public int RawLength, CompressedLength;
            public long Offset;
            public byte[] Sha256;
        }

        public readonly string ExePath;
        public long StubLength { get; private set; }
        public string Version { get; private set; }
        public long TotalSize { get; private set; }
        public List<Entry> Files { get; } = new List<Entry>();
        readonly List<Block> _blocks = new List<Block>();

        Payload(string exePath)
        {
            ExePath = exePath;
        }

        /// <summary>读自己 exe 末尾的安装数据；没有（卸载程序）返回 null，有但坏了抛 CorruptPackageException。</summary>
        public static Payload TryOpen(string exePath)
        {
            using (var fs = new FileStream(exePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            {
                if (fs.Length < 16) return null;
                var footer = new byte[16];
                fs.Seek(-16, SeekOrigin.End);
                ReadExactly(fs, footer, 16);
                if (!footer.Skip(8).SequenceEqual(FooterMagic)) return null;

                var p = new Payload(exePath);
                p.StubLength = BitConverter.ToInt64(footer, 0);
                try
                {
                    p.ReadHeader(fs);
                }
                catch (Exception e) when (!(e is CorruptPackageException))
                {
                    throw new CorruptPackageException();
                }
                return p;
            }
        }

        void ReadHeader(FileStream fs)
        {
            if (StubLength <= 0 || StubLength >= fs.Length - 16) throw new CorruptPackageException();
            fs.Seek(StubLength, SeekOrigin.Begin);
            var r = new BinaryReader(fs, Encoding.UTF8, true);
            if (!r.ReadBytes(8).SequenceEqual(HeaderMagic)) throw new CorruptPackageException();
            int headerLength = r.ReadInt32();
            if (headerLength <= 0 || headerLength > 64 << 20) throw new CorruptPackageException();
            byte[] header = r.ReadBytes(headerLength);
            byte[] hash = r.ReadBytes(32);
            using (var sha = SHA256.Create())
                if (header.Length != headerLength || !sha.ComputeHash(header).SequenceEqual(hash)) throw new CorruptPackageException();

            var h = new BinaryReader(new MemoryStream(header), Encoding.UTF8);
            Version = ReadString(h);
            TotalSize = h.ReadInt64();
            h.ReadInt32();   // 块大小（只是记录一下，解压按每块自己的大小来）
            int fileCount = h.ReadInt32();
            for (int i = 0; i < fileCount; i++)
                Files.Add(new Entry { Path = ReadString(h), Size = h.ReadInt64(), FileTime = h.ReadInt64() });
            int blockCount = h.ReadInt32();
            long offset = fs.Position;
            for (int i = 0; i < blockCount; i++)
            {
                var b = new Block { RawLength = h.ReadInt32(), CompressedLength = h.ReadInt32(), Sha256 = h.ReadBytes(32), Offset = offset };
                offset += b.CompressedLength;
                _blocks.Add(b);
            }
            if (offset != fs.Length - 16 || Files.Sum(f => f.Size) != TotalSize || _blocks.Sum(b => (long)b.RawLength) != TotalSize)
                throw new CorruptPackageException();
        }

        static string ReadString(BinaryReader r)
        {
            int n = r.ReadUInt16();
            return Encoding.UTF8.GetString(r.ReadBytes(n));
        }

        static void ReadExactly(Stream s, byte[] buffer, int count)
        {
            int read = 0;
            while (read < count)
            {
                int n = s.Read(buffer, read, count - read);
                if (n <= 0) throw new CorruptPackageException();
                read += n;
            }
        }

        FileStream OpenRead() =>
            new FileStream(ExePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 1 << 20);

        byte[] ReadBlock(FileStream fs, Block b)
        {
            var data = new byte[b.CompressedLength];
            fs.Seek(b.Offset, SeekOrigin.Begin);
            ReadExactly(fs, data, data.Length);
            return data;
        }

        /// <summary>核对每一块的 SHA-256；不对就抛 CorruptPackageException（这时还没碰安装目录）。</summary>
        public void Verify(Action<double> progress)
        {
            using (var fs = OpenRead())
            using (var sha = SHA256.Create())
            {
                for (int i = 0; i < _blocks.Count; i++)
                {
                    if (!sha.ComputeHash(ReadBlock(fs, _blocks[i])).SequenceEqual(_blocks[i].Sha256)) throw new CorruptPackageException();
                    progress((i + 1) / (double)_blocks.Count);
                }
            }
        }

        static byte[] Decompress(byte[] compressed, int rawLength)
        {
            if (!Native.CreateDecompressor(Native.COMPRESS_ALGORITHM_LZMS, IntPtr.Zero, out IntPtr handle))
                throw new Win32Exception();
            try
            {
                var raw = new byte[rawLength];
                if (!Native.Decompress(handle, compressed, (UIntPtr)compressed.Length, raw, (UIntPtr)raw.Length, out UIntPtr written)
                    || (long)written.ToUInt64() != rawLength)
                    throw new CorruptPackageException();
                return raw;
            }
            finally
            {
                Native.CloseDecompressor(handle);
            }
        }

        /// <summary>
        /// 解压到 dir。几块同时解压（最多 4 块在内存里），这个线程按顺序写文件。
        /// onFile：开始写一个文件；onBytes：累计写了多少字节。
        /// </summary>
        public void Extract(string dir, Action<Entry> onFile, Action<long> onBytes)
        {
            int parallel = Math.Max(1, Math.Min(4, Environment.ProcessorCount - 1));
            var tasks = new Task<byte[]>[_blocks.Count];
            var writer = new FileWriter(dir, Files, onFile, onBytes);
            using (var fs = OpenRead())
            {
                Task<byte[]> Start(int i)
                {
                    byte[] compressed = ReadBlock(fs, _blocks[i]);
                    int raw = _blocks[i].RawLength;
                    return Task.Run(() => Decompress(compressed, raw));
                }

                for (int i = 0; i < Math.Min(parallel, tasks.Length); i++) tasks[i] = Start(i);
                for (int i = 0; i < tasks.Length; i++)
                {
                    byte[] data;
                    try
                    {
                        data = tasks[i].Result;
                    }
                    catch (AggregateException e)
                    {
                        throw e.InnerException;
                    }
                    tasks[i] = null;
                    if (i + parallel < tasks.Length) tasks[i + parallel] = Start(i + parallel);
                    writer.Write(data);
                }
            }
            writer.Finish();
        }

        /// <summary>只要外壳（不带安装数据）：写成安装目录里的 Uninstall.exe。</summary>
        public void WriteStub(string path)
        {
            using (var src = OpenRead())
            using (var dst = new FileStream(path, FileMode.Create, FileAccess.Write))
            {
                var buffer = new byte[1 << 20];
                long left = StubLength;
                while (left > 0)
                {
                    int n = src.Read(buffer, 0, (int)Math.Min(buffer.Length, left));
                    if (n <= 0) throw new CorruptPackageException();
                    dst.Write(buffer, 0, n);
                    left -= n;
                }
            }
        }

        /// <summary>把首尾相接的数据流切回一个个文件。</summary>
        sealed class FileWriter
        {
            readonly string _dir;
            readonly List<Entry> _files;
            readonly Action<Entry> _onFile;
            readonly Action<long> _onBytes;
            int _index = -1;
            long _left;
            long _written;
            FileStream _out;
            string _target;
            string _temp;   // 目标被别的程序占着时，先写到这里

            public FileWriter(string dir, List<Entry> files, Action<Entry> onFile, Action<long> onBytes)
            {
                _dir = dir;
                _files = files;
                _onFile = onFile;
                _onBytes = onBytes;
                Next();
            }

            void Next()
            {
                Close();
                while (++_index < _files.Count)
                {
                    Entry e = _files[_index];
                    _onFile(e);
                    _target = Paths.Under(_dir, e.Path);
                    _out = FileOps.TryCreateForWrite(_target);
                    if (_out == null)
                    {
                        _temp = _target + FileOps.TempSuffix;
                        _out = FileOps.CreateForWrite(_temp);
                    }
                    _left = e.Size;
                    if (_left > 0) return;
                    Close();
                }
            }

            void Close()
            {
                if (_out == null) return;
                string path = _out.Name;
                _out.Dispose();
                _out = null;
                long time = _files[_index].FileTime;
                if (time > 0)
                {
                    try { File.SetLastWriteTimeUtc(path, DateTime.FromFileTimeUtc(time)); }
                    catch (Exception) { }
                }
                if (_temp != null)
                {
                    string temp = _temp;
                    _temp = null;
                    FileOps.ReplaceLocked(temp, _target);
                }
            }

            public void Write(byte[] data)
            {
                int offset = 0;
                while (offset < data.Length)
                {
                    if (_out == null) throw new CorruptPackageException();
                    int n = (int)Math.Min(_left, data.Length - offset);
                    FileOps.Guard(_out.Name, () => _out.Write(data, offset, n));
                    offset += n;
                    _left -= n;
                    _written += n;
                    _onBytes(_written);
                    if (_left == 0) Next();
                }
            }

            public void Finish()
            {
                if (_index < _files.Count) throw new CorruptPackageException();
            }
        }
    }

    sealed class CorruptPackageException : Exception
    {
        public CorruptPackageException() : base(Strings.T("err.corrupt")) { }
    }

    /// <summary>给用户看的出错原因（已经是当前界面语言）。</summary>
    sealed class SetupException : Exception
    {
        public SetupException(string message, Exception inner = null) : base(message, inner) { }
    }

    static class FileOps
    {
        const int ERROR_SHARING_VIOLATION = 32, ERROR_LOCK_VIOLATION = 33, ERROR_HANDLE_DISK_FULL = 39, ERROR_DISK_FULL = 112;

        static int Win32Code(Exception e) => e.HResult & 0xFFFF;

        static bool InUse(Exception e) => e is IOException && (Win32Code(e) == ERROR_SHARING_VIOLATION || Win32Code(e) == ERROR_LOCK_VIOLATION);

        /// <summary>写安装日志（重试、跳过被占用的文件）。</summary>
        public static Action<string> Log;

        /// <summary>被占用时的出错原因：查得到是谁占着就写出来。</summary>
        static SetupException InUseError(string path, Exception inner)
        {
            var who = Native.FileLockers(path);
            return new SetupException(who.Count > 0 ? Strings.T("err.inUseBy", path, string.Join(" / ", who)) : Strings.T("err.inUse", path), inner);
        }

        /// <summary>把 IO 出错换成看得懂的原因；被占用时重试 3 次。</summary>
        public static void Guard(string path, Action action)
        {
            for (int attempt = 0; ; attempt++)
            {
                try
                {
                    action();
                    return;
                }
                catch (IOException e) when (InUse(e))
                {
                    if (attempt >= 3) throw InUseError(path, e);
                    Log?.Invoke(Strings.T("log.retry", path));
                    Thread.Sleep(500);
                }
                catch (IOException e) when (Win32Code(e) == ERROR_DISK_FULL || Win32Code(e) == ERROR_HANDLE_DISK_FULL)
                {
                    throw new SetupException(Strings.T("err.diskFull"), e);
                }
                catch (UnauthorizedAccessException e)
                {
                    // 只读文件先去掉只读再试一次；还不行就是真没权限，或者 exe 正在运行
                    if (attempt == 0 && File.Exists(path) && (File.GetAttributes(path) & FileAttributes.ReadOnly) != 0)
                    {
                        File.SetAttributes(path, FileAttributes.Normal);
                        continue;
                    }
                    if (attempt >= 3) throw new SetupException(Strings.T("err.access", path), e);
                    Log?.Invoke(Strings.T("log.retry", path));
                    Thread.Sleep(500);
                }
            }
        }

        public static FileStream CreateForWrite(string path)
        {
            FileStream fs = null;
            Guard(path, () =>
            {
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                fs = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 16);
            });
            return fs;
        }

        /// <summary>同 CreateForWrite，但重试完还被别的程序占着时返回 null（交给 ReplaceLocked），不报错。</summary>
        public static FileStream TryCreateForWrite(string path)
        {
            try
            {
                return CreateForWrite(path);
            }
            catch (SetupException e) when (e.InnerException != null && InUse(e.InnerException))
            {
                return null;
            }
        }

        /// <summary>被别的程序占着的文件留下的东西（先写的新内容、挪开的旧文件），下次安装、卸载时顺手删。</summary>
        public const string TempSuffix = ".ganlan-new", LockedSuffix = ".ganlan-old-";

        /// <summary>
        /// target 被别的程序占着，新内容先写到了 temp：
        /// 内容一样（升级时 PySide6 之类的 DLL 基本都没变）就不用换；不一样先把旧文件改名挪开
        /// （加载着的 DLL、exe 允许改名），新文件放回原位，旧的重启后删。改名也不行就报是谁占着。
        /// </summary>
        public static void ReplaceLocked(string temp, string target)
        {
            var who = Native.FileLockers(target);
            string whoText = who.Count > 0 ? string.Join(" / ", who) : "?";
            if (SameContent(temp, target))
            {
                TryDelete(temp);
                Log?.Invoke(Strings.T("log.lockedSame", target, whoText));
                return;
            }
            string old = target + LockedSuffix + Guid.NewGuid().ToString("N").Substring(0, 8);
            try
            {
                File.Move(target, old);
            }
            catch (Exception e) when (e is IOException || e is UnauthorizedAccessException)
            {
                TryDelete(temp);
                throw InUseError(target, e);
            }
            File.Move(temp, target);
            if (!TryDelete(old)) Native.MoveFileExW(old, null, Native.MOVEFILE_DELAY_UNTIL_REBOOT);
            Log?.Invoke(Strings.T("log.lockedMoved", target, whoText));
        }

        static bool SameContent(string a, string b)
        {
            try
            {
                using (var fa = new FileStream(a, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 1 << 16))
                using (var fb = new FileStream(b, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 1 << 16))
                {
                    if (fa.Length != fb.Length) return false;
                    var ba = new byte[1 << 20];
                    var bb = new byte[1 << 20];
                    while (true)
                    {
                        int na = ReadFull(fa, ba), nb = ReadFull(fb, bb);
                        if (na != nb) return false;
                        if (na == 0) return true;
                        for (int i = 0; i < na; i++)
                            if (ba[i] != bb[i]) return false;
                    }
                }
            }
            catch (Exception e) when (e is IOException || e is UnauthorizedAccessException)
            {
                return false;
            }
        }

        static int ReadFull(Stream s, byte[] buffer)
        {
            int total = 0;
            while (total < buffer.Length)
            {
                int n = s.Read(buffer, total, buffer.Length - total);
                if (n <= 0) break;
                total += n;
            }
            return total;
        }

        /// <summary>删掉以前因为被占用挪开的旧文件（那时删不掉，安排了重启后删），和中途失败留下的新内容。</summary>
        public static void RemoveLockedLeftovers(string dir)
        {
            if (!Directory.Exists(dir)) return;
            try
            {
                foreach (string file in Directory.EnumerateFiles(dir, "*.ganlan-*", SearchOption.AllDirectories).ToList())
                {
                    string name = Path.GetFileName(file);
                    if (name.EndsWith(TempSuffix, StringComparison.OrdinalIgnoreCase) || name.Contains(LockedSuffix)) TryDelete(file);
                }
            }
            catch (Exception)
            {
            }
        }

        public static bool TryDelete(string path)
        {
            try
            {
                if (!File.Exists(path)) return true;
                File.SetAttributes(path, FileAttributes.Normal);
                File.Delete(path);
                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }

        /// <summary>从 start 往上删空文件夹，到 stop 为止（stop 本身不删）。</summary>
        public static void RemoveEmptyParents(string start, string stop)
        {
            try
            {
                string dir = start;
                while (dir != null && Paths.IsInside(dir, stop) && Directory.Exists(dir) && !Directory.EnumerateFileSystemEntries(dir).Any())
                {
                    Directory.Delete(dir);
                    dir = Path.GetDirectoryName(dir);
                }
            }
            catch (Exception)
            {
            }
        }

        /// <summary>递归删除目录；删不掉的记下来，安排重启后删除。返回删不掉的数量。</summary>
        public static int DeleteTree(string dir, Action<int> progress)
        {
            if (!Directory.Exists(dir)) return 0;
            int failed = 0, done = 0;
            foreach (string file in Directory.EnumerateFiles(dir, "*", SearchOption.AllDirectories).ToList())
            {
                if (!TryDelete(file))
                {
                    failed++;
                    Native.MoveFileExW(file, null, Native.MOVEFILE_DELAY_UNTIL_REBOOT);
                }
                progress?.Invoke(++done);
            }
            foreach (string sub in Directory.EnumerateDirectories(dir, "*", SearchOption.AllDirectories).OrderByDescending(d => d.Length).ToList())
            {
                try { Directory.Delete(sub); }
                catch (Exception) { Native.MoveFileExW(sub, null, Native.MOVEFILE_DELAY_UNTIL_REBOOT); }
            }
            try { Directory.Delete(dir); }
            catch (Exception) { Native.MoveFileExW(dir, null, Native.MOVEFILE_DELAY_UNTIL_REBOOT); }
            return failed;
        }

        /// <summary>复制整个文件夹（覆盖同名文件），给旧版搬数据用。</summary>
        public static void CopyTree(string from, string to)
        {
            foreach (string file in Directory.EnumerateFiles(from, "*", SearchOption.AllDirectories))
            {
                string target = Path.Combine(to, file.Substring(from.Length).TrimStart('\\'));
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                File.Copy(file, target, true);
            }
        }
    }
}
