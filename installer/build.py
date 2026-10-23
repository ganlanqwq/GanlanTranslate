"""打包甘岚翻译的安装程序。

用法（在仓库根目录）：
    .venv\\Scripts\\python.exe installer\\build.py                打包（先自己跑好 PyInstaller）
    .venv\\Scripts\\python.exe installer\\build.py --pyinstaller  先跑 PyInstaller 再打包

步骤：
1. 从 ui/app.py 读版本号。
2. 安装界面（installer/ui，加上主软件的 css、许可）打成 build_installer/setup_ui.zip。
   主软件的文件不复制进 installer/ui：网页里写 shared/...，这里映射到 ui/static/，主软件改了样式下次打包就跟着变。
3. dotnet build 编译外壳 GanlanSetup.exe（WebView2 的 DLL 和 setup_ui.zip 都打在 exe 资源里）。
4. build_installer/dist/GanlanTranslate 打成安装数据接在外壳后面 → build_installer/GanlanTranslate-Setup-<版本>.exe
   文件按路径排序首尾相接，64 MB 一块，每块用 Windows 自带的 LZMS（cabinet.dll）压缩。
   格式见 installer/Payload.cs 开头的注释，两边要一致。
"""
import argparse
import ctypes
import hashlib
import io
import os
import re
import struct
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BUILD = ROOT / 'build_installer'
DIST = BUILD / 'dist' / 'GanlanTranslate'
UI_ZIP = BUILD / 'setup_ui.zip'
STUB_DIR = BUILD / 'setup_stub'
CSPROJ = HERE / 'GanlanSetup.csproj'

BLOCK_SIZE = 64 * 1024 * 1024
HEADER_MAGIC = b'GLPKG001'
FOOTER_MAGIC = b'GLSETUP1'

# 网页里 shared/ 开头的路径 → 仓库里主软件的文件
SHARED = {
    'shared/css/tailwind.css': ROOT / 'ui' / 'static' / 'css' / 'tailwind.css',
    'shared/css/style.css': ROOT / 'ui' / 'static' / 'css' / 'style.css',
    'LICENSE.txt': ROOT / 'LICENSE.txt',
}


def read_version() -> str:
    text = (ROOT / 'ui' / 'app.py').read_text(encoding='utf-8')
    m = re.search(r"^APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", text, re.M)
    if not m:
        raise SystemExit('ui/app.py 里找不到 APP_VERSION')
    return m.group(1)


def webview2_package() -> Path | None:
    """dotnet 还原下来的 Microsoft.Web.WebView2 包目录（取许可和声明）。"""
    m = re.search(r'Include="Microsoft\.Web\.WebView2"\s+Version="([^"]+)"', CSPROJ.read_text(encoding='utf-8'))
    root = Path(os.environ.get('NUGET_PACKAGES') or Path.home() / '.nuget' / 'packages')
    path = root / 'microsoft.web.webview2' / m.group(1) if m else None
    return path if path and path.is_dir() else None


def ui_files() -> dict[str, Path]:
    """安装界面的全部文件：{网页里的路径: 本机文件}。"""
    files: dict[str, Path] = {}
    ui = HERE / 'ui'
    for path in sorted(ui.rglob('*')):
        if path.is_file() and not any(part.startswith('.') for part in path.relative_to(ui).parts):
            files[path.relative_to(ui).as_posix()] = path
    files.update(SHARED)
    package = webview2_package()
    if package:
        for name in ('LICENSE.txt', 'NOTICE.txt'):
            if (package / name).is_file():
                files[f'third_party/WebView2-{name}'] = package / name
    return files


def build_ui_zip(path: Path = UI_ZIP) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, src in ui_files().items():
            if not src.is_file():
                raise SystemExit(f'找不到安装界面要用的文件：{src}')
            zf.write(src, name)
    return path


def build_stub() -> Path:
    subprocess.run(['dotnet', 'restore', str(CSPROJ)], check=True, cwd=ROOT)
    build_ui_zip()
    subprocess.run(['dotnet', 'build', str(CSPROJ), '-c', 'Release', '--no-restore', '-nologo', '-v', 'q',
                    '-o', str(STUB_DIR)], check=True, cwd=ROOT)
    return STUB_DIR / 'GanlanSetup.exe'


# ---------------- LZMS（Windows Compression API，cabinet.dll） ----------------

COMPRESS_ALGORITHM_LZMS = 5
COMPRESS_INFORMATION_CLASS_BLOCK_SIZE = 1
_cabinet = None


def _cab():
    global _cabinet
    if _cabinet is None:
        cab = ctypes.WinDLL('cabinet.dll', use_last_error=True)
        handle = ctypes.c_void_p
        size = ctypes.c_size_t
        cab.CreateCompressor.argtypes = [wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(handle)]
        cab.SetCompressorInformation.argtypes = [handle, ctypes.c_int, ctypes.c_void_p, size]
        cab.Compress.argtypes = [handle, ctypes.c_void_p, size, ctypes.c_void_p, size, ctypes.POINTER(size)]
        cab.CloseCompressor.argtypes = [handle]
        cab.CreateDecompressor.argtypes = [wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(handle)]
        cab.Decompress.argtypes = [handle, ctypes.c_void_p, size, ctypes.c_void_p, size, ctypes.POINTER(size)]
        cab.CloseDecompressor.argtypes = [handle]
        _cabinet = cab
    return _cabinet


def lzms_compress(data: bytes) -> bytes:
    """整块一次压（LZMS 的块大小设成这块的大小，比默认的小块压得更小）。"""
    cab = _cab()
    h = ctypes.c_void_p()
    if not cab.CreateCompressor(COMPRESS_ALGORITHM_LZMS, None, ctypes.byref(h)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        block = wintypes.DWORD(max(len(data), 1 << 20))
        if not cab.SetCompressorInformation(h, COMPRESS_INFORMATION_CLASS_BLOCK_SIZE, ctypes.byref(block), 4):
            raise ctypes.WinError(ctypes.get_last_error())
        capacity = len(data) + len(data) // 8 + (1 << 20)
        out = ctypes.create_string_buffer(capacity)
        n = ctypes.c_size_t()
        if not cab.Compress(h, data, len(data), out, capacity, ctypes.byref(n)):
            raise ctypes.WinError(ctypes.get_last_error())
        return out.raw[:n.value]
    finally:
        cab.CloseCompressor(h)


def lzms_decompress(data: bytes, size: int) -> bytes:
    cab = _cab()
    h = ctypes.c_void_p()
    if not cab.CreateDecompressor(COMPRESS_ALGORITHM_LZMS, None, ctypes.byref(h)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        out = ctypes.create_string_buffer(size)
        n = ctypes.c_size_t()
        if not cab.Decompress(h, data, len(data), out, size, ctypes.byref(n)) or n.value != size:
            raise ValueError('解压失败')
        return out.raw[:size]
    finally:
        cab.CloseDecompressor(h)


# ---------------- 安装数据 ----------------

def _filetime(ns: int) -> int:
    return ns // 100 + 116444736000000000


def collect_files(dist: Path) -> list[tuple[str, int, int]]:
    """[(相对路径, 大小, 修改时间 FILETIME)]，按路径排序。"""
    files = []
    for path in dist.rglob('*'):
        if path.is_file():
            st = path.stat()
            files.append((path.relative_to(dist).as_posix(), st.st_size, _filetime(st.st_mtime_ns)))
    files.sort(key=lambda f: f[0].lower())
    return files


def _raw_blocks(dist: Path, files, block_size: int):
    buf = bytearray()
    for rel, _size, _mtime in files:
        with open(dist / rel, 'rb') as f:
            while chunk := f.read(4 << 20):
                buf += chunk
                while len(buf) >= block_size:
                    yield bytes(buf[:block_size])
                    del buf[:block_size]
    if buf:
        yield bytes(buf)


def _header(version: str, total: int, block_size: int, files, blocks) -> bytes:
    out = io.BytesIO()

    def text(value: str):
        data = value.encode('utf-8')
        out.write(struct.pack('<H', len(data)))
        out.write(data)

    text(version)
    out.write(struct.pack('<qi', total, block_size))
    out.write(struct.pack('<i', len(files)))
    for rel, size, mtime in files:
        text(rel)
        out.write(struct.pack('<qq', size, mtime))
    out.write(struct.pack('<i', len(blocks)))
    for raw_length, compressed in blocks:
        out.write(struct.pack('<ii', raw_length, len(compressed)))
        out.write(hashlib.sha256(compressed).digest())
    return out.getvalue()


def pack(stub: Path, dist: Path, version: str, out: Path, block_size: int = BLOCK_SIZE, log=print) -> Path:
    """外壳 + 安装数据 → 安装程序 exe。"""
    files = collect_files(dist)
    if not files:
        raise SystemExit(f'{dist} 是空的，先跑 PyInstaller')
    total = sum(size for _, size, _ in files)
    workers = max(1, min(8, (os.cpu_count() or 2)))
    gate = threading.BoundedSemaphore(workers + 2)   # 同时在内存里的原始块不超过这么多

    def compress(raw: bytes):
        try:
            return len(raw), lzms_compress(raw)
        finally:
            gate.release()

    started = time.time()
    with ThreadPoolExecutor(workers) as pool:
        futures = []
        for raw in _raw_blocks(dist, files, block_size):
            gate.acquire()
            futures.append(pool.submit(compress, raw))
        blocks = [f.result() for f in futures]
    compressed = sum(len(c) for _, c in blocks)
    log(f'压缩：{len(files)} 个文件 {total / 2**20:.1f} MB → {compressed / 2**20:.1f} MB（{len(blocks)} 块，{time.time() - started:.0f} 秒）')

    header = _header(version, total, block_size, files, blocks)
    stub_bytes = stub.read_bytes()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix('.tmp')
    with open(tmp, 'wb') as f:
        f.write(stub_bytes)
        f.write(HEADER_MAGIC)
        f.write(struct.pack('<i', len(header)))
        f.write(header)
        f.write(hashlib.sha256(header).digest())
        for _, data in blocks:
            f.write(data)
        f.write(struct.pack('<q', len(stub_bytes)))
        f.write(FOOTER_MAGIC)
    os.replace(tmp, out)
    return out


def read_payload(exe: Path) -> dict:
    """按 Payload.cs 的格式读回安装数据（测试、检查用）。"""
    data = exe.read_bytes()
    if data[-8:] != FOOTER_MAGIC:
        raise ValueError('没有安装数据')
    offset = struct.unpack('<q', data[-16:-8])[0]
    pos = offset
    if data[pos:pos + 8] != HEADER_MAGIC:
        raise ValueError('头的标记不对')
    header_length = struct.unpack('<i', data[pos + 8:pos + 12])[0]
    header = data[pos + 12:pos + 12 + header_length]
    if hashlib.sha256(header).digest() != data[pos + 12 + header_length:pos + 44 + header_length]:
        raise ValueError('头的校验不对')
    pos += 44 + header_length

    h = io.BytesIO(header)

    def text():
        n = struct.unpack('<H', h.read(2))[0]
        return h.read(n).decode('utf-8')

    version = text()
    total, block_size = struct.unpack('<qi', h.read(12))
    files = []
    for _ in range(struct.unpack('<i', h.read(4))[0]):
        rel = text()
        size, mtime = struct.unpack('<qq', h.read(16))
        files.append((rel, size, mtime))
    blocks = []
    for _ in range(struct.unpack('<i', h.read(4))[0]):
        raw_length, length = struct.unpack('<ii', h.read(8))
        digest = h.read(32)
        chunk = data[pos:pos + length]
        if hashlib.sha256(chunk).digest() != digest:
            raise ValueError('块的校验不对')
        blocks.append((raw_length, chunk))
        pos += length
    if pos != len(data) - 16:
        raise ValueError('长度不对')
    return {'version': version, 'total': total, 'block_size': block_size, 'files': files,
            'blocks': blocks, 'stub_length': offset}


def extract_payload(exe: Path, dest: Path) -> dict:
    """Python 版的解压（和 Payload.cs 的 Extract 一样），测试用。"""
    info = read_payload(exe)
    stream = b''.join(lzms_decompress(chunk, raw) for raw, chunk in info['blocks'])
    pos = 0
    for rel, size, _mtime in info['files']:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(stream[pos:pos + size])
        pos += size
    if pos != len(stream):
        raise ValueError('数据比文件表多')
    return info


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='打包甘岚翻译的安装程序')
    parser.add_argument('--pyinstaller', action='store_true', help='先跑 PyInstaller（GanlanTranslate.spec）')
    parser.add_argument('--skip-stub', action='store_true', help='不重新编译外壳，用 build_installer/setup_stub 里现成的')
    args = parser.parse_args(argv)

    started = time.time()
    version = read_version()
    print(f'甘岚翻译 {version}')
    if args.pyinstaller:
        subprocess.run([sys.executable, '-m', 'PyInstaller', 'GanlanTranslate.spec', '--noconfirm',
                        '--distpath', str(BUILD / 'dist'), '--workpath', str(BUILD / 'work')], check=True, cwd=ROOT)
    if not (DIST / 'GanlanTranslate.exe').is_file():
        raise SystemExit(f'找不到 {DIST}\\GanlanTranslate.exe，先跑 PyInstaller（或加 --pyinstaller）')
    stub = STUB_DIR / 'GanlanSetup.exe' if args.skip_stub else build_stub()
    print(f'外壳：{stub}（{stub.stat().st_size / 1024:.0f} KB）')
    out = pack(stub, DIST, version, BUILD / f'GanlanTranslate-Setup-{version}.exe')
    print(f'完成：{out}（{out.stat().st_size / 2**20:.1f} MB，共 {time.time() - started:.0f} 秒）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
