"""安装程序：installer/build.py 打的安装数据（格式、LZMS、版本号、界面文件），以及主程序给安装程序用的 /api/quit。"""
import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location('installer_build', ROOT / 'installer' / 'build.py')
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

windows_only = pytest.mark.skipif(sys.platform != 'win32', reason='LZMS 用的是 Windows 自带的 cabinet.dll')


def make_dist(tmp_path):
    dist = tmp_path / 'dist'
    files = {
        'GanlanTranslate.exe': b'MZ' + os.urandom(5000),
        '_internal/a.txt': '甘岚翻译'.encode() * 3000,
        '_internal/empty.bin': b'',
        '_internal/中文/名字.json': b'{"a": 1}',
        'vr_overlay/icon.png': os.urandom(70000),
        'Zeta.txt': b'z' * 10,
    }
    for rel, data in files.items():
        path = dist / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return dist, files


def make_stub(tmp_path):
    stub = tmp_path / 'stub.exe'
    stub.write_bytes(b'MZ stub' + bytes(1000))
    return stub


@windows_only
def test_lzms_roundtrip():
    data = os.urandom(1000) * 300 + b'abc' * 100000
    compressed = build.lzms_compress(data)
    assert len(compressed) < len(data)
    assert build.lzms_decompress(compressed, len(data)) == data


@windows_only
def test_pack_and_extract_roundtrip(tmp_path):
    dist, files = make_dist(tmp_path)
    stub = make_stub(tmp_path)
    out = build.pack(stub, dist, '9.8.7', tmp_path / 'setup.exe', block_size=32 * 1024, log=lambda *_: None)
    info = build.extract_payload(out, tmp_path / 'out')

    assert info['version'] == '9.8.7'
    assert info['stub_length'] == stub.stat().st_size
    assert out.read_bytes()[:info['stub_length']] == stub.read_bytes()   # Uninstall.exe 就是这一段
    assert len(info['blocks']) > 1                                        # 块很小：文件会跨块
    assert [f[0] for f in info['files']] == sorted(files, key=str.lower)
    assert info['total'] == sum(len(d) for d in files.values())
    for rel, data in files.items():
        assert (tmp_path / 'out' / rel).read_bytes() == data


@windows_only
def test_damaged_installer_is_detected(tmp_path):
    dist, _ = make_dist(tmp_path)
    out = build.pack(make_stub(tmp_path), dist, '1.0', tmp_path / 'setup.exe', block_size=32 * 1024, log=lambda *_: None)
    data = bytearray(out.read_bytes())
    data[len(data) - 2000] ^= 0xFF        # 压缩块里改一个字节
    out.write_bytes(bytes(data))
    with pytest.raises(ValueError):
        build.read_payload(out)
    out.write_bytes(bytes(data[:-5000]))   # 下载到一半
    with pytest.raises(ValueError):
        build.read_payload(out)


def test_payload_markers_match_csharp_reader():
    cs = (ROOT / 'installer' / 'Payload.cs').read_text(encoding='utf-8')
    assert '"GLSETUP1"' in cs and '"GLPKG001"' in cs
    assert build.FOOTER_MAGIC == b'GLSETUP1' and build.HEADER_MAGIC == b'GLPKG001'


def test_read_version_matches_app():
    from ui import app as ui_app
    assert build.read_version() == ui_app.APP_VERSION


def test_ui_files_cover_everything_the_page_references():
    files = build.ui_files()
    for name in ('setup.html', 'setup.js', 'setup.css', 'LICENSE.txt'):
        assert files[name].is_file()
    html = re.sub(r'<!--.*?-->', '', files['setup.html'].read_text(encoding='utf-8'), flags=re.S)   # 注释里是放动画的示例
    refs = re.findall(r'(?:href|src)="((?!https?:)[^"#]+)"', html) + re.findall(r"url\('([^']+)'\)", html)
    assert any(r.startswith('shared/') for r in refs)
    for ref in refs:
        assert ref in files and files[ref].is_file(), ref


def test_quit_route(monkeypatch):
    from ui import app as ui_app
    client = ui_app.app.test_client()

    monkeypatch.setattr(ui_app, '_quit_handler', None)
    assert client.post('/api/quit').get_json()['success'] is False   # 开发版：不退出

    called = []
    monkeypatch.setattr(ui_app, '_quit_handler', lambda: called.append(True))
    assert client.post('/api/quit', headers={'Origin': 'https://example.com'}).status_code == 403
    assert client.post('/api/quit', headers={'Host': 'evil.test:5001'}).status_code == 403
    assert not called
    assert client.post('/api/quit').get_json()['success'] is True
    assert called == [True]


def test_uninstall_route(monkeypatch, tmp_path):
    """「软件信息」页的「卸载」：安装版打开 exe 旁边的 Uninstall.exe（当前目录不在安装目录里），开发版没有。"""
    import subprocess
    from ui import app as ui_app
    client = ui_app.app.test_client()
    launched = []
    monkeypatch.setattr(subprocess, 'Popen', lambda args, **kw: launched.append((args, kw)))

    monkeypatch.delattr(sys, 'frozen', raising=False)
    assert client.get('/api/app-info').get_json()['uninstaller'] is False
    assert client.post('/api/uninstall').status_code == 404

    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'GanlanTranslate.exe'))
    assert client.post('/api/uninstall').status_code == 404   # 安装目录里没有 Uninstall.exe
    (tmp_path / 'Uninstall.exe').write_bytes(b'MZ')
    assert client.get('/api/app-info').get_json()['uninstaller'] is True
    assert client.post('/api/uninstall', headers={'Origin': 'https://example.com'}).status_code == 403
    assert client.post('/api/uninstall', headers={'Host': 'evil.test:5001'}).status_code == 403
    assert not launched

    assert client.post('/api/uninstall').get_json()['success'] is True
    (args, kw), = launched
    assert args == [str(tmp_path / 'Uninstall.exe')]
    assert not Path(kw['cwd']).resolve().is_relative_to(tmp_path.resolve())


def test_uninstall_button_on_about_page():
    page = (ROOT / 'ui' / 'templates' / 'index.html').read_text(encoding='utf-8')
    about = page[page.index('id="page-about"'):page.index('id="page-credits"')]
    assert 'id="uninstall-btn"' in about and 'openUninstaller()' in about
    # 卡片都要在页面的格子里：<section> 要自己关上，不能靠后面的 </div> 顺带关
    assert about.count('<section') == about.count('</section>')
    i18n = (ROOT / 'ui' / 'static' / 'js' / 'i18n.js').read_text(encoding='utf-8')
    for key in ('about.uninstallTitle', 'about.uninstallHint', 'about.uninstallMissing', 'about.uninstall',
                'msg.uninstallerOpened', 'msg.uninstallerFailed'):
        assert i18n.count(f"'{key}':") == 4, key
