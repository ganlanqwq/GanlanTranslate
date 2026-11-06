"""1.1.2：个性化、日志导出 / 分析错误、预设导出、API 两种模式、软件信息里的 GitHub 链接。"""
import base64
import io
import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / 'ui' / 'templates' / 'index.html').read_text(encoding='utf-8')
JS = ROOT / 'ui' / 'static' / 'js'

# 最小的 1x1 图片
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==')
JPG = base64.b64decode(
    '/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////'
    '////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=')


@pytest.fixture
def custom_dir(tmp_path, monkeypatch):
    import personalization
    monkeypatch.setattr(personalization, 'get_user_data_path', lambda rel='': str(tmp_path / rel) if rel else str(tmp_path))
    return tmp_path


@pytest.fixture
def client():
    from ui import app as ui_app
    return ui_app.app.test_client()


# ---------- 个性化 ----------

def test_sanitize_keeps_only_known_fields():
    import personalization as p
    out = p.sanitize({
        'theme': {'accent': '1f7a4d', 'surface': 'red', 'text': '#12261A', 'evil': '#000000', 'preset': '<x>'},
        'hero': {'title1': 'A' * 100, 'lede': 'line1\r\nline2', 'eyebrow': 'a\nb', 'overlay': 999},
        'image': {'custom': True, 'rev': 'x', 'original': '../../etc'},
        'other': 1,
    })
    assert out['theme']['accent'] == '#1F7A4D'
    assert out['theme']['surface'] == ''          # 不是 #RRGGBB 就不要
    assert 'evil' not in out['theme']
    assert out['theme']['preset'] == 'custom'
    assert len(out['hero']['title1']) == p.TEXT_LIMITS['title1']
    assert out['hero']['lede'] == 'line1\nline2'  # 介绍可以换行
    assert out['hero']['eyebrow'] == 'a b'
    assert out['hero']['overlay'] == p.OVERLAY_RANGE[1]
    assert out['image']['original'] == '' and out['image']['rev'] == 0
    assert 'other' not in out


def test_save_load_and_images(custom_dir):
    import personalization as p
    p.save({'theme': {'accent': '#6A4BB0'}, 'hero': {'title1': '你好', 'overlay': 60}})
    loaded = p.load()
    assert loaded['theme']['accent'] == '#6A4BB0' and loaded['hero']['title1'] == '你好'
    assert (custom_dir / 'personalization.json').is_file()

    with pytest.raises(p.PersonalizationError):
        p.save_hero_image(b'not an image')
    with pytest.raises(p.PersonalizationError):
        p.save_hero_image(JPG, b'\xff\xd8\xff' + b'0' * p.MAX_IMAGE_BYTES)

    s1 = p.save_hero_image(JPG, PNG)
    assert s1['image'] == {'custom': True, 'rev': 1, 'original': 'png'}
    assert (custom_dir / 'personalization' / 'hero.jpg').read_bytes() == JPG
    # 重新裁剪不传原图：保留之前的原图
    s2 = p.save_hero_image(JPG)
    assert s2['image']['original'] == 'png' and s2['image']['rev'] == 2
    assert p.original_path(s2).endswith('hero-original.png')
    # 保存配色不会动图片状态
    assert p.save({'theme': {}, 'hero': {}, 'image': {'custom': False}})['image']['custom'] is True

    s3 = p.remove_hero_image()
    assert s3['image']['custom'] is False and s3['image']['rev'] == 3
    assert not (custom_dir / 'personalization' / 'hero.jpg').exists()


def test_export_import_reset_round_trip(custom_dir):
    import personalization as p
    p.save({'theme': {'accent': '#C2457A', 'text': '#111111'}, 'hero': {'lede': '介绍'}})
    p.save_hero_image(JPG, PNG)
    data = p.export_data('1.1.2')
    assert data['kind'] == p.EXPORT_KIND and data['images']['hero'].startswith('data:image/jpeg;base64,')
    assert data['images']['original'].startswith('data:image/png;base64,')
    json.dumps(data)   # 能写成文件

    p.reset()
    assert p.load()['theme']['accent'] == '' and not (custom_dir / 'personalization' / 'hero.jpg').exists()

    restored = p.import_data(json.loads(json.dumps(data)))
    assert restored['theme']['accent'] == '#C2457A' and restored['hero']['lede'] == '介绍'
    assert restored['image']['custom'] is True and restored['image']['original'] == 'png'
    assert (custom_dir / 'personalization' / 'hero.jpg').read_bytes() == JPG

    with pytest.raises(p.PersonalizationError):
        p.import_data({'kind': 'something-else'})
    with pytest.raises(p.PersonalizationError):
        p.import_data({'kind': p.EXPORT_KIND, 'images': {'hero': 'data:image/png;base64,' + base64.b64encode(b'xx').decode()}})


def test_personalization_routes(custom_dir, client):
    bad = {'Origin': 'https://example.com'}
    assert client.post('/api/personalization', json={}, headers=bad).status_code == 403
    assert client.post('/api/personalization/reset', headers=bad).status_code == 403
    assert client.post('/api/personalization/hero-image', headers=bad).status_code == 403

    r = client.post('/api/personalization', json={'theme': {'accent': '#1F6FA8'}, 'hero': {'title2': '第二行'}})
    assert r.get_json()['settings']['theme']['accent'] == '#1F6FA8'
    assert client.get('/api/personalization/hero-image').status_code == 404

    r = client.post('/api/personalization/hero-image', data={
        'image': (io.BytesIO(JPG), 'hero.jpg'), 'original': (io.BytesIO(PNG), 'a.png')},
        content_type='multipart/form-data')
    settings = r.get_json()['settings']
    assert settings['image']['custom'] is True
    img = client.get(f"/api/personalization/hero-image?rev={settings['image']['rev']}")
    assert img.status_code == 200 and img.mimetype == 'image/jpeg' and img.data == JPG
    orig = client.get('/api/personalization/hero-image?original=1')
    assert orig.mimetype == 'image/png' and orig.data == PNG
    bad_upload = client.post('/api/personalization/hero-image', data={'image': (io.BytesIO(b'xx'), 'x.jpg')},
                             content_type='multipart/form-data')
    assert bad_upload.status_code == 400

    # 页面打开时就带上个性化：配色、首页图片、自定义文字
    html = client.get('/').get_data(as_text=True)
    assert '"accent": "#1F6FA8"' in html
    assert f"/api/personalization/hero-image?rev={settings['image']['rev']}" in html
    injected = json.loads(re.search(r'window\.GL_PERSONALIZATION = (.*);', html).group(1))
    assert injected['hero']['title2'] == '第二行'

    exported = client.get('/api/personalization/export').get_json()
    assert exported['data']['images']['hero']
    assert client.post('/api/personalization/reset').get_json()['settings']['image']['custom'] is False
    imported = client.post('/api/personalization/import', json=exported['data']).get_json()
    assert imported.get('settings', {}).get('image', {}).get('custom') is True, imported
    assert client.post('/api/personalization/import', json={'kind': 'x'}).status_code == 400


def test_index_injects_personalization_before_paint():
    head = INDEX.split('</head>')[0]
    # 配色在 <head> 里同步设好；theme.css 接在 tailwind.css 后面
    assert 'GL_PERSONALIZATION' in head and 'js/theme.js' in head
    assert head.index('css/tailwind.css') < head.index('css/theme.css') < head.index('css/style.css')
    assert 'class="hero-shade' in INDEX and '#0A21159E' not in INDEX   # 遮罩改成可调浓度的变量


def test_theme_css_covers_template_colors():
    """index.html 里写死颜色的 Tailwind 类都要在 theme.css 里接到配色变量上（不然换主题色时那块不变）。"""
    theme = (ROOT / 'ui' / 'static' / 'css' / 'theme.css').read_text(encoding='utf-8')
    classes = set(re.findall(r'(?<![\w-])((?:bg|text|border)-\[#[0-9A-Fa-f]{6}\])', INDEX))
    # 白字、半透明白（首页图片上）、琥珀色警告字不跟主题色
    allowed = {'text-[#FFFFFF]', 'text-[#B45309]', 'bg-[#FFF7E8]', 'text-[#8A4A08]'}
    missing = []
    for cls in sorted(classes - allowed):
        escaped = cls.replace('[', '\\[').replace(']', '\\]').replace('#', '\\#')
        if escaped not in theme:
            missing.append(cls)
    assert not missing, missing


def test_personalize_nav_after_plugins():
    groups = re.findall(r'data-page-group="(\w+)" aria-pressed', INDEX)
    assert groups == ['settings', 'features', 'plugins', 'personalize', 'info']
    tabs = (JS / 'tabs.js').read_text(encoding='utf-8')
    assert "personalize: ['theme', 'hero']" in tabs
    assert 'id="page-theme"' in INDEX and 'id="page-hero"' in INDEX
    # 调色盘是页面里画的：<input type=color> 在安装版窗口里会弹 Qt 的灰色对话框
    assert 'type="color"' not in INDEX and "type = 'color'" not in (JS / 'personalize.js').read_text(encoding='utf-8')


# ---------- 日志 ----------

def test_redact_and_rules():
    import log_tools as L
    text = L.redact('key sk-abcdef123456 Bearer abc.def.ghijkl api_key=XYZ12345678 ok', ['mysecretvalue1'])
    assert 'abcdef123456' not in text and 'abc.def.ghijkl' not in text and 'XYZ12345678' not in text
    assert L.redact('mysecretvalue1 here', ['mysecretvalue1']) == '*** here'

    lines = [
        '[10:00:00] [Others] 翻译他人管线已启动（等待 VRChat.exe）',
        '[10:00:01] [Others] 已挂接 VRChat.exe PID=24012',
        '[10:00:02] [Screen] 识别失败: Error code: 404 - Model not exist',
        '[10:00:03] [Others/ASR] 断句时识别连接已关闭，已忽略',
        '[10:00:04] [ASR] 识别错误: 401 api-key is invalid sk-abcdef1234567',
    ]
    found = L.analyze_rules(lines, keys_filled={'asr': True, 'llm': True})
    ids = [f['id'] for f in found]
    assert ids == ['auth', 'model']                 # 挂接上了就不报 VRChat；一行只报一个原因
    assert 'sk-abcdef1234567' not in found[0]['line']
    assert L.analyze_rules(lines[:1], keys_filled={})[0]['id'] == 'vrchat'
    missing = L.analyze_rules([], keys_filled={'asr': False, 'llm': False, 'ocr': False}, needs=['asr', 'llm'])
    assert missing == [{'id': 'no_key', 'line': '', 'missing': ['asr', 'llm']}]
    assert L.analyze_rules(['PID=24012 started'], keys_filled={}) == []


def test_error_excerpt_keeps_errors_and_tail():
    import log_tools as L
    lines = [f'[10:00:{i:02d}] normal {i}' for i in range(300)]
    lines[20] = '[10:00:20] Traceback (most recent call last):'
    text = L.error_excerpt(lines)
    assert 'Traceback' in text and 'normal 299' in text and 'normal 100' not in text and '...' in text


def test_analyze_with_llm_payload(monkeypatch):
    import log_tools as L
    import requests
    sent = {}

    class Resp:
        status_code = 200

        def json(self):
            return {'choices': [{'message': {'content': '<think>hmm</think>结论：Key 不对 sk-secretkey123456'}}]}

    def fake_post(url, headers=None, data=None, timeout=None):
        sent.update(url=url, headers=headers, body=json.loads(data.decode('utf-8')))
        return Resp()

    monkeypatch.setattr(requests, 'post', fake_post)
    text = L.analyze_with_llm(['[1] 401 api-key is invalid'], [{'id': 'auth'}], base_url='https://x.test/v1/',
                              api_key='sk-secretkey123456', model='m1', language='ja',
                              extra_body={'enable_thinking': False})
    assert sent['url'] == 'https://x.test/v1/chat/completions'
    assert sent['body']['model'] == 'm1' and sent['body']['enable_thinking'] is False
    assert 'Japanese' in sent['body']['messages'][0]['content']
    assert 'auth' in sent['body']['messages'][1]['content']
    assert text.startswith('结论') and 'sk-secretkey123456' not in text

    class Bad(Resp):
        status_code = 401
        text = 'invalid key sk-secretkey123456'

    monkeypatch.setattr(requests, 'post', lambda *a, **k: Bad())
    with pytest.raises(RuntimeError) as exc:
        L.analyze_with_llm([], [], base_url='https://x.test', api_key='sk-secretkey123456', model='m', language='zh-CN')
    assert '401' in str(exc.value) and 'sk-secretkey123456' not in str(exc.value)


def test_log_routes(client, monkeypatch):
    import config
    import log_tools
    monkeypatch.setattr(config, 'DASHSCOPE_API_KEY', 'sk-runtimesecret999', raising=False)
    import log_buffer
    log_buffer._append('[测试] 用到的 Key sk-runtimesecret999 不该出现在导出里')
    data = client.get('/api/logs/export?api_mode=bailian').get_json()
    assert data['success'] and re.match(r'GanlanTranslate-log-\d{8}-\d{6}\.txt$', data['filename'])
    assert 'Version: ' in data['text'] and 'API mode: bailian' in data['text']
    assert 'sk-runtimesecret999' not in data['text'] and '[测试]' in data['text']

    assert client.post('/api/logs/analyze', json={}, headers={'Origin': 'https://example.com'}).status_code == 403
    local = client.post('/api/logs/analyze', json={'keys_filled': {'asr': False, 'llm': False}}).get_json()
    assert local['mode'] == 'local' and local['reason'] == 'no_key' and local['findings'][0]['id'] == 'no_key'

    monkeypatch.setattr(log_tools, 'analyze_with_llm', lambda *a, **k: '分析结果')
    llm = client.post('/api/logs/analyze', json={
        'keys_filled': {'asr': True, 'llm': True},
        'llm': {'base_url': 'https://x.test/v1', 'api_key': 'sk-1', 'model': 'm', 'extra_body': '{"a": 1}'},
    }).get_json()
    assert llm['mode'] == 'llm' and llm['analysis'] == '分析结果' and llm['model'] == 'm'

    def boom(*a, **k):
        raise RuntimeError('HTTP 500 sk-1')
    monkeypatch.setattr(log_tools, 'analyze_with_llm', boom)
    failed = client.post('/api/logs/analyze', json={
        'llm': {'base_url': 'https://x.test/v1', 'api_key': 'sk-1', 'model': 'm'}}).get_json()
    assert failed['mode'] == 'local' and 'HTTP 500' in failed['llm_error']


def test_about_page_links_and_export(client):
    info = client.get('/api/app-info').get_json()
    assert info['repo_url'] == 'https://github.com/ganlanqwq/GanlanTranslate'
    assert info['releases_url'].endswith('/releases/latest')
    html = client.get('/').get_data(as_text=True)
    assert 'href="https://github.com/ganlanqwq/GanlanTranslate"' in html
    assert 'href="https://github.com/ganlanqwq/GanlanTranslate/releases/latest"' in html
    assert 'onclick="exportLogsFile()"' in INDEX and 'onclick="analyzeLogs()"' in INDEX


# ---------- 预设导出 / API 模式 ----------

def test_preset_export_by_name_with_optional_keys():
    main = (JS / 'main.js').read_text(encoding='utf-8')
    body = main[main.index('async function exportPresetsFile'):main.index('function applyImportedApiKeys')]
    assert "`${safeFileName(name, 'preset')}.json`" in body       # 文件名 = 预设名.json
    assert 'checkbox.checked = false' in body                     # 默认不带 Key
    assert 'delete values.CUSTOM_API_KEY' in body
    warn = main[main.index('async function confirmExportApiKeys'):main.index('async function exportPresetsFile')]
    for n in (1, 2, 3):                                           # 三次警告
        assert f"t('preset.keyWarn{n}Title')" in warn and f"t('preset.keyWarn{n}')" in warn
    assert 'charset=utf-8' in main[main.index('function downloadTextFile'):]


def test_preset_whitelist_has_api_mode():
    import presets
    for key in ('API_MODE', 'BAILIAN_LLM_MODEL', 'BAILIAN_OCR_MODEL'):
        assert key in presets.DEFAULT_PRESET_FIELDS
    # Key 还是不进预设
    assert not any(k.endswith('_KEY') or 'API_KEY' in k for k in presets.DEFAULT_PRESET_FIELDS)


def test_single_preset_file_imports(tmp_path, monkeypatch):
    """导出的单个预设文件（带 api_keys）能被导入；Key 不会写进 presets.json。"""
    import presets
    monkeypatch.setattr(presets, 'get_presets_path', lambda: str(tmp_path / 'presets.json'))
    result = presets.import_presets({
        'kind': 'ganlan-preset', 'schema_version': 1, 'name': '测试',
        'values': {'LLM_MODEL': 'qwen3.5-flash', 'API_MODE': 'bailian', 'CUSTOM_API_KEY': 'sk-x'},
        'api_keys': {'dashscope': 'sk-should-not-be-stored'},
    })
    assert result['imported'] == 1
    stored = (tmp_path / 'presets.json').read_text(encoding='utf-8')
    assert '测试' in stored and 'API_MODE' in stored
    assert 'sk-should-not-be-stored' not in stored and 'sk-x' not in stored


def test_api_card_structure():
    for element in ('api-mode-switch', 'api-panel-bailian', 'api-panel-custom', 'bailian-models', 'bailian-llm-model',
                    'bailian-ocr-model', 'api-sub-asr', 'api-sub-llm', 'api-sub-ocr'):
        assert f'id="{element}"' in INDEX, element
    # OpenAI 兼容模式的语音识别只用 OpenAI 兼容接口（岚要求去掉「百炼实时识别」那个选择）
    assert 'asr-provider' not in INDEX and 'asrProvider' not in (JS / 'api-mode.js').read_text(encoding='utf-8')
    # 「模型」每次打开都是折叠的
    assert 'class="collapsible-content collapsed" id="bailian-models"' in INDEX
    # 原来的三张卡（语音识别 / 翻译大模型 / 图像识别）合成了一张，图像识别那张卡没了
    assert 'id="screen-ocr-card"' not in INDEX
    # 每个输入框只出现一次（百炼 Key 在两种模式之间是挪过去的，不是复制）
    for element in ('dashscope-api-key', 'qwen-audio3-model', 'llm-base-url', 'screen-ocr-model'):
        assert INDEX.count(f'id="{element}"') == 1, element
    api = (JS / 'api-mode.js').read_text(encoding='utf-8')
    assert "'https://dashscope.aliyuncs.com/compatible-mode/v1'" in api
    assert "'https://dashscope-intl.aliyuncs.com/compatible-mode/v1'" in api


# ---------- 1.1.2 第二轮（岚看过安装版之后） ----------

def test_preset_fields_all_saved():
    """网页存进预设的每一项都要在白名单里、名字不能重复（以前流式翻译和显示部分结果撞成同一个名字，又都不在白名单里）。"""
    import presets
    main = (JS / 'main.js').read_text(encoding='utf-8')
    block = main[main.index('const PRESET_ID_TO_CONFIG = {'):main.index('function idToConfigKey')]
    mapped = dict(re.findall(r"'([\w-]+)': '(\w+)'", block))
    names = [v for k, v in mapped.items() if v != 'CUSTOM_API_KEY']
    assert len(names) == len(set(names)), sorted(n for n in names if names.count(n) > 1)
    lost = sorted(n for n in names if n not in presets.DEFAULT_PRESET_FIELDS)
    assert not lost, lost
    collected = main[main.index('function collectAllSerializableConfig'):main.index('const PRESET_ID_TO_CONFIG')]
    for element in re.findall(r"'([\w-]+)'", collected[collected.index('const ids = ['):collected.index('];')]):
        assert element in mapped, element


def test_others_asr_follows_api_settings():
    assert 'id="others-asr-backend"' not in INDEX
    assert 'data-i18n="hint.othersAsrFollowsApi"' in INDEX
    main = (JS / 'main.js').read_text(encoding='utf-8')
    assert main.count("asr_backend: '',") == 2      # 存浏览器、发后端都是「跟随」
    import presets
    assert 'OTHERS_ASR_BACKEND' not in presets.DEFAULT_PRESET_FIELDS


def test_fixed_pause_on_mute_hidden():
    assert '<div id="fg-mic-control" class="hidden" aria-hidden="true">' in INDEX
    assert 'id="fg-show-partial-results"' in INDEX


def test_theme_change_animates():
    theme = (ROOT / 'ui' / 'static' / 'css' / 'theme.css').read_text(encoding='utf-8')
    assert "@property --green-600 { syntax: '<color>'" in theme
    assert ':root.gl-theme-animating' in theme
    assert 'gl-theme-animating' in (JS / 'personalize.js').read_text(encoding='utf-8')
    # 打开页面时不播：theme.js 自己不加这个类
    assert 'gl-theme-animating' not in (JS / 'theme.js').read_text(encoding='utf-8')


def test_dialog_locks_page_scroll_and_no_spinning_star():
    dialog = (JS / 'dialog.js').read_text(encoding='utf-8')
    assert 'gl-scroll-locked' in dialog
    css = (ROOT / 'ui' / 'static' / 'css' / 'style.css').read_text(encoding='utf-8')
    assert 'html.gl-scroll-locked body' in css
    assert 'gl-spin' not in css


def test_window_has_no_green_border():
    src = (ROOT / 'qt_ui' / 'main_window.py').read_text(encoding='utf-8')
    assert 'WINDOW_BORDER' not in src.split('def _sync_frame_border')[0]
    body = src.split('def _sync_frame_border')[1].split('\n    def ')[0]
    assert 'setContentsMargins(0, 0, 0, 0)' in body


def test_legacy_service_start_route_removed(client):
    assert client.post('/api/service/start').status_code in (404, 405)


def test_removed_provider_key_fields():
    # 1.1.2 第三轮（岚：「把隐藏的 DeepL / 豆包 / Soniox Key 框去掉」）
    for name in ('deepl', 'doubao', 'soniox'):
        assert f'id="{name}-api-key"' not in INDEX
    assert '<option value="doubao_file"' not in INDEX
    assert '<option value="soniox"' not in INDEX
    main = (JS / 'main.js').read_text(encoding='utf-8')
    for name in ('deepl', 'doubao', 'soniox'):
        assert f'{name}-api-key' not in main
        assert f'{name}: ' not in main          # 不再往 api_keys 里塞
    assert 'openrouter_streaming_deepl_hybrid' not in main
    # 浏览器里存过的这三个 Key 打开页面时删掉
    assert "['deepl_api_key', 'doubao_api_key', 'soniox_api_key']" in main


def test_llm_template_machinery_removed():
    main = (JS / 'main.js').read_text(encoding='utf-8')
    for name in ('LLM_TEMPLATE_CONFIGS', 'ensureSelectedLLMTemplate', 'populateLLMTemplateForm',
                 'persistCurrentLLMTemplateKey', 'llm_template:'):
        assert name not in main
    assert 'llm-template' not in INDEX
    i18n = (JS / 'i18n.js').read_text(encoding='utf-8')
    assert 'llmTemplate' not in i18n
    # 旧的模板键：把 custom1 的 Key / 额外参数放回输入框一次，再删掉
    body = main.split('function migrateLegacyBrowserKeys() {')[1].split('\n}\n')[0]
    assert 'key_custom1' in body and 'extrabody_custom1' in body
    assert 'localStorage.removeItem(key)' in body
    # 后端不再存这个只存不用的字段
    import config
    assert not hasattr(config, 'LLM_TEMPLATE')


def test_streaming_toggle_survives_reload():
    # 以前读回时 openrouter（非流式）也当成流式，取消勾选一刷新又勾回去
    main = (JS / 'main.js').read_text(encoding='utf-8')
    assert "return apiType !== 'openrouter';" in main
    assert main.count('isStreamingStoredApiType(config.translation.api_type)') == 2


def test_reset_keeps_api_card():
    main = (JS / 'main.js').read_text(encoding='utf-8')
    body = main.split('async function resetToDefaults() {')[1].split('\n}\n')[0]
    assert 'snapshotApiCardFields()' in body
    assert body.index('snapshotApiCardFields()') < body.index('applyServerConfigPayload(defaults)') \
        < body.index('restoreApiCardFields(apiCardSnapshot)')


# ---------- 1.1.2 第四轮：后端删掉 DeepL / 豆包 / Soniox；OpenAI 兼容的语音识别、图像识别

def test_removed_provider_backends_gone():
    for rel in ('speech_recognizers/doubao_file_speech_recognizer.py',
                'speech_recognizers/soniox_speech_recognizer.py',
                'streaming_translation/api/deepl.py'):
        assert not (ROOT / rel).exists()
    import config
    for name in ('DEEPL_API_KEY', 'SONIOX_API_KEY', 'DOUBAO_API_KEY', 'STREAMING_FINAL_DEEPL_MAX_UPDATES'):
        assert not hasattr(config, name)
    from ui import app as ui_app
    assert set(ui_app.RUNTIME_CREDENTIAL_FIELDS) == {'dashscope', 'llm', 'screen_ocr'}
    import streaming_translation
    assert not hasattr(streaming_translation, 'DeepLAPI')


def test_whisper_language_codes():
    from speech_recognizers.custom_openai_speech_recognizer import whisper_language
    assert whisper_language('zh-CN') == 'zh'
    assert whisper_language('zh-Hans') == 'zh'
    assert whisper_language('ja') == 'ja'
    assert whisper_language('EN_us') == 'en'
    assert whisper_language('auto') == ''
    assert whisper_language(None) == ''


def test_api_test_route_uses_right_endpoint(client, monkeypatch):
    import requests
    calls = []

    class Resp:
        status_code = 200
        text = ''

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, headers=None, files=None, data=None, json=None, timeout=None):
        calls.append({'url': url, 'files': files, 'data': data, 'json': json})
        if url.endswith('/chat/completions') and json and isinstance(json['messages'][0]['content'], list):
            return Resp({'choices': [{'message': {'content': 'GANLAN 2468'}}]})
        return Resp({'text': ''})

    monkeypatch.setattr(requests, 'post', fake_post)
    base = {'base_url': 'https://api.example.com/v1/', 'api_key': 'k', 'model': 'm'}

    r = client.post('/api/custom-api/test', json={**base, 'kind': 'asr'}).get_json()
    assert r['success'] and calls[-1]['url'] == 'https://api.example.com/v1/audio/transcriptions'
    assert calls[-1]['files']['file'][0] == 'probe.wav' and calls[-1]['data'] == {'model': 'm'}

    r = client.post('/api/custom-api/test', json={**base, 'kind': 'ocr'}).get_json()
    assert calls[-1]['url'].endswith('/chat/completions')
    assert calls[-1]['json']['messages'][0]['content'][0]['type'] == 'image_url'
    assert r['reply'] == 'GANLAN 2468' and r['expected'] == 'GANLAN 2468'

    client.post('/api/custom-api/test', json=base)
    assert calls[-1]['json']['messages'][0]['content'] == 'ping'

    bad = client.post('/api/custom-api/test', json={**base, 'kind': 'asr'}, headers={'Origin': 'https://example.com'})
    assert bad.status_code == 403
    assert 'id="ocr-test-btn"' in INDEX


def test_empty_ocr_reply_is_explained(monkeypatch, capsys):
    import types
    from PIL import Image
    import screen_translation

    class FakeClient:
        def __init__(self, **kwargs):
            message = types.SimpleNamespace(content='')
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(
                create=lambda **kw: types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])))

        def close(self):
            pass

    import openai
    monkeypatch.setattr(openai, 'OpenAI', FakeClient)
    monkeypatch.setattr(screen_translation.config, 'SCREEN_OCR_API_KEY', 'k', raising=False)
    assert screen_translation.recognize_text(Image.new('RGB', (64, 64), 'black')) == []
    out = capsys.readouterr().out
    assert '返回了空内容' in out and '几乎是一种颜色' in out


def test_dashscope_asr_uses_chat_audio(monkeypatch):
    # 百炼兼容地址没有 /audio/transcriptions：直接走 /chat/completions，音频用 data URI，语言放 asr_options
    import requests
    from speech_recognizers import custom_openai_speech_recognizer as mod
    sent = []

    class Resp:
        status_code = 200
        text = ''

        def json(self):
            return {'choices': [{'message': {'content': '你好'}}]}

    def fake_post(url, headers=None, json=None, files=None, data=None, timeout=None):
        sent.append((url, json))
        return Resp()

    monkeypatch.setattr(requests, 'post', fake_post)
    results = []
    cb = type('CB', (), {'on_session_started': lambda s: None, 'on_session_stopped': lambda s: None,
                         'on_result': lambda s, e: results.append(e.text), 'on_error': lambda s, e: results.append(e)})()
    r = mod.CustomSttRecognizer(cb, base_url='https://dashscope.aliyuncs.com/compatible-mode/v1', api_key='k',
                                model='qwen3-asr-flash', source_language='zh-CN')
    r.start(); r.send_audio_frame(b'\x00\x00' * 1600); r.stop()
    url, body = sent[0]
    assert url.endswith('/chat/completions') and results == ['你好']
    part = body['messages'][0]['content'][0]
    assert part['type'] == 'input_audio' and part['input_audio']['data'].startswith('data:audio/wav;base64,')
    assert body['asr_options']['language'] == 'zh'


def test_ocr_coordinates_only_retries(monkeypatch):
    import types
    from PIL import Image
    import openai
    import screen_translation
    prompts = []

    class FakeClient:
        def __init__(self, **kwargs):
            def create(**kw):
                prompt = kw['messages'][0]['content'][1]['text']
                prompts.append(prompt)
                text = 'Hello' if prompt == screen_translation.OCR_MODEL_PROMPT else '50,88,15,18,90 64,86,13,7,90'
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))])
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))

        def close(self):
            pass

    monkeypatch.setattr(openai, 'OpenAI', FakeClient)
    monkeypatch.setattr(screen_translation.config, 'SCREEN_OCR_API_KEY', 'k', raising=False)
    monkeypatch.setattr(screen_translation.config, 'SCREEN_OCR_MODEL', 'qwen-vl-ocr', raising=False)
    assert screen_translation.recognize_text(Image.new('RGB', (32, 32), 'white')) == ['Hello']
    assert prompts == [screen_translation.OCR_MODEL_PROMPT]
    monkeypatch.setattr(screen_translation.config, 'SCREEN_OCR_MODEL', 'other-vision', raising=False)
    prompts.clear()
    assert screen_translation.recognize_text(Image.new('RGB', (32, 32), 'white')) == ['Hello']
    assert prompts == [screen_translation.OCR_PROMPT, screen_translation.OCR_MODEL_PROMPT]


def test_new_default_models(monkeypatch):
    import config
    import screen_translation
    assert config.QWEN_AUDIO3_ASR_MODEL == 'qwen-audio-3.1-asr-flash-streaming'
    assert config.SCREEN_OCR_MODEL == 'qwen3.5-ocr'
    api_mode = (JS / 'api-mode.js').read_text(encoding='utf-8')
    assert "llm: 'qwen3.7-flash'" in api_mode and 'upgradeOldDefaultModels' in api_mode
    # 国际版没有 qwen3.5-ocr：后台换成 qwen-vl-ocr
    monkeypatch.setattr(config, 'SCREEN_OCR_BASE_URL', screen_translation.DEFAULT_DASHSCOPE_COMPAT_URL, raising=False)
    monkeypatch.setattr(config, 'SCREEN_OCR_MODEL', 'qwen3.5-ocr', raising=False)
    monkeypatch.setattr(config, 'USE_INTERNATIONAL_ENDPOINT', True, raising=False)
    assert screen_translation._ocr_model() == 'qwen-vl-ocr'
    monkeypatch.setattr(config, 'USE_INTERNATIONAL_ENDPOINT', False, raising=False)
    assert screen_translation._ocr_model() == 'qwen3.5-ocr'


def test_dashscope_url_and_realtime_test(client, monkeypatch):
    # 百炼地址漏了 compatible-mode 自动补；实时流式模型的测试只验 Key（GET /models）
    import requests
    from speech_recognizers.custom_openai_speech_recognizer import normalize_dashscope_url
    assert normalize_dashscope_url('https://dashscope.aliyuncs.com/v1') == 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    assert normalize_dashscope_url('https://api.openai.com/v1') == 'https://api.openai.com/v1'
    calls = []

    class Resp:
        status_code = 200
        text = ''

        def json(self):
            return {}

    monkeypatch.setattr(requests, 'get', lambda url, headers=None, timeout=None: calls.append(('GET', url)) or Resp())
    monkeypatch.setattr(requests, 'post', lambda url, **kw: calls.append(('POST', url)) or Resp())
    r = client.post('/api/custom-api/test', json={'kind': 'asr', 'base_url': 'https://dashscope.aliyuncs.com/v1',
                                                  'api_key': 'k', 'model': 'qwen-audio-3.1-asr-flash-streaming'}).get_json()
    assert r['success'] and r['realtime']
    assert calls == [('GET', 'https://dashscope.aliyuncs.com/compatible-mode/v1/models')]
    api_mode = (JS / 'api-mode.js').read_text(encoding='utf-8')
    assert 'function compatRealtimeAsr()' in api_mode
    main = (JS / 'main.js').read_text(encoding='utf-8')
    assert "apiPlan && apiPlan.asrRealtime" in main and 'dashscope: getEffectiveDashscopeKey()' in main
