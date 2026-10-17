# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_dynamic_libs

# 打包：.venv\Scripts\python.exe -m PyInstaller GanlanTranslate.spec --noconfirm --distpath build_installer\dist --workpath build_installer\work
# 不要输出到 dist\（那里是旧的打包目录，不要动）。安装包再用 installer.nsi 生成。

datas = [('icon.ico', '.'), ('hot_words', 'hot_words'), ('terminology', 'terminology'), ('ui/templates', 'ui/templates'), ('ui/static', 'ui/static'), ('local_asr/models', 'local_asr/models')]
binaries = []
hiddenimports = ['dashscope', 'dashscope.audio.asr', 'dashscope.audio.qwen_omni', 'dashscope.audio.qwen_omni.omni_realtime', 'deepl', 'flask', 'flask_cors', 'pythonosc', 'pyaudio', 'fast_langdetect', 'googletrans', 'aiohttp', 'dotenv', 'main', 'openai', 'pykakasi', 'httpx', 'websockets', 'pypinyin', 'jieba', 'fancify_text', 'arabic_reshaper', 'bidi', 'bidi.algorithm', 'panel_app', 'panel_launcher', 'log_buffer', 'single_instance', 'qt_ui', 'qt_ui.main_window', 'qt_ui.window_frame', 'PySide6', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineCore', 'PySide6.QtWebChannel', 'shiboken6', 'webview', 'onnxruntime', 'onnxruntime.capi', 'presets', 'vrchat_detector', 'tray_app', 'pystray', 'PIL', 'speech_recognizers.custom_openai_speech_recognizer', 'requests', 'vr_bridge', 'screen_translation', 'others_pipeline', 'speaker_id', 'ui_locale', 'word_settings', 'terminology_manager', 'language_detectors.fasttext_detector', 'fasttext']
datas += collect_data_files('pykakasi')
datas += collect_data_files('fast_langdetect')   # 自带的 lid.176.ftz（他人多语言检测用）
hiddenimports += collect_submodules('webview.platforms')
hiddenimports += collect_submodules('streaming_translation')
hiddenimports += collect_submodules('local_asr')
hiddenimports += collect_submodules('gguf')

# PySide6 不要 collect_all（会把整个 Qt 打进来，600MB+）；PyInstaller 自带的 hook 只带用到的模块，
# QtWebEngineProcess.exe 和资源由 hook-PySide6.QtWebEngineCore 处理。


a = Analysis(
    ['run_ui.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['runtime_hook_std.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GanlanTranslate',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # 无控制台：以前带控制台时，点控制台的 × 会把整个程序杀掉（不进托盘）；日志在网页里看
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
# ---- VR 附属程序：单独一个无控制台的 GanlanTranslateVR.exe，和主程序放同一个目录 ----
vr_a = Analysis(
    ['run_vr.py'],
    pathex=[],
    binaries=collect_dynamic_libs('openvr'),
    datas=[('vr_overlay/icon.png', 'vr_overlay')],   # 附属程序图标（SteamVR 菜单缩略图 / 应用列表）
    hiddenimports=['vr_overlay', 'vr_overlay.main', 'vr_overlay.render', 'vr_overlay.link',
                   'vr_overlay.settings', 'vr_overlay.strings', 'vr_overlay.d3d_texture', 'ui_locale'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6', 'shiboken6', 'tkinter', 'webview'],
    noarchive=False,
    optimize=0,
)
vr_pyz = PYZ(vr_a.pure)
vr_exe = EXE(
    vr_pyz,
    vr_a.scripts,
    [],
    exclude_binaries=True,
    name='GanlanTranslateVR',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['vr_icon.ico'],   # 附属程序用自己的图标（绿色小人）
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    vr_exe,
    vr_a.binaries,
    vr_a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='GanlanTranslate',
)
