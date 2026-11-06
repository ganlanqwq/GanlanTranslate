// API基础URL
const API_BASE = '/api';

// 自动保存定时器
let autoSaveTimer = null;

/** PyAudio 解析到的系统默认输入设备 identity，用于检测默认设备是否变化 */
let lastMicDefaultIdentity = null;

// 配置键名
const CONFIG_STORAGE_KEY = 'vrchat_translator_config';
const VRCX_BRIDGE_BUTTON_VISIBLE_STORAGE_KEY = 'vrcx_bridge_button_visible';

// ===================== 大面板 ↔ 后端 同步基础设施 =====================
//
// 规则：
//   1. 后端在内存里维护 backend_boot_ms（进程启动时固定）和 config_applied_at_ms（每次配置变更刷新）
//   2. 大面板在 localStorage 维护 touch_main_ms（用户在大面板做了任何更改就写 Date.now()）
//      以及 last_seen_boot_ms（上次见到的后端 boot 时刻）
//   3. 小面板不维护时间戳；只推后端、拉后端
//
// 打开大面板时（reconcile）：
//   - boot_ms ≠ last_seen_boot_ms → 后端重启过 → 大面板本地配置推到后端
//   - boot_ms = last_seen_boot_ms → 同一运行期 → config_applied_at_ms vs touch_main_ms 谁大听谁
//
// 运行中轮询（updateStatus）：
//   - config_applied_at_ms > touch_main_ms → 拉服务器的（小面板改了语言等）

const CONFIG_TOUCH_MAIN_MS_KEY = 'vrchat_translator_touch_main_ms';
const LAST_SEEN_BOOT_MS_KEY = 'vrchat_translator_last_seen_boot_ms';

function _readLocalStorageInt(key) {
    const v = parseInt(localStorage.getItem(key) || '0', 10);
    return Number.isFinite(v) ? v : 0;
}

function getMainConfigTouchMs() {
    return _readLocalStorageInt(CONFIG_TOUCH_MAIN_MS_KEY);
}

function setMainConfigTouchMs(ms) {
    const n = Math.floor(Number(ms));
    if (Number.isFinite(n) && n > 0) {
        localStorage.setItem(CONFIG_TOUCH_MAIN_MS_KEY, String(n));
    }
}

function getLastSeenBootMs() {
    return _readLocalStorageInt(LAST_SEEN_BOOT_MS_KEY);
}

function setLastSeenBootMs(ms) {
    const n = Math.floor(Number(ms));
    if (Number.isFinite(n) && n > 0) {
        localStorage.setItem(LAST_SEEN_BOOT_MS_KEY, String(n));
    }
}

function touchMainPanelUserEditedAt() {
    setMainConfigTouchMs(Date.now());
}

// ===================== 同步基础设施结束 =====================

const PANEL_FLOATING_MODE_STORAGE_KEY = 'panel_floating_mode';
const PANEL_FLOATING_MODE_DEFAULT = true;
// 1.1.2 去掉了大模型「模板」：以前翻译的 Key、额外参数在 llm_template_* 里另存了一份（读的时候以它为准）；
// DeepL / 豆包 / Soniox 的 Key 框也去掉了。打开页面时把模板里的值放回输入框一次，再把这些键删掉。
const LEGACY_LLM_TEMPLATE_STORAGE_PREFIX = 'llm_template_';
const LEGACY_LLM_SELECTED_TEMPLATE_STORAGE_KEY = 'llm_selected_template';
const LEGACY_REMOVED_KEY_STORAGE_KEYS = ['deepl_api_key', 'doubao_api_key', 'soniox_api_key'];

// 后端进程内凭据状态
let runtimeCredentialStatus = {
    llm: {
        api_key_set: false,
    },
};

let featureFlags = {
    local_asr_build_enabled: false,
    local_asr_ui_enabled: false,
    engines: {},
};

let localAsrStatus = {
    running: false,
    engine: null,
    status: '',
    error: null,
};

function updateLocalAsrEngineHint() {
    const el = document.getElementById('local-asr-engine-hint');
    const engine = document.getElementById('local-asr-engine')?.value || 'sensevoice';
    if (!el) return;
    if (!isLocalAsrUiEnabled()) {
        el.textContent = '';
        return;
    }
    const t = window.i18n ? window.i18n.t : (key) => key;
    const key =
        engine === 'qwen3-asr' ? 'localAsr.engine.qwen3Hint' : 'localAsr.engine.sensevoiceHint';
    el.textContent = t(key);
}

function isLocalAsrUiEnabled() {
    return !!featureFlags.local_asr_ui_enabled;
}

function getLocalAsrConfigFromForm() {
    return {
        engine: document.getElementById('local-asr-engine')?.value || 'sensevoice',
        incremental_asr: document.getElementById('local-incremental-asr')?.checked ?? true,
        interim_interval: parseFloat(document.getElementById('local-interim-interval')?.value || '2'),
    };
}

function applyLocalAsrConfig(config) {
    if (!config) return;
    if (document.getElementById('local-asr-engine')) {
        document.getElementById('local-asr-engine').value = config.engine || 'sensevoice';
    }
    if (document.getElementById('local-incremental-asr')) {
        document.getElementById('local-incremental-asr').checked = config.incremental_asr ?? true;
    }
    if (document.getElementById('local-interim-interval')) {
        document.getElementById('local-interim-interval').value = config.interim_interval ?? 2.0;
    }
    updateLocalAsrEngineHint();
}

function getVadConfigFromForm() {
    return {
        enabled: document.getElementById('vad-enabled')?.checked ?? true,
        mode: document.getElementById('vad-mode')?.value || 'silero',
        threshold: parseFloat(document.getElementById('vad-threshold')?.value || '0.5'),
        min_speech_duration: parseFloat(document.getElementById('vad-min-speech-duration')?.value || '1'),
        max_speech_duration: parseFloat(document.getElementById('vad-max-speech-duration')?.value || '30'),
        silence_duration: parseFloat(document.getElementById('vad-silence-duration')?.value || '0.8'),
        pre_speech_duration: parseFloat(document.getElementById('vad-pre-speech-duration')?.value || '0.2'),
    };
}

function resolveVadConfig(config = {}) {
    const legacyAsr = config.asr || {};
    const legacyLocalAsr = config.local_asr || {};
    const legacyMic = config.mic_control || {};
    const vad = config.vad || {};
    const hasUnifiedVadEnabled = Object.prototype.hasOwnProperty.call(vad, 'enabled');
    return {
        enabled:
            (hasUnifiedVadEnabled ? vad.enabled : undefined) ??
            (legacyMic.enable_vad_gating === true ? true : undefined) ??
            true,
        mode: vad.mode || legacyLocalAsr.vad_mode || 'silero',
        threshold:
            vad.threshold ??
            legacyLocalAsr.vad_threshold ??
            legacyAsr.vad_threshold ??
            0.5,
        min_speech_duration:
            vad.min_speech_duration ??
            legacyLocalAsr.min_speech_duration ??
            1.0,
        max_speech_duration:
            vad.max_speech_duration ??
            legacyLocalAsr.max_speech_duration ??
            30.0,
        silence_duration:
            vad.silence_duration ??
            legacyLocalAsr.silence_duration ??
            ((legacyAsr.vad_silence_duration_ms ?? 800) / 1000),
        pre_speech_duration:
            vad.pre_speech_duration ??
            legacyLocalAsr.pre_speech_duration ??
            0.2,
    };
}

function applyVadConfig(config) {
    const vad = resolveVadConfig({ vad: config || {} });
    if (document.getElementById('vad-enabled')) {
        document.getElementById('vad-enabled').checked = vad.enabled ?? true;
    }
    if (document.getElementById('vad-mode')) {
        document.getElementById('vad-mode').value = vad.mode || 'silero';
    }
    if (document.getElementById('vad-threshold')) {
        document.getElementById('vad-threshold').value = vad.threshold ?? 0.5;
    }
    if (document.getElementById('vad-min-speech-duration')) {
        document.getElementById('vad-min-speech-duration').value = vad.min_speech_duration ?? 1.0;
    }
    if (document.getElementById('vad-max-speech-duration')) {
        document.getElementById('vad-max-speech-duration').value = vad.max_speech_duration ?? 30.0;
    }
    if (document.getElementById('vad-silence-duration')) {
        document.getElementById('vad-silence-duration').value = vad.silence_duration ?? 0.8;
    }
    if (document.getElementById('vad-pre-speech-duration')) {
        document.getElementById('vad-pre-speech-duration').value = vad.pre_speech_duration ?? 0.2;
    }
}

function ensureLocalAsrBackendOption() {
    const select = document.getElementById('asr-backend');
    if (!select) return;
    const t = window.i18n ? window.i18n.t : (key) => key;
    let option = select.querySelector('option[value="local"]');
    if (isLocalAsrUiEnabled()) {
        if (!option) {
            option = document.createElement('option');
            option.value = 'local';
            option.setAttribute('data-i18n', 'asr.local');
            select.appendChild(option);
        }
        option.textContent = t('asr.local');
    } else if (option) {
        if (select.value === 'local') {
            select.value = 'qwen_audio3';
        }
        option.remove();
    }
}

function sanitizeAsrBackendValue(value) {
    let normalized = value || 'qwen_audio3';
    // 已下线的后端自动迁移：qwen3-asr-flash-realtime 于 2026-10-10 由阿里云下线
    if (normalized === 'qwen') {
        normalized = 'qwen_audio3';
    }
    if (normalized === 'local' && !isLocalAsrUiEnabled()) {
        return 'qwen_audio3';
    }
    return normalized;
}

function applyOthersTranslationForm(ot) {
    if (!ot) return;
    const langEl = document.getElementById('others-target-language');
    if (langEl) {
        langEl.value = (ot.target_language || '').trim() || 'zh-CN';
    }
}

const SCREEN_OCR_PRESETS = {
    qwen: {
        baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        model: 'qwen3.5-ocr',
    },
    openai: {
        baseUrl: 'https://api.openai.com/v1',
        model: 'gpt-4o-mini',
    },
};

function applyScreenTranslationForm(sc) {
    if (!sc) return;
    const langEl = document.getElementById('screen-target-language');
    if (langEl) {
        langEl.value = (sc.target_language || '').trim() || 'zh-CN';
    }
    const hotkeyEl = document.getElementById('screen-hotkey');
    if (hotkeyEl && Object.prototype.hasOwnProperty.call(sc, 'hotkey')) {
        hotkeyEl.value = sc.hotkey || '';
    }
    const urlEl = document.getElementById('screen-ocr-base-url');
    if (urlEl && sc.ocr_base_url) {
        urlEl.value = sc.ocr_base_url;
    }
    const modelEl = document.getElementById('screen-ocr-model');
    if (modelEl && sc.ocr_model) {
        // qwen-vl-ocr-latest 已被阿里云下线（404 Model not exist），存过的旧名字换成稳定版
        modelEl.value = sc.ocr_model === 'qwen-vl-ocr-latest' ? 'qwen-vl-ocr' : sc.ocr_model;
    }
}

// effective=true：发给后端，按「API 配置」的模式取实际用的地址和模型；false：存浏览器，原样存兼容模式的框
function getScreenTranslationConfigFromForm(effective = false) {
    const plan = effective && window.GanlanApiMode ? window.GanlanApiMode.effective() : null;
    return {
        target_language: (document.getElementById('screen-target-language')?.value || '').trim() || 'zh-CN',
        hotkey: (document.getElementById('screen-hotkey')?.value || '').trim(),
        ocr_base_url: (plan ? plan.ocr.baseUrl : (document.getElementById('screen-ocr-base-url')?.value || '').trim())
            || SCREEN_OCR_PRESETS.qwen.baseUrl,
        ocr_model: (plan ? plan.ocr.model : (document.getElementById('screen-ocr-model')?.value || '').trim())
            || SCREEN_OCR_PRESETS.qwen.model,
    };
}

function applyScreenOcrPreset(name) {
    const preset = SCREEN_OCR_PRESETS[name];
    if (!preset) return;
    document.getElementById('screen-ocr-base-url').value = preset.baseUrl;
    document.getElementById('screen-ocr-model').value = preset.model;
    onSettingChange();
}

function getScreenOcrApiKey() {
    // 1.1.2：按「API 配置」的模式取（百炼模式留空 = 后端用百炼 Key）
    if (window.GanlanApiMode) return window.GanlanApiMode.effective().ocr.key;
    return (document.getElementById('screen-ocr-api-key')?.value || '').trim();
}

function renderLocalAsrStatus(payload) {
    const box = document.getElementById('local-asr-status');
    const button = document.getElementById('local-asr-download-btn');
    if (!box || !button) return;
    const t = window.i18n ? window.i18n.t : (key) => key;
    const engine = document.getElementById('local-asr-engine')?.value || 'sensevoice';
    const engineStatus = payload?.engines?.[engine];
    localAsrStatus = payload?.download || localAsrStatus;

    if (!payload || !engineStatus) {
        box.textContent = t('hint.localAsrNotChecked');
        button.disabled = false;
        return;
    }

    if (localAsrStatus.running) {
        box.textContent = `${t('status.downloading')}: ${localAsrStatus.status || ''}`;
        button.disabled = true;
        return;
    }

    if (engineStatus.ready) {
        box.textContent = t('status.localAsrReady', { engine: engineStatus.display_name || engine });
    } else {
        const issues = [];
        if (Array.isArray(engineStatus.runtime_issues) && engineStatus.runtime_issues.length) {
            issues.push(`${t('label.dependencies')}: ${engineStatus.runtime_issues.join(', ')}`);
        }
        if (engineStatus.error) {
            issues.push(engineStatus.error);
        }
        if (Array.isArray(engineStatus.missing) && engineStatus.missing.length) {
            issues.push(t('hint.localAsrNeedsDownload'));
        }
        box.textContent = issues.length
            ? issues.join(' | ')
            : t('hint.localAsrNeedsDownload');
        button.disabled = false;
    }
}

async function refreshLocalAsrStatus() {
    if (!isLocalAsrUiEnabled()) return;
    try {
        const response = await fetch(`${API_BASE}/local-asr/status`);
        if (!response.ok) return;
        const payload = await response.json();
        renderLocalAsrStatus(payload);
    } catch (error) {
        console.warn('获取本地 ASR 状态失败:', error);
    }
}

async function downloadLocalAsrModels() {
    const t = window.i18n ? window.i18n.t : (key) => key;
    if (!isLocalAsrUiEnabled()) return;
    const localConfig = getLocalAsrConfigFromForm();
    const button = document.getElementById('local-asr-download-btn');
    if (button) {
        button.disabled = true;
    }
    try {
        const response = await fetch(`${API_BASE}/local-asr/download`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                engine: localConfig.engine,
            }),
        });
        const result = await response.json();
        if (!response.ok || !result.success) {
            throw new Error(result.message || 'download failed');
        }
        showMessage(t('msg.localAsrDownloadStarted'), 'success');
        await refreshLocalAsrStatus();
    } catch (error) {
        console.error('下载本地 ASR 失败:', error);
        showMessage(t('msg.localAsrDownloadFailed') + ': ' + error.message, 'error');
        if (button) {
            button.disabled = false;
        }
    }
}

function updateLocalAsrUiVisibility() {
    const card = document.getElementById('local-asr-card');
    if (!card) return;
    ensureLocalAsrBackendOption();
    if (isLocalAsrUiEnabled()) {
        card.style.display = 'block';
        updateLocalAsrEngineHint();
        void refreshLocalAsrStatus();
    } else {
        card.style.display = 'none';
    }
}

function onLocalAsrSettingChange(changedElement = null) {
    if (!isLocalAsrUiEnabled()) return;
    if (changedElement && changedElement.id === 'local-asr-engine') {
        updateLocalAsrEngineHint();
        void refreshLocalAsrStatus();
    }
    onSettingChange(changedElement);
}

async function loadServerFeatures() {
    try {
        const response = await fetch(`${API_BASE}/features`);
        if (!response.ok) return;
        featureFlags = await response.json();
        updateLocalAsrUiVisibility();
    } catch (error) {
        console.warn('加载功能开关失败:', error);
    }
}

function isVrcxBridgeButtonVisible() {
    return localStorage.getItem(VRCX_BRIDGE_BUTTON_VISIBLE_STORAGE_KEY) === 'true';
}

function setVrcxBridgeStatusText(messageId, params = {}) {
    const statusEl = document.getElementById('vrcx-bridge-status');
    if (!statusEl) return;
    const t = window.i18n ? window.i18n.t : (key) => key;
    statusEl.textContent = t(messageId, params);
    statusEl.setAttribute('data-i18n', messageId);
}

function updateVrcxBridgeControlsVisibility() {
    const visible = isVrcxBridgeButtonVisible();
    const toggle = document.getElementById('show-vrcx-bridge-button');
    const controls = document.getElementById('vrcx-bridge-controls');
    if (toggle) {
        toggle.checked = visible;
    }
    if (controls) {
        controls.style.display = visible ? 'flex' : 'none';
    }
    if (visible) {
        void refreshVrcxBridgeStatus();
    }
}

function onVrcxBridgeVisibilityChange() {
    const toggle = document.getElementById('show-vrcx-bridge-button');
    localStorage.setItem(
        VRCX_BRIDGE_BUTTON_VISIBLE_STORAGE_KEY,
        toggle && toggle.checked ? 'true' : 'false',
    );
    updateVrcxBridgeControlsVisibility();
}

function loadVrcxBridgeUiPreference() {
    if (!localStorage.getItem(VRCX_BRIDGE_BUTTON_VISIBLE_STORAGE_KEY)) {
        localStorage.setItem(VRCX_BRIDGE_BUTTON_VISIBLE_STORAGE_KEY, 'false');
    }
    updateVrcxBridgeControlsVisibility();
}

async function writeTextToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (error) {
            console.warn('Clipboard API failed, using fallback:', error);
        }
    }

    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    textarea.style.top = '0';
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    let ok = false;
    try {
        ok = document.execCommand('copy');
    } finally {
        document.body.removeChild(textarea);
    }
    return ok;
}

async function copyVrcxBridgeScript() {
    const button = document.getElementById('copy-vrcx-bridge-btn');
    const t = window.i18n ? window.i18n.t : (key) => key;
    if (button) {
        button.disabled = true;
        button.textContent = t('btn.copying');
    }

    try {
        const response = await fetch(`${API_BASE}/vrcx-bridge/script`, { cache: 'no-store' });
        const payload = await response.json();
        if (!response.ok || !payload.success || !payload.script) {
            throw new Error(payload.message || 'script unavailable');
        }

        const copied = await writeTextToClipboard(payload.script);
        if (!copied) {
            throw new Error('clipboard unavailable');
        }
        setVrcxBridgeStatusText('status.vrcxBridgeWaiting');
        showLocalizedMessage('msg.vrcxBridgeScriptCopied', 'success');
        void refreshVrcxBridgeStatus();
    } catch (error) {
        console.error('复制 VRCX 上下文脚本失败:', error);
        showLocalizedMessage('msg.vrcxBridgeScriptCopyFailed', 'error');
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = t('btn.copyVrcxBridgeScript');
        }
    }
}

async function refreshVrcxBridgeStatus() {
    if (!isVrcxBridgeButtonVisible()) return;
    try {
        const response = await fetch(`${API_BASE}/vrcx-bridge/status`, { cache: 'no-store' });
        if (!response.ok) return;
        const status = await response.json();
        if (status.connected) {
            const ageSeconds = Math.max(0, Math.round((status.ageMs || 0) / 1000));
            setVrcxBridgeStatusText('status.vrcxBridgeConnected', { seconds: ageSeconds });
        } else if (status.ageMs != null) {
            setVrcxBridgeStatusText('status.vrcxBridgeStale');
        } else {
            setVrcxBridgeStatusText('status.vrcxBridgeIdle');
        }
    } catch (error) {
        console.warn('获取 VRCX 上下文桥接状态失败:', error);
    }
}

const LANGUAGE_OPTIONS = [
    { code: 'zh-CN', labelKey: 'lang.zhCN' },
    { code: 'zh-TW', labelKey: 'lang.zhTW' },
    { code: 'en', labelKey: 'lang.en' },
    { code: 'en-GB', labelKey: 'lang.enGB' },
    { code: 'ja', labelKey: 'lang.ja' },
    { code: 'ko', labelKey: 'lang.ko' },
    { code: 'ar', labelKey: 'lang.ar' },
    { code: 'de', labelKey: 'lang.de' },
    { code: 'es', labelKey: 'lang.es' },
    { code: 'fr', labelKey: 'lang.fr' },
    { code: 'id', labelKey: 'lang.id' },
    { code: 'it', labelKey: 'lang.it' },
    { code: 'pt', labelKey: 'lang.pt' },
    { code: 'ru', labelKey: 'lang.ru' },
    { code: 'th', labelKey: 'lang.th' },
    { code: 'tl', labelKey: 'lang.tl' },
    { code: 'tr', labelKey: 'lang.tr' },
];

/** 语音识别源语言下拉：中文/英文不展示地区变体（仅 zh、en） */
const SOURCE_LANGUAGE_COMBO_OPTIONS = [
    { code: 'zh', labelKey: 'lang.asrZh' },
    { code: 'en', labelKey: 'lang.en' },
    { code: 'ja', labelKey: 'lang.ja' },
    { code: 'ko', labelKey: 'lang.ko' },
    { code: 'ar', labelKey: 'lang.ar' },
    { code: 'de', labelKey: 'lang.de' },
    { code: 'es', labelKey: 'lang.es' },
    { code: 'fr', labelKey: 'lang.fr' },
    { code: 'id', labelKey: 'lang.id' },
    { code: 'it', labelKey: 'lang.it' },
    { code: 'pt', labelKey: 'lang.pt' },
    { code: 'ru', labelKey: 'lang.ru' },
    { code: 'th', labelKey: 'lang.th' },
    { code: 'tl', labelKey: 'lang.tl' },
    { code: 'tr', labelKey: 'lang.tr' },
];

function normalizeSourceLanguageInputValue(stored) {
    const s = stored == null ? '' : String(stored).trim();
    const lower = s.toLowerCase();
    if (!s || lower === 'auto' || lower === 'auto-detect') {
        return '';
    }
    if (lower === 'zh' || lower === 'zh-cn' || lower === 'zh-tw' || lower === 'zh-hans' || lower === 'zh-hant') {
        return 'zh';
    }
    if (lower === 'en' || lower === 'en-gb' || lower === 'en-us') {
        return 'en';
    }
    return s;
}

function getSourceLanguageEffective() {
    const el = document.getElementById('source-language');
    if (!el) return 'auto';
    const raw = el.value.trim();
    const lower = raw.toLowerCase();
    if (!raw || lower === 'auto' || lower === 'auto-detect') {
        return 'auto';
    }
    if (lower === 'zh' || lower === 'zh-cn' || lower === 'zh-tw' || lower === 'zh-hans' || lower === 'zh-hant') {
        return 'zh';
    }
    if (lower === 'en' || lower === 'en-gb' || lower === 'en-us') {
        return 'en';
    }
    return raw;
}

function normalizeSourceLanguageInputOnBlur(input) {
    const v = input.value.trim().toLowerCase();
    if (!v || v === 'auto' || v === 'auto-detect') {
        input.value = '';
    } else if (v === 'zh-cn' || v === 'zh-tw' || v === 'zh-hans' || v === 'zh-hant') {
        input.value = 'zh';
    } else if (v === 'en-gb' || v === 'en-us') {
        input.value = 'en';
    }
}

function applySourceLanguageInputFromStored(stored) {
    const el = document.getElementById('source-language');
    if (!el) return;
    el.value = normalizeSourceLanguageInputValue(stored);
}

/** 手动指定的源语言是否为 zh / en / ja / ko（不含 auto） */
function sourceLanguageIsCjkeFamily(effective) {
    if (effective === 'auto' || !effective) {
        return false;
    }
    const norm = String(effective).trim().toLowerCase();
    return norm === 'zh' || norm === 'en' || norm === 'ja' || norm === 'ko';
}

/** 浏览器首选界面语言是否为中英日韩；无法读取时返回 undefined */
function isBrowserPrimaryLanguageCjke() {
    if (typeof navigator === 'undefined') {
        return undefined;
    }
    const list = Array.isArray(navigator.languages) && navigator.languages.length
        ? navigator.languages
        : [navigator.language || navigator.userLanguage || ''].filter(Boolean);
    if (!list.length) {
        return undefined;
    }
    const raw = String(list[0]).trim();
    if (!raw) {
        return undefined;
    }
    const s = raw.toLowerCase().replace(/_/g, '-');
    if (s === 'zh' || s.startsWith('zh-')) return true;
    if (s === 'en' || s.startsWith('en-')) return true;
    if (s === 'ja' || s.startsWith('ja-')) return true;
    if (s === 'ko' || s.startsWith('ko-')) return true;
    return false;
}

function shouldAutoUseGenericLanguageDetector() {
    const primCjke = isBrowserPrimaryLanguageCjke();
    if (primCjke === false) {
        return true;
    }
    const effective = getSourceLanguageEffective();
    if (effective !== 'auto' && !sourceLanguageIsCjkeFamily(effective)) {
        return true;
    }
    return false;
}

/** 与中日韩英检测器匹配：手动选了中英日韩，或自动检测且浏览器首选为中英日韩 */
function shouldAutoUseCjkeLanguageDetector() {
    if (shouldAutoUseGenericLanguageDetector()) {
        return false;
    }
    const effective = getSourceLanguageEffective();
    if (sourceLanguageIsCjkeFamily(effective)) {
        return true;
    }
    if (effective === 'auto' && isBrowserPrimaryLanguageCjke() === true) {
        return true;
    }
    return false;
}

/** 按源语言 / 浏览器语言自动在 fasttext 与 cjke 之间切换；其它类型（如 enzh）在命中规则时也会被覆盖 */
function applyAutoLanguageDetectorIfNeeded() {
    const sel = document.getElementById('language-detector');
    if (!sel) return false;
    if (shouldAutoUseGenericLanguageDetector()) {
        if (sel.value === 'fasttext') return false;
        sel.value = 'fasttext';
        return true;
    }
    if (shouldAutoUseCjkeLanguageDetector()) {
        if (sel.value === 'cjke') return false;
        sel.value = 'cjke';
        return true;
    }
    return false;
}

const LLM_PARALLEL_FASTEST_MODES = ['off', 'final_only', 'all'];
const DEFAULT_QWEN_AUDIO3_MODEL = 'qwen-audio-3.1-asr-flash-streaming';

function resolveLLMParallelFastestModeFromStoredTranslation(trans) {
    if (!trans) return 'off';
    const raw = trans.llm_parallel_fastest_mode;
    if (LLM_PARALLEL_FASTEST_MODES.includes(raw)) {
        return raw;
    }
    if (typeof trans.enable_llm_parallel_fastest === 'boolean') {
        return trans.enable_llm_parallel_fastest ? 'final_only' : 'off';
    }
    return 'off';
}

function setLLMParallelFastestModeSelect(value) {
    const el = document.getElementById('llm-parallel-fastest-mode');
    if (!el) return;
    el.value = LLM_PARALLEL_FASTEST_MODES.includes(value) ? value : 'off';
}

function getLLMParallelFastestModeSelect() {
    const el = document.getElementById('llm-parallel-fastest-mode');
    if (!el) return 'off';
    return LLM_PARALLEL_FASTEST_MODES.includes(el.value) ? el.value : 'off';
}

/** 发给后端的百炼 Key：兼容模式走百炼实时识别时是语音识别那块填的 Key */
function getEffectiveDashscopeKey() {
    if (window.GanlanApiMode) return window.GanlanApiMode.effective().asrDashscopeKey || '';
    return (document.getElementById('dashscope-api-key')?.value || '').trim();
}

function getEffectiveLLMApiKeyForCurrentMode() {
    if (window.GanlanApiMode) return window.GanlanApiMode.effective().llm.key;
    return (document.getElementById('llm-api-key')?.value || '').trim();
}

/**
 * 翻译只剩大模型（隐藏的 translation-api-type 只有 openrouter）。存的 api_type 只决定「流式翻译模式」勾不勾：
 * 明确存成 openrouter（非流式）才不勾，其余（openrouter_streaming、没存、以前的 DeepL 混合等）都是流式。
 * 以前这里两种都勾上，取消勾选后一刷新又勾回去了。
 */
function isStreamingStoredApiType(apiType) {
    return apiType !== 'openrouter';
}

function updateLLMStreamingPromoState() {
    const switchBtn = document.getElementById('switch-to-llm-streaming-btn');
    if (!switchBtn) return;
    const t = window.i18n ? window.i18n.t : (key) => key;
    if (isUsingLLMStreamingTranslation()) {
        switchBtn.disabled = true;
        switchBtn.setAttribute('data-i18n', 'feature.alreadyUsingLlmStreaming');
        switchBtn.textContent = t('feature.alreadyUsingLlmStreaming');
    } else {
        switchBtn.disabled = false;
        switchBtn.setAttribute('data-i18n', 'feature.switchToLlmStreaming');
        switchBtn.textContent = t('feature.switchToLlmStreaming');
    }
}

function updateLLMSettingsVisibility(apiType = null, expandPanel = false) {
    const wrapper = document.getElementById('llm-settings-wrapper');
    const formalityGroup = document.getElementById('llm-translation-formality-group');
    const styleGroup = document.getElementById('llm-translation-style-group');
    const toneGrid = document.getElementById('llm-tone-grid');
    if (!wrapper || !formalityGroup || !styleGroup || !toneGrid) return;

    wrapper.style.display = 'block';
    wrapper.classList.remove('hidden');
    toneGrid.style.display = 'grid';
    toneGrid.classList.remove('hidden');
    formalityGroup.style.display = 'block';
    formalityGroup.classList.remove('hidden');
    styleGroup.style.display = 'block';
    styleGroup.classList.remove('hidden');

    if (expandPanel) {
        ensureCollapsibleExpanded('llm-settings');
    }
}

const VALID_LLM_TRANSLATION_FORMALITY = ['low', 'medium', 'high'];
const VALID_LLM_TRANSLATION_STYLE = ['standard', 'light'];
const DEFAULT_LLM_TRANSLATION_FORMALITY = 'medium';
const DEFAULT_LLM_TRANSLATION_STYLE = 'standard';

function sanitizeLLMTranslationFormality(value) {
    const normalized = (value || DEFAULT_LLM_TRANSLATION_FORMALITY).trim().toLowerCase();
    return VALID_LLM_TRANSLATION_FORMALITY.includes(normalized)
        ? normalized
        : DEFAULT_LLM_TRANSLATION_FORMALITY;
}

function sanitizeLLMTranslationStyle(value) {
    const normalized = (value || DEFAULT_LLM_TRANSLATION_STYLE).trim().toLowerCase();
    return VALID_LLM_TRANSLATION_STYLE.includes(normalized)
        ? normalized
        : DEFAULT_LLM_TRANSLATION_STYLE;
}

/** LLM 地址、模型、Key 是否已就绪（与启动校验一致：Key 可为环境/后端已设置而输入框为空）。 */
function isLLMConnectionFieldsComplete() {
    const plan = window.GanlanApiMode ? window.GanlanApiMode.effective().llm : null;
    const base = plan ? plan.baseUrl : (document.getElementById('llm-base-url')?.value || '').trim();
    const model = plan ? plan.model : (document.getElementById('llm-model')?.value || '').trim();
    const llmKey = plan ? plan.key : (document.getElementById('llm-api-key')?.value || '').trim();
    const hasKey = runtimeCredentialStatus.llm.api_key_set || !!llmKey;
    return !!(base && model && hasKey);
}

/** 同步「流式翻译模式」分组：始终显示（翻译仅大模型）。 */
function updateOpenRouterStreamingUi() {
    const streamingModeGroup = document.getElementById('openrouter-streaming-mode-group');
    const streamingMode = document.getElementById('openrouter-streaming-mode');
    if (!streamingModeGroup || !streamingMode) return;

    streamingModeGroup.style.display = 'block';
    streamingModeGroup.classList.remove('hidden');
    streamingMode.disabled = false;
    updateLLMStreamingPromoState();
}

function isUsingLLMStreamingTranslation() {
    const apiType = document.getElementById('translation-api-type')?.value ?? '';
    const streamingMode = document.getElementById('openrouter-streaming-mode');
    return apiType === 'openrouter' && !!streamingMode?.checked;
}

/** Qwen ASR / Qwen-Audio-3.0 / Fun-ASR / Qwen-MT 依赖 DashScope Key，其余场景不校验 */
function currentConfigRequiresDashscopeKey() {
    const asrBackend = document.getElementById('asr-backend')?.value;
    const enableTranslation = document.getElementById('enable-translation')?.checked ?? false;
    const translationApiType = document.getElementById('translation-api-type')?.value ?? '';
    const byAsr = asrBackend === 'qwen' || asrBackend === 'qwen_audio3' || asrBackend === 'dashscope';
    const byTranslation = enableTranslation && translationApiType === 'qwen_mt';
    return byAsr || byTranslation;
}

function updateDashscopeKeyFieldState() {
    const badge = document.getElementById('dashscope-key-required-badge');
    const input = document.getElementById('dashscope-api-key');
    if (!input) return;
    const need = currentConfigRequiresDashscopeKey();
    if (badge) {
        badge.hidden = true;
        badge.textContent = '';
        badge.removeAttribute('data-i18n');
    }
    input.required = !!need;
}

function ensureCollapsibleExpanded(id) {
    const content = document.getElementById(id);
    if (content && content.classList.contains('collapsed')) {
        toggleCollapsible(id);
    }
}

function highlightInput(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    input.classList.add('error-highlight');
    input.focus();
    setTimeout(() => {
        input.classList.remove('error-highlight');
    }, 3000);
}

function expandLLMSettingsPanel(inputIds = []) {
    if (window.GanlanApiMode) {
        window.GanlanApiMode.reveal(inputIds);
        return;
    }
    const wrapper = document.getElementById('llm-settings-wrapper');
    ensureCollapsibleExpanded('translation-api');
    if (wrapper) {
        wrapper.style.display = 'block';
        wrapper.classList.add('error-highlight');
        setTimeout(() => wrapper.classList.remove('error-highlight'), 3000);
    }
    ensureCollapsibleExpanded('llm-settings');
    inputIds.forEach(highlightInput);
}

function persistSecretInputValue(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    const keyName = inputId.replace(/-/g, '_');
    const value = input.value;
    if (value) {
        localStorage.setItem(keyName, value);
    } else {
        localStorage.removeItem(keyName);
    }
}

function updateSecretVisibilityToggleState(toggleButton, targetInput) {
    if (!toggleButton || !targetInput) return;

    const isVisible = targetInput.type === 'text';
    toggleButton.classList.toggle('visible', isVisible);
    toggleButton.setAttribute('aria-pressed', isVisible ? 'true' : 'false');
    toggleButton.setAttribute('aria-label', isVisible ? 'Hide key' : 'Show key');
    toggleButton.title = isVisible ? 'Hide key' : 'Show key';
}

function setupSecretVisibilityToggles() {
    document.querySelectorAll('.secret-visibility-toggle[data-target-input]').forEach((toggleButton) => {
        const targetInputId = toggleButton.getAttribute('data-target-input');
        const targetInput = targetInputId ? document.getElementById(targetInputId) : null;
        if (!targetInput) return;

        updateSecretVisibilityToggleState(toggleButton, targetInput);

        if (toggleButton.dataset.bound === 'true') {
            return;
        }

        toggleButton.addEventListener('click', () => {
            targetInput.type = targetInput.type === 'password' ? 'text' : 'password';
            updateSecretVisibilityToggleState(toggleButton, targetInput);
        });

        toggleButton.dataset.bound = 'true';
    });
}

function loadPanelFloatingModeSetting() {
    const toggle = document.getElementById('panel-floating-mode');
    if (!toggle) return;

    const storedValue = localStorage.getItem(PANEL_FLOATING_MODE_STORAGE_KEY);
    toggle.checked = storedValue === null ? PANEL_FLOATING_MODE_DEFAULT : storedValue === 'true';
    refreshPanelSettingMirrors();
}

function savePanelFloatingModeSetting() {
    const toggle = document.getElementById('panel-floating-mode');
    if (!toggle) return;

    localStorage.setItem(PANEL_FLOATING_MODE_STORAGE_KEY, toggle.checked.toString());
}

function syncPanelSettingMirror(el) {
    if (!el) return;
    const targetId = el.getAttribute('data-mirror-of');
    if (!targetId) return;
    const primary = document.getElementById(targetId);
    if (!primary || primary === el) return;
    if (el.type === 'checkbox') {
        primary.checked = el.checked;
    } else {
        primary.value = el.value;
    }
}

function refreshPanelSettingMirrors() {
    document.querySelectorAll('.panel-setting-mirror[data-mirror-of]').forEach((mirror) => {
        const primary = document.getElementById(mirror.getAttribute('data-mirror-of'));
        if (!primary) return;
        if (mirror.type === 'checkbox') {
            mirror.checked = !!primary.checked;
        } else {
            mirror.value = primary.value || '';
        }
    });
}

function onPanelFloatingModeChange() {
    // Keep mirrors in sync when toggled from either page
    const primary = document.getElementById('panel-floating-mode');
    document.querySelectorAll('[data-mirror-of="panel-floating-mode"]').forEach((m) => {
        if (primary) m.checked = primary.checked;
    });
    savePanelFloatingModeSetting();
}

function resetPanelFloatingModeSetting() {
    const toggle = document.getElementById('panel-floating-mode');
    if (toggle) {
        toggle.checked = PANEL_FLOATING_MODE_DEFAULT;
    }

    localStorage.removeItem(PANEL_FLOATING_MODE_STORAGE_KEY);
}

function getNormalizedPanelWidth() {
    const input = document.getElementById('panel-width');
    if (!input) return 600;

    const rawValue = parseInt(input.value, 10);
    const normalizedValue = Math.min(2000, Math.max(300, Number.isFinite(rawValue) ? rawValue : 600));
    input.value = normalizedValue;
    return normalizedValue;
}

function getOscSendTargetPortFromForm() {
    const input = document.getElementById('osc-send-target-port');
    if (!input) return 9000;
    const raw = parseInt(input.value, 10);
    const p = Math.max(1, Math.min(65535, Number.isFinite(raw) ? raw : 9000));
    input.value = p;
    return p;
}

function isOscCompatModeEnabled() {
    return document.getElementById('osc-compat-mode')?.checked === true;
}

function getOscCompatListenPortFromForm() {
    const input = document.getElementById('osc-compat-listen-port');
    if (!input) return 9001;
    const raw = parseInt(input.value, 10);
    const p = Math.max(1, Math.min(65535, Number.isFinite(raw) ? raw : 9001));
    input.value = p;
    return p;
}

function updateOscCompatModeUi() {
    const compatEnabled = isOscCompatModeEnabled();
    const compatPortGroup = document.getElementById('osc-compat-listen-port-group');
    if (compatPortGroup) {
        compatPortGroup.style.display = compatEnabled ? 'block' : 'none';
    }

    const compatPortInput = document.getElementById('osc-compat-listen-port');
    if (compatPortInput) {
        compatPortInput.disabled = !compatEnabled;
        getOscCompatListenPortFromForm();
    }

    const bypassOscEl = document.getElementById('bypass-osc-udp-port-check');
    const bypassOscGroup = document.getElementById('bypass-osc-udp-port-check-group');
    if (bypassOscEl) {
        bypassOscEl.disabled = compatEnabled;
    }
    if (bypassOscGroup) {
        bypassOscGroup.classList.toggle('disabled-option', compatEnabled);
    }
}

function shouldSkipOscUdpPortCheck() {
    return isOscCompatModeEnabled()
        || document.getElementById('bypass-osc-udp-port-check')?.checked === true;
}

function closeLanguageMenus(exceptCombo = null) {
    document.querySelectorAll('.language-combo.open').forEach((combo) => {
        if (combo !== exceptCombo) {
            combo.classList.remove('open');
        }
    });
}

function refreshLanguageComboClearLabels() {
    const t = window.i18n ? window.i18n.t : (key) => key;
    const label = t('btn.clearLanguageInput');
    document.querySelectorAll('.language-combo-clear').forEach((btn) => {
        btn.setAttribute('aria-label', label);
        btn.title = label;
    });
}

function syncLanguageComboClearVisibility(combo) {
    if (!combo) return;
    const input = combo.querySelector('.language-combo-input');
    const clearBtn = combo.querySelector('.language-combo-clear');
    if (!input || !clearBtn) return;
    clearBtn.hidden = input.value.trim() === '';
}

function syncAllLanguageComboClearButtons() {
    document.querySelectorAll('.language-combo').forEach(syncLanguageComboClearVisibility);
}

function renderLanguageComboMenu(combo) {
    if (!combo) return;

    const input = combo.querySelector('.language-combo-input');
    const menu = combo.querySelector('.language-combo-menu');
    const t = window.i18n ? window.i18n.t : (key) => key;

    if (!input || !menu) return;

    const currentValue = input.value.trim().toLowerCase();
    menu.innerHTML = '';

    if (input.id === 'secondary-target-language' || input.id === 'fallback-language') {
        const noneButton = document.createElement('button');
        noneButton.type = 'button';
        noneButton.className = 'language-combo-option';
        if (!currentValue) {
            noneButton.classList.add('active');
        }

        const noneCode = document.createElement('span');
        noneCode.className = 'language-combo-option-plain';
        noneCode.textContent = t('select.none');
        noneButton.appendChild(noneCode);

        noneButton.addEventListener('mousedown', (event) => {
            event.preventDefault();
        });

        noneButton.addEventListener('click', () => {
            input.value = '';
            syncLanguageComboClearVisibility(combo);
            renderLanguageComboMenu(combo);
            closeLanguageMenus();
            onSettingChange(input);
            input.focus();
        });

        menu.appendChild(noneButton);
    }

    if (input.id === 'source-language') {
        const autoButton = document.createElement('button');
        autoButton.type = 'button';
        autoButton.className = 'language-combo-option';
        const isAuto = !currentValue || currentValue === 'auto' || currentValue === 'auto-detect';
        if (isAuto) {
            autoButton.classList.add('active');
        }

        const autoCode = document.createElement('span');
        autoCode.className = 'language-combo-option-code';
        autoCode.textContent = 'auto';

        const autoLabel = document.createElement('span');
        autoLabel.className = 'language-combo-option-label';
        autoLabel.textContent = t('sourceLang.auto');

        autoButton.appendChild(autoCode);
        autoButton.appendChild(autoLabel);

        autoButton.addEventListener('mousedown', (event) => {
            event.preventDefault();
        });

        autoButton.addEventListener('click', () => {
            input.value = '';
            syncLanguageComboClearVisibility(combo);
            renderLanguageComboMenu(combo);
            closeLanguageMenus();
            onSettingChange(input);
            input.focus();
        });

        menu.appendChild(autoButton);
    }

    const comboLanguageOptions = input.id === 'source-language' ? SOURCE_LANGUAGE_COMBO_OPTIONS : LANGUAGE_OPTIONS;

    comboLanguageOptions.forEach((option) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'language-combo-option';
        let isActive = option.code.toLowerCase() === currentValue;
        if (input.id === 'source-language') {
            const oc = option.code.toLowerCase();
            if (oc === 'zh') {
                isActive = ['zh', 'zh-cn', 'zh-tw', 'zh-hans', 'zh-hant'].includes(currentValue);
            } else if (oc === 'en') {
                isActive = currentValue === 'en' || currentValue === 'en-gb' || currentValue === 'en-us';
            }
        }
        if (isActive) {
            button.classList.add('active');
        }

        const code = document.createElement('span');
        code.className = 'language-combo-option-code';
        code.textContent = option.code;

        const label = document.createElement('span');
        label.className = 'language-combo-option-label';
        label.textContent = t(option.labelKey);

        button.appendChild(code);
        button.appendChild(label);

        button.addEventListener('mousedown', (event) => {
            event.preventDefault();
        });

        button.addEventListener('click', () => {
            input.value = option.code;
            syncLanguageComboClearVisibility(combo);
            renderLanguageComboMenu(combo);
            closeLanguageMenus();
            if (input.id === 'target-language') {
                updateFuriganaVisibility();
            }
            if (input.id.startsWith('quick-lang-')) {
                onQuickLangChange();
            } else {
                onSettingChange(input);
            }
            input.focus();
        });

        menu.appendChild(button);
    });
}

function renderLanguageComboMenus() {
    document.querySelectorAll('.language-combo').forEach((combo) => {
        renderLanguageComboMenu(combo);
    });
}

function setupLanguageComboboxes() {
    document.querySelectorAll('.language-combo').forEach((combo) => {
        if (combo.dataset.initialized === 'true') {
            return;
        }

        const input = combo.querySelector('.language-combo-input');
        const toggle = combo.querySelector('.language-combo-toggle');

        if (!input || !toggle) {
            return;
        }

        // 模板里没有写候选菜单容器，这里补上，否则下拉按钮点了没反应
        if (!combo.querySelector('.language-combo-menu')) {
            const menu = document.createElement('div');
            menu.className = 'language-combo-menu';
            menu.setAttribute('role', 'listbox');
            combo.appendChild(menu);
        }

        const openMenu = () => {
            closeLanguageMenus(combo);
            renderLanguageComboMenu(combo);
            combo.classList.add('open');
        };

        toggle.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            if (combo.classList.contains('open')) {
                combo.classList.remove('open');
            } else {
                openMenu();
            }
            input.focus();
        });

        input.addEventListener('input', () => {
            syncLanguageComboClearVisibility(combo);
            if (combo.classList.contains('open')) {
                renderLanguageComboMenu(combo);
            }
        });

        input.addEventListener('keydown', (event) => {
            if (event.key === 'ArrowDown') {
                event.preventDefault();
                openMenu();
            } else if (event.key === 'Escape') {
                combo.classList.remove('open');
            }
        });

        const clearBtn = combo.querySelector('.language-combo-clear');
        if (clearBtn) {
            clearBtn.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                input.value = '';
                syncLanguageComboClearVisibility(combo);
                closeLanguageMenus(combo);
                renderLanguageComboMenu(combo);
                onSettingChange(input);
                input.focus();
            });
        }

        if (input.id === 'source-language') {
            input.addEventListener('blur', () => {
                normalizeSourceLanguageInputOnBlur(input);
                syncLanguageComboClearVisibility(combo);
                onSettingChange(input);
            });
        }

        syncLanguageComboClearVisibility(combo);

        combo.dataset.initialized = 'true';
    });

    refreshLanguageComboClearLabels();
    renderLanguageComboMenus();
}

const QUICK_LANG_STORAGE_KEY = 'panel_quick_languages';
const QUICK_LANG_BAR_ENABLED_KEY = 'panel_quick_lang_bar_enabled';
const QUICK_LANG_DEFAULTS = ['en', 'zh-CN', 'ja', 'ko'];

function loadQuickLanguageSettings() {
    try {
        const toggle = document.getElementById('enable-quick-lang-bar');
        if (toggle) {
            const storedEnabled = localStorage.getItem(QUICK_LANG_BAR_ENABLED_KEY);
            toggle.checked = storedEnabled === null ? true : storedEnabled === 'true';
        }

        const stored = localStorage.getItem(QUICK_LANG_STORAGE_KEY);
        let langs = QUICK_LANG_DEFAULTS;
        if (stored) {
            const parsed = JSON.parse(stored);
            if (Array.isArray(parsed) && parsed.length === 4) {
                langs = parsed;
            }
        }
        for (let i = 0; i < 4; i++) {
            const input = document.getElementById(`quick-lang-${i + 1}`);
            if (input) {
                input.value = langs[i] || QUICK_LANG_DEFAULTS[i];
            }
        }
        refreshPanelSettingMirrors();
    } catch (e) {
        console.warn('Failed to load quick language settings:', e);
    }
}

function saveQuickLanguageSettings() {
    const langs = [];
    for (let i = 0; i < 4; i++) {
        const input = document.getElementById(`quick-lang-${i + 1}`);
        langs.push(input ? input.value.trim() || QUICK_LANG_DEFAULTS[i] : QUICK_LANG_DEFAULTS[i]);
    }
    localStorage.setItem(QUICK_LANG_STORAGE_KEY, JSON.stringify(langs));
}

function resetQuickLanguageSettings() {
    for (let i = 0; i < 4; i++) {
        const input = document.getElementById(`quick-lang-${i + 1}`);
        if (input) {
            input.value = QUICK_LANG_DEFAULTS[i];
        }
    }

    const toggle = document.getElementById('enable-quick-lang-bar');
    if (toggle) {
        toggle.checked = true;
    }

    localStorage.setItem(QUICK_LANG_STORAGE_KEY, JSON.stringify(QUICK_LANG_DEFAULTS));
    localStorage.removeItem(QUICK_LANG_BAR_ENABLED_KEY);
}

function onQuickLangChange() {
    saveQuickLanguageSettings();
    refreshPanelSettingMirrors();
}

function onEnableQuickLangBarChange() {
    const toggle = document.getElementById('enable-quick-lang-bar');
    if (!toggle) return;
    localStorage.setItem(QUICK_LANG_BAR_ENABLED_KEY, toggle.checked.toString());
    refreshPanelSettingMirrors();
}

function getQuickLanguageSettingsForPanel() {
    const languages = [];
    for (let i = 0; i < 4; i++) {
        const input = document.getElementById(`quick-lang-${i + 1}`);
        languages.push(input ? input.value.trim() || QUICK_LANG_DEFAULTS[i] : QUICK_LANG_DEFAULTS[i]);
    }

    const toggle = document.getElementById('enable-quick-lang-bar');
    return {
        enabled: toggle ? toggle.checked : true,
        languages,
    };
}
// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', async function () {
    // 先初始化 i18n 系统
    if (window.i18n) {
        window.i18n.initI18n();
    }
    applyDisplayMode(currentDisplayMode);

    const toastArea = document.getElementById('toast-area');
    if (toastArea) {
        toastArea.addEventListener('click', (event) => {
            const closeBtn = event.target.closest('.message-close');
            if (!closeBtn) return;
            const messageEl = closeBtn.closest('.message');
            if (messageEl) {
                dismissToastMessage(messageEl);
            }
        });
    }

    setupLanguageComboboxes();
    decorateRestartRequiredFields();
    await loadServerFeatures();

    document.addEventListener('click', function (event) {
        if (!event.target.closest('.language-combo')) {
            closeLanguageMenus();
        }
    });

    const targetLangInput = document.getElementById('target-language');
    if (targetLangInput) {
        targetLangInput.addEventListener('input', updateFuriganaVisibility);
    }

    const hadSavedConfig = !!localStorage.getItem(CONFIG_STORAGE_KEY);
    loadConfigFromLocalStorage();
    if (hadSavedConfig) {
        await reconcileMainConfigWithServerAtStartup();
    } else {
        await loadConfigFromServer();
    }

    loadPanelFloatingModeSetting();
    loadQuickLanguageSettings();
    loadVrcxBridgeUiPreference();
    loadAPIKeys();
    if (window.GanlanApiMode) window.GanlanApiMode.onConfigReady();
    initializeCollapsibleStates();
    setupSecretVisibilityToggles();
    applyAsrBackendLocks();
    updateOscCompatModeUi();
    loadEnvStatus();
    refreshMicDevices(true);
    setupMicDeviceAutoRefresh();
    updateDashscopeKeyFieldState();
    const statusLoaded = await updateStatus();
    if (!statusLoaded) {
        const startBtn = document.getElementById('start-btn');
        const t = window.i18n ? window.i18n.t : (key) => key;
        if (startBtn) {
            setElementI18nText(startBtn, 'btn.startAllModes', t);
            startBtn.disabled = false;
        }
    }
    updateIpcStatus();
    // 每2秒更新一次状态
    setInterval(updateStatus, 2000);
    setInterval(updateIpcStatus, 2500);
    setInterval(refreshVrcxBridgeStatus, 5000);
    setInterval(() => {
        if (isLocalAsrUiEnabled()) {
            void refreshLocalAsrStatus();
        }
    }, 3000);

    // 显示配置保存提示
    showConfigStorageInfo();

    const switchLlmStreamingBtn = document.getElementById('switch-to-llm-streaming-btn');
    if (switchLlmStreamingBtn) {
        switchLlmStreamingBtn.addEventListener('click', switchToLLMStreamingTranslation);
    }
});

document.addEventListener('i18n:languageChanged', function () {
    const useInternational = document.getElementById('use-international-endpoint')?.checked ?? false;
    updateAsrOptionsForInternational(useInternational);
    updateLocalAsrEngineHint();
    updateLocalAsrUiVisibility();
    renderLanguageComboMenus();
    refreshLanguageComboClearLabels();
    refreshMicDevices(true);
    updateDashscopeKeyFieldState();
    updateStatus();
    refreshVrcxBridgeStatus();
});

// 从服务器加载进程内凭据状态
async function loadEnvStatus() {
    try {
        const response = await fetch(`${API_BASE}/credentials/status`);
        if (!response.ok) return;
        const data = await response.json();
        if (data && data.llm) {
            runtimeCredentialStatus.llm.api_key_set = !!data.llm.api_key_set;
        }
    } catch (e) {
        // 静默失败：不影响其它功能
        console.warn('获取运行凭据状态失败:', e);
    }
}

/**
 * 「系统默认」后面显示的设备名：优先用列表里同一设备的完整名字。
 */
function resolveDefaultMicDisplayName(devices, defaultId, defaultNameApi) {
    const api = (defaultNameApi || '').trim();
    const fromId = defaultId ? devices.find((ad) => ad.id === defaultId) : null;
    if (fromId && fromId.name) {
        return String(fromId.name).trim();
    }
    if (!api) return '';
    const exact = devices.find((d) => d.name && String(d.name).trim() === api);
    if (exact && exact.name) {
        return String(exact.name).trim();
    }
    let best = '';
    for (const d of devices) {
        const n = d.name ? String(d.name).trim() : '';
        if (!n || !n.startsWith(api)) continue;
        if (n.length > best.length) best = n;
    }
    return best || api;
}

// 设置麦克风下拉框的值。页面加载时先读配置、后拉设备列表，列表还没回来时
// 先放一个占位选项，不然选好的麦克风会被清成「系统默认」
function setMicDeviceSelectValue(micSelect, value) {
    const id = value ? String(value) : '';
    if (id && !Array.from(micSelect.options).some((o) => o.value === id)) {
        const opt = document.createElement('option');
        opt.value = id;
        opt.textContent = '…';
        micSelect.appendChild(opt);
    }
    micSelect.value = id;
}

// 系统默认麦克风变化且当前选用「系统默认」时，保存并重启服务以重新打开采集流
async function maybeRestartForNewSystemDefaultMic() {
    try {
        const statusRes = await fetch(`${API_BASE}/status`);
        const status = await statusRes.json();
        if (getServiceLifecycle(status) !== 'running') return;
        const ok = await saveConfig(true);
        if (!ok) return;
        await restartService();
    } catch (e) {
        console.warn('默认麦克风变更后重启服务失败:', e);
    }
}

// 刷新麦克风列表。选项的值是后端录音用的设备 ID（Windows 上是 WASAPI 端点 ID）
async function refreshMicDevices(preserveSelection = true) {
    const micSelect = document.getElementById('mic-device');
    if (!micSelect) return;

    const previousValue = preserveSelection ? micSelect.value : '';

    try {
        const response = await fetch(`${API_BASE}/audio/input-devices`);
        const data = await response.json();
        // 列不出来时保留现在的选项，不要把选好的麦克风清掉
        if (!response.ok || data.error) {
            console.warn('获取麦克风列表失败:', data.error || response.status);
            return;
        }
        const devices = Array.isArray(data.devices) ? data.devices : [];

        // 重建选项
        micSelect.innerHTML = '';
        const defaultOpt = document.createElement('option');
        defaultOpt.value = '';
        const t = window.i18n ? window.i18n.t : (key) => key;
        const defaultId = data.default_id || null;
        const defaultNameApi = data.default_name ? String(data.default_name).trim() : '';
        const displayDefaultName = resolveDefaultMicDisplayName(devices, defaultId, defaultNameApi);
        defaultOpt.textContent = displayDefaultName
            ? (window.i18n
                ? window.i18n.t('option.systemDefaultWithDevice', { name: displayDefaultName })
                : `系统默认（${displayDefaultName}）`)
            : (window.i18n ? window.i18n.t('option.systemDefault') : '系统默认');
        micSelect.appendChild(defaultOpt);

        const nameCounts = new Map();
        devices.forEach((d) => {
            const name = d.name ? String(d.name) : '';
            nameCounts.set(name, (nameCounts.get(name) || 0) + 1);
        });
        const ids = new Set();
        devices.forEach((d) => {
            if (!d.id) return;
            const id = String(d.id);
            ids.add(id);

            const opt = document.createElement('option');
            opt.value = id;
            const name = d.name ? String(d.name) : '';
            // 同名设备（比如两个一样的 USB 麦克风）后面加一小段 ID 区分
            const suffix = nameCounts.get(name) > 1 && d.label_suffix ? ` (#${d.label_suffix})` : '';
            opt.textContent = (name || t('label.device')) + suffix;
            micSelect.appendChild(opt);
        });

        // 优先保留当前选择；否则使用后端当前配置；最后回退默认
        const serverSelected = data.selected_id ? String(data.selected_id) : '';
        if (previousValue && ids.has(previousValue)) {
            micSelect.value = previousValue;
        } else if (serverSelected && ids.has(serverSelected)) {
            micSelect.value = serverSelected;
        } else {
            micSelect.value = '';
        }

        const identity = `${defaultId ?? 'none'}|${displayDefaultName}`;
        const prevIdentity = lastMicDefaultIdentity;
        lastMicDefaultIdentity = identity;
        // 后端录音会自己跟着系统默认麦克风换（follows_default）时不用重启服务
        if (prevIdentity !== null && identity !== prevIdentity && micSelect.value === '' && !data.follows_default) {
            void maybeRestartForNewSystemDefaultMic();
        }
    } catch (e) {
        // 静默失败：不影响其它功能
        console.warn('获取麦克风列表失败:', e);
    }
}

function setupMicDeviceAutoRefresh() {
    // 尽量监听浏览器设备变化事件（不保证每个环境都触发）
    try {
        if (navigator.mediaDevices) {
            if (typeof navigator.mediaDevices.addEventListener === 'function') {
                navigator.mediaDevices.addEventListener('devicechange', () => refreshMicDevices(true));
            } else if ('ondevicechange' in navigator.mediaDevices) {
                navigator.mediaDevices.ondevicechange = () => refreshMicDevices(true);
            }
        }
    } catch (_) {
        // ignore
    }

    // 后端设备列表变化更可靠：做一个低频轮询刷新
    setInterval(() => refreshMicDevices(true), 5000);
}

// 显示配置保存信息
function showConfigStorageInfo() {
    const lastSaved = localStorage.getItem(CONFIG_STORAGE_KEY + '_timestamp');

    if (lastSaved) {
        const date = new Date(parseInt(lastSaved));
        console.log(`配置已加载 (${date.toLocaleString('zh-CN')})`);
    }
}

/**
 * 1.1.2 以前的大模型「模板」：当前模板（一直是 custom1）的 Key 和额外参数在 llm_template_*_custom1 里另存了一份，
 * 打开页面时以它为准。读一次放回输入框（和以前打开时看到的一样），再删掉所有模板键；
 * 去掉的 DeepL / 豆包 / Soniox Key 也从浏览器里删掉。返回 true 表示迁移过，要把表单存回浏览器。
 */
function migrateLegacyBrowserKeys() {
    const legacyKeys = [];
    for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (!key) continue;
        if (key.startsWith(LEGACY_LLM_TEMPLATE_STORAGE_PREFIX)
            || key === LEGACY_LLM_SELECTED_TEMPLATE_STORAGE_KEY
            || LEGACY_REMOVED_KEY_STORAGE_KEYS.includes(key)) {
            legacyKeys.push(key);
        }
    }
    if (!legacyKeys.length) return false;

    const templateKey = (localStorage.getItem(`${LEGACY_LLM_TEMPLATE_STORAGE_PREFIX}key_custom1`) || '').trim();
    if (templateKey) {
        document.getElementById('llm-api-key').value = templateKey;
        persistSecretInputValue('llm-api-key');
    }
    const templateExtraBody = localStorage.getItem(`${LEGACY_LLM_TEMPLATE_STORAGE_PREFIX}extrabody_custom1`);
    if (templateExtraBody !== null) {
        document.getElementById('openai-compat-extra-body-json').value = templateExtraBody;
    }
    legacyKeys.forEach((key) => localStorage.removeItem(key));
    console.log(`已迁移并删除旧的模板 / Key 存储（${legacyKeys.length} 项）`);
    return true;
}

// 从localStorage加载API Keys
function loadAPIKeys() {
    const dashscopeKey = localStorage.getItem('dashscope_api_key');
    const llmKey = localStorage.getItem('llm_api_key');
    const useInternational = localStorage.getItem('use_international_endpoint') === 'true';

    if (dashscopeKey) document.getElementById('dashscope-api-key').value = dashscopeKey;
    if (llmKey) document.getElementById('llm-api-key').value = llmKey;

    const screenOcrKey = localStorage.getItem('screen_ocr_api_key');
    if (screenOcrKey) document.getElementById('screen-ocr-api-key').value = screenOcrKey;

    if (migrateLegacyBrowserKeys()) saveConfigToLocalStorage();

    document.getElementById('use-international-endpoint').checked = useInternational;

    // 应用国际版设置对 ASR 选项的影响
    updateAsrOptionsForInternational(useInternational);

    // 「API 配置」卡和里面各项折不折叠由 api-mode.js 的 onConfigReady 决定（填好的折叠，没填的展开）

    // 添加API Key change事件监听
    document.getElementById('dashscope-api-key').addEventListener('input', saveAPIKey);
    document.getElementById('llm-api-key').addEventListener('input', saveAPIKey);
    document.getElementById('screen-ocr-api-key').addEventListener('input', saveAPIKey);

    ['llm-base-url', 'llm-model', 'openai-compat-extra-body-json'].forEach((inputId) => {
        document.getElementById(inputId)?.addEventListener('input', (event) => onSettingChange(event.target));
    });
}

// 处理国际版端点开关变化
function handleInternationalEndpointChange(event) {
    const useInternational = event.target.checked;

    // 保存到 localStorage
    localStorage.setItem('use_international_endpoint', useInternational.toString());

    // 更新 ASR 选项
    updateAsrOptionsForInternational(useInternational);

    // 触发配置保存
    onSettingChange(event.target);
}

// 根据国际版设置更新 ASR 选项
function updateAsrOptionsForInternational(useInternational) {
    const asrBackendSelect = document.getElementById('asr-backend');
    const dashscopeOption = asrBackendSelect.querySelector('option[value="dashscope"]');
    const t = window.i18n ? window.i18n.t : (key) => key;

    if (useInternational) {
        // 国际版：禁用 Fun-ASR 选项
        if (dashscopeOption) {
            dashscopeOption.disabled = true;
            dashscopeOption.textContent = t('asr.dashscopeDisabled');
        }

        // 如果当前选中的是 dashscope，自动切换到 qwen_audio3
        if (asrBackendSelect.value === 'dashscope') {
            asrBackendSelect.value = 'qwen_audio3';
            onSettingChange();
        }
    } else {
        // 中国大陆版：启用 Fun-ASR 选项
        if (dashscopeOption) {
            dashscopeOption.disabled = false;
            dashscopeOption.textContent = t('asr.dashscope');
        }
    }
}

function applyAsrBackendLocks() {
    const asrBackendSelect = document.getElementById('asr-backend');
    const micControlToggle = document.getElementById('enable-mic-control');
    const partialResultsToggle = document.getElementById('show-partial-results');
    const enableTranslationToggle = document.getElementById('enable-translation');
    const translationApiSelect = document.getElementById('translation-api-type');
    const streamingModeToggle = document.getElementById('openrouter-streaming-mode');
    if (!asrBackendSelect || !micControlToggle || !partialResultsToggle || !enableTranslationToggle || !translationApiSelect || !streamingModeToggle) {
        return;
    }

    const isStreamingTranslation = enableTranslationToggle.checked
        && translationApiSelect.value === 'openrouter'
        && streamingModeToggle.checked;

    // 固定开启：VRChat 麦克风没开时不跑自我识别（后端也固定开启）
    micControlToggle.checked = true;
    micControlToggle.disabled = true;

    if (isStreamingTranslation) {
        partialResultsToggle.checked = false;
        partialResultsToggle.disabled = true;
    } else {
        partialResultsToggle.disabled = false;
    }
    // 1.1.2 整理：流式翻译开着时「显示部分结果」反正用不了，整行藏起来，不再显示一个灰掉的勾
    document.getElementById('fg-show-partial-results')?.classList.toggle('hidden', partialResultsToggle.disabled);
}

// 保存API Key到localStorage
function saveAPIKey(event) {
    persistSecretInputValue(event.target.id);

    if (event.target.id === 'llm-api-key') {
        runtimeCredentialStatus.llm.api_key_set = !!event.target.value.trim();
    }

    // 触发配置自动保存
    onSettingChange(event.target);
}

// 从 localStorage 加载配置
function loadConfigFromLocalStorage() {
    try {
        const savedConfig = localStorage.getItem(CONFIG_STORAGE_KEY);

        if (savedConfig) {
            const config = JSON.parse(savedConfig);

            // 填充表单
            if (config.translation) {
                document.getElementById('enable-translation').checked = config.translation.enable_translation ?? true;
                document.getElementById('target-language').value = config.translation.target_language || 'ja';
                document.getElementById('secondary-target-language').value = config.translation.secondary_target_language || '';
                document.getElementById('fallback-language').value = config.translation.fallback_language || '';
                document.getElementById('translation-api-type').value = 'openrouter';
                document.getElementById('openrouter-streaming-mode').checked =
                    isStreamingStoredApiType(config.translation.api_type);
                document.getElementById('openrouter-streaming-mode').disabled = false;
                document.getElementById('llm-base-url').value = config.translation.llm_base_url || '';
                document.getElementById('llm-model').value = config.translation.llm_model || '';
                document.getElementById('openai-compat-extra-body-json').value = config.translation.openai_compat_extra_body_json || '';
                document.getElementById('llm-translation-formality').value =
                    sanitizeLLMTranslationFormality(config.translation.llm_translation_formality);
                document.getElementById('llm-translation-style').value =
                    sanitizeLLMTranslationStyle(config.translation.llm_translation_style);
                setLLMParallelFastestModeSelect(
                    resolveLLMParallelFastestModeFromStoredTranslation(config.translation)
                );
                applySourceLanguageInputFromStored(config.translation.source_language ?? 'auto');
                document.getElementById('show-partial-results').checked = config.translation.show_partial_results ?? false;
                document.getElementById('enable-furigana').checked = config.translation.enable_furigana ?? false;
                document.getElementById('enable-pinyin').checked = config.translation.enable_pinyin ?? false;
                document.getElementById('enable-arabic-reshaper').checked = config.translation.enable_arabic_reshaper ?? true;
                document.getElementById('remove-trailing-period').checked = config.translation.remove_trailing_period ?? false;
                document.getElementById('text-fancy-style').value = config.translation.text_fancy_style || 'none';
                document.getElementById('enable-reverse-translation').checked = config.translation.enable_reverse_translation ?? false;

                const showTag = document.getElementById('show-original-and-lang-tag');
                if (showTag) {
                    showTag.checked = config.translation.show_original_and_lang_tag ?? true;
                }
            }

            if (config.osc) {
                const oscCompatEl = document.getElementById('osc-compat-mode');
                if (oscCompatEl) {
                    oscCompatEl.checked = config.osc.compat_mode ?? false;
                }
                const oscCompatPortEl = document.getElementById('osc-compat-listen-port');
                if (oscCompatPortEl) {
                    oscCompatPortEl.value = config.osc.compat_listen_port ?? 9001;
                }
                const bypassOscEl = document.getElementById('bypass-osc-udp-port-check');
                if (bypassOscEl) {
                    bypassOscEl.checked = config.osc.bypass_udp_port_check ?? false;
                }
                const oscPortEl = document.getElementById('osc-send-target-port');
                if (oscPortEl) {
                    oscPortEl.value = config.osc.send_target_port ?? 9000;
                }
                const oscSendErrorsEl = document.getElementById('osc-send-error-messages');
                if (oscSendErrorsEl) {
                    oscSendErrorsEl.checked = config.osc.send_error_messages ?? false;
                }
            }

            if (config.mic_control) {
                document.getElementById('enable-mic-control').checked = config.mic_control.enable_mic_control ?? false;
                document.getElementById('mute-delay').value = config.mic_control.mute_delay_seconds || 0.2;
                document.getElementById('enable-double-mute-clear').checked = config.mic_control.enable_double_mute_clear ?? true;

                const micSelect = document.getElementById('mic-device');
                if (micSelect) setMicDeviceSelectValue(micSelect, config.mic_control.mic_device_id);
            }

            applyVrchatMicSettingsToForm(config);

            if (config.asr) {
                document.getElementById('asr-backend').value = sanitizeAsrBackendValue(config.asr.preferred_backend || 'qwen_audio3');
                document.getElementById('enable-hot-words').checked = config.asr.enable_hot_words ?? true;
                const qwenAudio3Model = document.getElementById('qwen-audio3-model');
                if (qwenAudio3Model) {
                    qwenAudio3Model.value = config.asr.qwen_audio3_model || DEFAULT_QWEN_AUDIO3_MODEL;
                }

                // 加载国际版设置
                const useInternational = config.asr.use_international_endpoint ?? false;
                document.getElementById('use-international-endpoint').checked = useInternational;
                localStorage.setItem('use_international_endpoint', useInternational.toString());
                updateAsrOptionsForInternational(useInternational);
            }

            if (config.custom_api) {
                applyCustomApiConfigToForm({
                    CUSTOM_API_BASE_URL: config.custom_api.base_url,
                    CUSTOM_API_KEY: config.custom_api.key,
                    CUSTOM_API_MODEL: config.custom_api.model,
                    CUSTOM_API_TARGET_LANGUAGE: config.custom_api.target_language,
                    CUSTOM_API_TIMEOUT: config.custom_api.timeout,
                    CUSTOM_API_PROMPT: config.custom_api.prompt,
                    CUSTOM_API_AUDIO_FORMAT: config.custom_api.audio_format,
                });
            }

            applyVadConfig(resolveVadConfig(config));

            if (isLocalAsrUiEnabled()) {
                applyLocalAsrConfig(config.local_asr || {});
            }

            if (config.language_detector) {
                document.getElementById('language-detector').value = config.language_detector.type || 'cjke';
            }

            applyAutoLanguageDetectorIfNeeded();

            if (config.panel) {
                document.getElementById('panel-width').value = config.panel.width || 600;
                getNormalizedPanelWidth();
            }

            const bypassOscEl = document.getElementById('bypass-osc-udp-port-check');
            if (bypassOscEl && config.osc) {
                bypassOscEl.checked = config.osc.bypass_udp_port_check ?? false;
            }
            const oscCompatEl = document.getElementById('osc-compat-mode');
            if (oscCompatEl && config.osc) {
                oscCompatEl.checked = config.osc.compat_mode ?? false;
            }
            const oscCompatPortEl = document.getElementById('osc-compat-listen-port');
            if (oscCompatPortEl && config.osc) {
                const clp = config.osc.compat_listen_port;
                const p =
                    clp == null || clp === ''
                        ? 9001
                        : Math.max(1, Math.min(65535, parseInt(clp, 10) || 9001));
                oscCompatPortEl.value = String(p);
            }
            const oscPortEl = document.getElementById('osc-send-target-port');
            if (oscPortEl && config.osc) {
                const stp = config.osc.send_target_port;
                const p =
                    stp == null || stp === ''
                        ? 9000
                        : Math.max(1, Math.min(65535, parseInt(stp, 10) || 9000));
                oscPortEl.value = String(p);
            }

            if (config.smart_target_language) {
                const stl = config.smart_target_language;
                if (document.getElementById('smart-target-primary-enabled')) {
                    document.getElementById('smart-target-primary-enabled').checked = stl.primary_enabled ?? true;
                }
                if (document.getElementById('smart-target-secondary-enabled')) {
                    document.getElementById('smart-target-secondary-enabled').checked = stl.secondary_enabled ?? false;
                }
                if (document.getElementById('smart-target-strategy')) {
                    document.getElementById('smart-target-strategy').value = stl.strategy || 'auto';
                }
                if (document.getElementById('smart-target-window-size')) {
                    document.getElementById('smart-target-window-size').value = stl.window_size ?? 5;
                }
                if (document.getElementById('smart-target-exclude-self')) {
                    document.getElementById('smart-target-exclude-self').checked = stl.exclude_self_language ?? true;
                }
                if (document.getElementById('smart-target-min-samples')) {
                    document.getElementById('smart-target-min-samples').value = stl.min_samples ?? 3;
                }
                updateSmartTargetVisibility();
            }

            if (config.others_translation) {
                applyOthersTranslationForm(config.others_translation);
            }
            if (config.screen_translation) {
                applyScreenTranslationForm(config.screen_translation);
            }
            applyDisplayMode(config.display?.mode);

            console.log('✓ 已从浏览器加载配置');
        } else {
            // 如果没有保存的配置，使用前端默认值（由页面初始化处再拉取服务器并 / 或对账）
            loadDefaultConfig();
        }

        // 根据翻译开关显示/隐藏翻译选项
        updateOpenRouterStreamingUi();
        toggleTranslationOptions();
        updateFuriganaVisibility();
        updateLLMSettingsVisibility();
        applyAsrBackendLocks();
        updateOscCompatModeUi();
        syncAllLanguageComboClearButtons();
        updateDashscopeKeyFieldState();
        updateLocalAsrUiVisibility();
        if (window.GanlanApiMode) {
            let savedApi = null;
            try { savedApi = JSON.parse(localStorage.getItem(CONFIG_STORAGE_KEY) || '{}').api || null; } catch (e) { savedApi = null; }
            window.GanlanApiMode.restore(savedApi);
        }

    } catch (error) {
        console.error('加载本地配置失败:', error);
        // 出错时使用前端默认值
        loadDefaultConfig();
        toggleTranslationOptions();
        updateFuriganaVisibility();
        updateLLMSettingsVisibility();
        applyAsrBackendLocks();
        updateOscCompatModeUi();
        syncAllLanguageComboClearButtons();
        updateDashscopeKeyFieldState();
        updateLocalAsrUiVisibility();
    }
}

// 加载前端默认配置（不依赖服务器）
function loadDefaultConfig() {
    // 翻译配置
    document.getElementById('enable-translation').checked = true;
    document.getElementById('target-language').value = 'ja';
    document.getElementById('secondary-target-language').value = '';
    document.getElementById('fallback-language').value = 'en';
    document.getElementById('translation-api-type').value = 'openrouter';
    applySourceLanguageInputFromStored('auto');
    document.getElementById('show-partial-results').checked = false;
    document.getElementById('enable-furigana').checked = false;
    document.getElementById('enable-pinyin').checked = false;
    document.getElementById('enable-arabic-reshaper').checked = true;
    document.getElementById('remove-trailing-period').checked = false;
    document.getElementById('text-fancy-style').value = 'none';
    document.getElementById('enable-reverse-translation').checked = false;
    const streamingModeEl = document.getElementById('openrouter-streaming-mode');
    if (streamingModeEl) {
        streamingModeEl.checked = true;
        streamingModeEl.disabled = false;
    }
    setLLMParallelFastestModeSelect('off');
    document.getElementById('llm-base-url').value = '';
    document.getElementById('llm-model').value = '';
    document.getElementById('llm-translation-formality').value = DEFAULT_LLM_TRANSLATION_FORMALITY;
    document.getElementById('llm-translation-style').value = DEFAULT_LLM_TRANSLATION_STYLE;
    document.getElementById('openai-compat-extra-body-json').value = '';

    const showTag = document.getElementById('show-original-and-lang-tag');
    if (showTag) showTag.checked = true;

    // 麦克风控制
    document.getElementById('enable-mic-control').checked = false;
    document.getElementById('mute-delay').value = 0.2;
    document.getElementById('enable-double-mute-clear').checked = true;

    // 麦克风设备
    const micSelect = document.getElementById('mic-device');
    if (micSelect) micSelect.value = '';

    // ASR 配置
    document.getElementById('asr-backend').value = 'qwen_audio3';
    const othersAsrDefault = document.getElementById('others-asr-backend');
    if (othersAsrDefault) othersAsrDefault.value = '';
    const othersEnabledDefault = document.getElementById('others-translation-enabled');
    if (othersEnabledDefault) othersEnabledDefault.checked = false;
    const othersTargetDefault = document.getElementById('others-target-language');
    if (othersTargetDefault) othersTargetDefault.value = 'zh-CN';
    const qwenAudio3Default = document.getElementById('qwen-audio3-model');
    if (qwenAudio3Default) qwenAudio3Default.value = DEFAULT_QWEN_AUDIO3_MODEL;
    document.getElementById('enable-hot-words').checked = true;
    document.getElementById('use-international-endpoint').checked = false;

    applyVadConfig({
        enabled: true,
        mode: 'silero',
        threshold: 0.50,
        min_speech_duration: 1.0,
        max_speech_duration: 30.0,
        silence_duration: 0.8,
        pre_speech_duration: 0.2,
    });

    if (isLocalAsrUiEnabled()) {
        applyLocalAsrConfig({
            engine: 'sensevoice',
            incremental_asr: true,
            interim_interval: 2.0,
        });
    }

    // 语言检测器
    document.getElementById('language-detector').value = 'cjke';
    applyAutoLanguageDetectorIfNeeded();

    // 小面板
    document.getElementById('panel-width').value = 600;

    const bypassOscDefault = document.getElementById('bypass-osc-udp-port-check');
    if (bypassOscDefault) {
        bypassOscDefault.checked = false;
    }
    const oscCompatDefault = document.getElementById('osc-compat-mode');
    if (oscCompatDefault) {
        oscCompatDefault.checked = false;
    }
    const oscCompatPortDefault = document.getElementById('osc-compat-listen-port');
    if (oscCompatPortDefault) {
        oscCompatPortDefault.value = '9001';
    }
    const oscSendErrorsDefault = document.getElementById('osc-send-error-messages');
    if (oscSendErrorsDefault) {
        oscSendErrorsDefault.checked = false;
    }
    const oscPortDefault = document.getElementById('osc-send-target-port');
    if (oscPortDefault) {
        oscPortDefault.value = '9000';
    }

    console.log('✓ 已加载前端默认配置');
    updateOpenRouterStreamingUi();
    updateFuriganaVisibility();
    updateLLMSettingsVisibility();
    applyAsrBackendLocks();
    updateOscCompatModeUi();
    syncAllLanguageComboClearButtons();
    updateDashscopeKeyFieldState();
    updateLocalAsrUiVisibility();
}

function refreshUiAfterServerConfigApply() {
    updateOpenRouterStreamingUi();
    toggleTranslationOptions();
    updateFuriganaVisibility();
    updateLLMSettingsVisibility();
    applyAsrBackendLocks();
    updateOscCompatModeUi();
    syncAllLanguageComboClearButtons();
    updateDashscopeKeyFieldState();
    updateLocalAsrUiVisibility();
    renderLanguageComboMenus();
}

/** 将 GET /api/config 的 JSON 填回表单（不写入 localStorage、不改 touch） */
function applyServerConfigPayload(config) {
    if (config.features) {
        featureFlags = config.features;
    }
    // 百炼模式下服务器发回来的是百炼的地址，不能写进兼容模式的框（最后由 absorbServerConfig 放回去）
    const customFieldSnapshot = window.GanlanApiMode ? window.GanlanApiMode.snapshotCustomFields() : null;
    updateLocalAsrUiVisibility();

    document.getElementById('enable-translation').checked = config.translation.enable_translation;
    document.getElementById('target-language').value = config.translation.target_language;
    document.getElementById('secondary-target-language').value = config.translation.secondary_target_language || '';
    document.getElementById('fallback-language').value = config.translation.fallback_language || '';
    document.getElementById('translation-api-type').value = 'openrouter';
    document.getElementById('openrouter-streaming-mode').checked = isStreamingStoredApiType(config.translation.api_type);
    document.getElementById('openrouter-streaming-mode').disabled = false;
    document.getElementById('llm-base-url').value = config.translation.llm_base_url || '';
    document.getElementById('llm-model').value = config.translation.llm_model || '';
    document.getElementById('openai-compat-extra-body-json').value = config.translation.openai_compat_extra_body_json || '';
    document.getElementById('llm-translation-formality').value =
        sanitizeLLMTranslationFormality(config.translation.llm_translation_formality);
    document.getElementById('llm-translation-style').value =
        sanitizeLLMTranslationStyle(config.translation.llm_translation_style);
    setLLMParallelFastestModeSelect(
        resolveLLMParallelFastestModeFromStoredTranslation(config.translation),
    );
    document.getElementById('show-partial-results').checked = config.translation.show_partial_results ?? false;
    document.getElementById('enable-furigana').checked = config.translation.enable_furigana ?? false;
    document.getElementById('enable-pinyin').checked = config.translation.enable_pinyin ?? false;
    document.getElementById('enable-arabic-reshaper').checked = config.translation.enable_arabic_reshaper ?? true;
    document.getElementById('remove-trailing-period').checked = config.translation.remove_trailing_period ?? false;
    document.getElementById('text-fancy-style').value = config.translation.text_fancy_style || 'none';
    document.getElementById('enable-reverse-translation').checked = config.translation.enable_reverse_translation ?? false;
    const showTag = document.getElementById('show-original-and-lang-tag');
    if (showTag) {
        showTag.checked = config.translation.show_original_and_lang_tag ?? true;
    }

    document.getElementById('enable-mic-control').checked = config.mic_control.enable_mic_control;
    document.getElementById('mute-delay').value = config.mic_control.mute_delay_seconds;
    document.getElementById('enable-double-mute-clear').checked = config.mic_control.enable_double_mute_clear ?? true;
    const micSelect = document.getElementById('mic-device');
    if (micSelect && config.mic_control) {
        setMicDeviceSelectValue(micSelect, config.mic_control.mic_device_id);
    }

    applyVrchatMicSettingsToForm(config);

    document.getElementById('asr-backend').value = sanitizeAsrBackendValue(config.asr.preferred_backend);
    const qwenAudio3ModelEl = document.getElementById('qwen-audio3-model');
    if (qwenAudio3ModelEl) {
        qwenAudio3ModelEl.value = config.asr.qwen_audio3_model || DEFAULT_QWEN_AUDIO3_MODEL;
    }
    document.getElementById('enable-hot-words').checked = config.asr.enable_hot_words;
    applyVadConfig(resolveVadConfig(config));
    const useInternational = config.asr.use_international_endpoint ?? false;
    document.getElementById('use-international-endpoint').checked = useInternational;
    localStorage.setItem('use_international_endpoint', useInternational.toString());
    updateAsrOptionsForInternational(useInternational);

    if (config.custom_api) {
        applyCustomApiConfigToForm({
            CUSTOM_API_BASE_URL: config.custom_api.base_url,
            CUSTOM_API_KEY: config.custom_api.key,
            CUSTOM_API_MODEL: config.custom_api.model,
            CUSTOM_API_TARGET_LANGUAGE: config.custom_api.target_language,
            CUSTOM_API_TIMEOUT: config.custom_api.timeout,
            CUSTOM_API_PROMPT: config.custom_api.prompt,
            CUSTOM_API_AUDIO_FORMAT: config.custom_api.audio_format,
        });
    }

    document.getElementById('language-detector').value = config.language_detector.type;
    applySourceLanguageInputFromStored(config.translation.source_language);
    applyAutoLanguageDetectorIfNeeded();
    document.getElementById('panel-width').value = (config.panel && config.panel.width) || 600;
    getNormalizedPanelWidth();
    const bypassOscEl = document.getElementById('bypass-osc-udp-port-check');
    if (bypassOscEl && config.osc) {
        bypassOscEl.checked = config.osc.bypass_udp_port_check ?? false;
    }
    const oscCompatEl = document.getElementById('osc-compat-mode');
    if (oscCompatEl && config.osc) {
        oscCompatEl.checked = config.osc.compat_mode ?? false;
    }
    const oscCompatPortEl = document.getElementById('osc-compat-listen-port');
    if (oscCompatPortEl && config.osc) {
        const clp = config.osc.compat_listen_port;
        const p =
            clp == null || clp === ''
                ? 9001
                : Math.max(1, Math.min(65535, parseInt(clp, 10) || 9001));
        oscCompatPortEl.value = String(p);
    }
    const oscSendErrorsEl = document.getElementById('osc-send-error-messages');
    if (oscSendErrorsEl && config.osc) {
        oscSendErrorsEl.checked = config.osc.send_error_messages ?? false;
    }
    const oscPortApply = document.getElementById('osc-send-target-port');
    if (oscPortApply && config.osc) {
        const stp = config.osc.send_target_port;
        const p =
            stp == null || stp === ''
                ? 9000
                : Math.max(1, Math.min(65535, parseInt(stp, 10) || 9000));
        oscPortApply.value = String(p);
    }
    if (isLocalAsrUiEnabled()) {
        applyLocalAsrConfig(config.local_asr || {});
    }

    if (config.smart_target_language) {
        const stl = config.smart_target_language;
        if (document.getElementById('smart-target-primary-enabled')) {
            document.getElementById('smart-target-primary-enabled').checked = stl.primary_enabled ?? true;
        }
        if (document.getElementById('smart-target-secondary-enabled')) {
            document.getElementById('smart-target-secondary-enabled').checked = stl.secondary_enabled ?? false;
        }
        if (document.getElementById('smart-target-strategy')) {
            document.getElementById('smart-target-strategy').value = stl.strategy || 'auto';
        }
        if (document.getElementById('smart-target-window-size')) {
            document.getElementById('smart-target-window-size').value = stl.window_size ?? 5;
        }
        if (document.getElementById('smart-target-exclude-self')) {
            document.getElementById('smart-target-exclude-self').checked = stl.exclude_self_language ?? true;
        }
        if (document.getElementById('smart-target-min-samples')) {
            document.getElementById('smart-target-min-samples').value = stl.min_samples ?? 3;
        }
        updateSmartTargetVisibility();
    }

    if (config.others_translation) {
        applyOthersTranslationForm(config.others_translation);
    }
    if (config.screen_translation) {
        applyScreenTranslationForm(config.screen_translation);
    }
    if (window.GanlanApiMode) window.GanlanApiMode.absorbServerConfig(config, customFieldSnapshot);
    applyDisplayMode(config.display?.mode);
    refreshPanelSettingMirrors();
}

/**
 * 打开大面板时与后端对账。
 *
 * 1. 后端 boot_ms ≠ 本地记录 → 后端重启过 → 把大面板本地配置推到后端（用户上次存的就是最终态）
 * 2. 同一运行期 → 比较 config_applied_at_ms 与 touch_main_ms，谁大听谁
 */
async function reconcileMainConfigWithServerAtStartup() {
    try {
        const response = await fetch(`${API_BASE}/config`);
        if (!response.ok) return;
        const serverCfg = await response.json();

        const serverBootMs = Number(serverCfg.backend_boot_ms) || 0;
        const lastSeenBootMs = getLastSeenBootMs();
        const serverAppliedMs = Number(serverCfg.config_applied_at_ms) || 0;
        const clientMs = getMainConfigTouchMs();

        if (serverBootMs !== lastSeenBootMs) {
            // 后端（重新）启动过 → 大面板本地配置推到后端
            setLastSeenBootMs(serverBootMs);
            const ok = await saveConfig(true);
            console.log(ok
                ? '对账：后端新启动，已将本地配置推送到服务器'
                : '对账：后端新启动，本地配置推送失败');
            return;
        }

        // 同一运行期：谁的时间戳更大，听谁的
        if (serverAppliedMs > clientMs) {
            applyServerConfigPayload(serverCfg);
            saveConfigToLocalStorage();
            setMainConfigTouchMs(serverAppliedMs);
            refreshUiAfterServerConfigApply();
            console.log('对账：服务器配置较新，已拉取到本地');
        } else if (clientMs > serverAppliedMs) {
            await saveConfig(true);
            console.log('对账：本地配置较新，已推送到服务器');
        }
        // 相等 → 已同步，无需操作
    } catch (e) {
        console.warn('对账失败:', e);
    }
}

/**
 * 首次打开（localStorage 无配置）时从服务器拉取全量配置。
 */
async function loadConfigFromServer() {
    try {
        const response = await fetch(`${API_BASE}/config`);
        const cfg = await response.json();
        applyServerConfigPayload(cfg);
        saveConfigToLocalStorage();

        const appliedMs = Number(cfg.config_applied_at_ms) || 0;
        if (appliedMs > 0) setMainConfigTouchMs(appliedMs);
        const bootMs = Number(cfg.backend_boot_ms) || 0;
        if (bootMs > 0) setLastSeenBootMs(bootMs);

        refreshUiAfterServerConfigApply();
        console.log('已从服务器加载配置（首次打开）');
    } catch (error) {
        console.error('加载服务器配置失败:', error);
    }
}

// 保存配置到 localStorage
function saveConfigToLocalStorage() {
    try {
        applyAsrBackendLocks();

        // 实际的 API 类型：大模型 + 流式开关 -> openrouter_streaming
        let actualApiType = document.getElementById('translation-api-type').value;
        if (actualApiType === 'openrouter' && document.getElementById('openrouter-streaming-mode').checked) {
            actualApiType = 'openrouter_streaming';
        }

        const llmBaseUrl = document.getElementById('llm-base-url').value.trim();
        const llmModel = document.getElementById('llm-model').value.trim();
        const openaiExtraBodyJson = document.getElementById('openai-compat-extra-body-json').value.trim();

        const config = {
            translation: {
                enable_translation: document.getElementById('enable-translation').checked,
                target_language: document.getElementById('target-language').value,
                secondary_target_language: document.getElementById('secondary-target-language').value || null,
                fallback_language: document.getElementById('fallback-language').value || null,
                api_type: actualApiType,
                llm_base_url: llmBaseUrl,
                llm_model: llmModel,
                llm_translation_formality: sanitizeLLMTranslationFormality(
                    document.getElementById('llm-translation-formality').value
                ),
                llm_translation_style: sanitizeLLMTranslationStyle(
                    document.getElementById('llm-translation-style').value
                ),
                openai_compat_extra_body_json: openaiExtraBodyJson,
                llm_parallel_fastest_mode: getLLMParallelFastestModeSelect(),
                source_language: getSourceLanguageEffective(),
                show_partial_results: document.getElementById('show-partial-results').checked,
                enable_furigana: document.getElementById('enable-furigana').checked,
                enable_pinyin: document.getElementById('enable-pinyin').checked,
                enable_arabic_reshaper: document.getElementById('enable-arabic-reshaper').checked,
                remove_trailing_period: document.getElementById('remove-trailing-period').checked,
                text_fancy_style: document.getElementById('text-fancy-style').value || 'none',
                enable_reverse_translation: document.getElementById('enable-reverse-translation').checked,
                show_original_and_lang_tag: document.getElementById('show-original-and-lang-tag')
                    ? document.getElementById('show-original-and-lang-tag').checked
                    : true,
            },
            mic_control: {
                enable_mic_control: document.getElementById('enable-mic-control').checked,
                mute_delay_seconds: parseFloat(document.getElementById('mute-delay').value),
                enable_double_mute_clear: document.getElementById('enable-double-mute-clear').checked,
                mic_device_id: document.getElementById('mic-device')?.value || null,
            },
            vrchat_mic: getVrchatMicSettingsFromForm(),
            asr: {
                preferred_backend: sanitizeAsrBackendValue(document.getElementById('asr-backend').value),
                qwen_audio3_model: (document.getElementById('qwen-audio3-model')?.value || '').trim() || DEFAULT_QWEN_AUDIO3_MODEL,
                enable_hot_words: document.getElementById('enable-hot-words').checked,
                use_international_endpoint: document.getElementById('use-international-endpoint').checked,
            },
            custom_api: {
                base_url: document.getElementById('custom-api-base-url')?.value.trim() || '',
                key: document.getElementById('custom-api-key')?.value.trim() || '',
                model: document.getElementById('custom-api-model')?.value.trim() || '',
                target_language: document.getElementById('custom-api-target-language')?.value.trim() || 'ja',
                timeout: parseInt(document.getElementById('custom-api-timeout')?.value || '30', 10) || 30,
                prompt: document.getElementById('custom-api-prompt')?.value || '',
                audio_format: document.getElementById('custom-api-audio-format')?.value || 'wav',
            },
            vad: getVadConfigFromForm(),
            language_detector: {
                type: document.getElementById('language-detector').value,
            },
            panel: {
                width: getNormalizedPanelWidth(),
            },
            osc: {
                send_target_port: getOscSendTargetPortFromForm(),
                compat_mode: isOscCompatModeEnabled(),
                compat_listen_port: getOscCompatListenPortFromForm(),
                bypass_udp_port_check:
                    document.getElementById('bypass-osc-udp-port-check')?.checked === true,
                send_error_messages:
                    document.getElementById('osc-send-error-messages')?.checked === true,
            },
            smart_target_language: {
                primary_enabled: document.getElementById('smart-target-primary-enabled')?.checked ?? true,
                secondary_enabled: document.getElementById('smart-target-secondary-enabled')?.checked ?? false,
                strategy: document.getElementById('smart-target-strategy')?.value || 'auto',
                window_size: parseInt(document.getElementById('smart-target-window-size')?.value || '5'),
                exclude_self_language: document.getElementById('smart-target-exclude-self')?.checked ?? true,
                min_samples: parseInt(document.getElementById('smart-target-min-samples')?.value || '3'),
            },
            others_translation: {
                asr_backend: '',   // 1.1.2：跟随「API 配置」，不再单独选
                target_language: (document.getElementById('others-target-language')?.value || '').trim() || 'zh-CN',
            },
            screen_translation: getScreenTranslationConfigFromForm(),
            display: { mode: currentDisplayMode },
            local_asr: isLocalAsrUiEnabled() ? getLocalAsrConfigFromForm() : null,
            api: window.GanlanApiMode ? window.GanlanApiMode.serialize() : undefined,
        };

        localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(config));
        localStorage.setItem(CONFIG_STORAGE_KEY + '_timestamp', Date.now().toString());

    } catch (error) {
        console.error('保存配置失败:', error);
    }
}

// 加载配置（兼容旧代码）
async function loadConfig() {
    loadConfigFromLocalStorage();
}

// 切换翻译选项的显示/隐藏
function toggleTranslationOptions() {
    const enableTranslation = document.getElementById('enable-translation').checked;
    const translationOptions = document.getElementById('translation-options');

    if (enableTranslation) {
        translationOptions.classList.remove('hidden');
    } else {
        translationOptions.classList.add('hidden');
    }
}

// 假名和拼音选项始终显示，无需根据语言切换
function updateFuriganaVisibility() {
    // 假名和拼音选项始终可见，不再根据目标语言隐藏
}

// 处理翻译API变更
let previousTranslationApi = null;

/** 一键：启用翻译、LLM API、流式翻译模式，并走与下拉框变更相同的更新与保存逻辑。 */
function switchToLLMStreamingTranslation() {
    const enableTrans = document.getElementById('enable-translation');
    if (enableTrans && !enableTrans.checked) {
        enableTrans.checked = true;
        toggleTranslationOptions();
    }

    const select = document.getElementById('translation-api-type');
    const streaming = document.getElementById('openrouter-streaming-mode');
    if (!select || !streaming) return;

    select.value = 'openrouter';
    streaming.checked = true;
    select.dispatchEvent(new Event('change', { bubbles: true }));
}

function handleTranslationApiChange(event) {
    const newApi = event.target.value;
    const warningElement = document.getElementById('translation-api-warning');

    updateOpenRouterStreamingUi();
    const expandLlmPanel = newApi === 'openrouter' && !isLLMConnectionFieldsComplete();
    updateLLMSettingsVisibility(newApi, expandLlmPanel);
    updateLLMStreamingPromoState();
    applyAsrBackendLocks();

    // 清除警告消息
    warningElement.style.display = 'none';

    // 记录当前选择作为下次的"之前选项"
    previousTranslationApi = newApi;

    // 触发配置保存
    onSettingChange();
}

// 初始化时记录当前的翻译API
document.addEventListener('DOMContentLoaded', function () {
    setTimeout(() => {
        const apiSelect = document.getElementById('translation-api-type');
        previousTranslationApi = apiSelect.value;

        updateOpenRouterStreamingUi();
        updateLLMSettingsVisibility(apiSelect.value);
        updateLLMStreamingPromoState();
    }, 100);
});

// 当设置改变时自动保存（延迟保存，避免频繁请求）
function onSettingChange(changedElement = null) {
    touchMainPanelUserEditedAt();
    applyAsrBackendLocks();
    applyAutoLanguageDetectorIfNeeded();
    updateDashscopeKeyFieldState();
    updateLLMStreamingPromoState();

    // 清除之前的定时器
    if (autoSaveTimer) {
        clearTimeout(autoSaveTimer);
    }

    // 200ms后自动保存（更快响应，静默保存）
    autoSaveTimer = setTimeout(async () => {
        // 先保存到本地浏览器
        saveConfigToLocalStorage();

        // 再保存到服务器
        const saveSucceeded = await saveConfig(true); // true表示是自动保存，不重启服务
        if (!saveSucceeded) {
            return;
        }

        // 如果服务正在运行，仅在必要时（如 ASR 后端等）重启；否则只保存并通知后端热加载配置
        const statusResponse = await fetch(`${API_BASE}/status`);
        const status = await statusResponse.json();
        if (status.running) {
            try {
                // 优先使用传入的变更元素；否则退回到当前活动元素
                const el = changedElement || document.activeElement;

                // 检查元素是否有 data-restart-required 属性
                const needRestart = el && el.getAttribute('data-restart-required') === 'true';

                if (needRestart) {
                    // 仅在确实修改了需要重启的项时重启服务
                    await restartService();
                } else {
                    // 不需要重启：后端（同进程）会收到配置变更并在下一次翻译时热加载
                    console.log('配置已保存；无需重启服务，已通知后端热加载。');
                }
            } catch (e) {
                console.error('处理自动重启逻辑失败:', e);
            }
        }
    }, 200);
}

// 保存配置
async function saveConfig(autoSave = false) {
    const t = window.i18n ? window.i18n.t : (key) => key;

    try {
        applyAsrBackendLocks();

        // 实际的 API 类型：大模型 + 流式开关 -> openrouter_streaming
        let actualApiType = document.getElementById('translation-api-type').value;
        if (actualApiType === 'openrouter' && document.getElementById('openrouter-streaming-mode').checked) {
            actualApiType = 'openrouter_streaming';
        }

        // 1.1.2：发给后端的是「API 配置」当前模式实际用的地址、模型（百炼模式 = 百炼的地址）
        const apiPlan = window.GanlanApiMode ? window.GanlanApiMode.effective() : null;
        const llmBaseUrl = apiPlan ? apiPlan.llm.baseUrl : document.getElementById('llm-base-url').value.trim();
        const llmModel = apiPlan ? apiPlan.llm.model : document.getElementById('llm-model').value.trim();
        const openaiExtraBodyJson = apiPlan
            ? apiPlan.llm.extraBody
            : document.getElementById('openai-compat-extra-body-json').value.trim();

        const config = {
            translation: {
                enable_translation: document.getElementById('enable-translation').checked,
                target_language: document.getElementById('target-language').value,
                secondary_target_language: document.getElementById('secondary-target-language').value || null,
                fallback_language: document.getElementById('fallback-language').value || null,
                api_type: actualApiType,
                llm_base_url: llmBaseUrl,
                llm_model: llmModel,
                llm_translation_formality: sanitizeLLMTranslationFormality(
                    document.getElementById('llm-translation-formality').value
                ),
                llm_translation_style: sanitizeLLMTranslationStyle(
                    document.getElementById('llm-translation-style').value
                ),
                openai_compat_extra_body_json: openaiExtraBodyJson,
                llm_parallel_fastest_mode: getLLMParallelFastestModeSelect(),
                source_language: getSourceLanguageEffective(),
                show_partial_results: document.getElementById('show-partial-results').checked,
                enable_furigana: document.getElementById('enable-furigana').checked,
                enable_pinyin: document.getElementById('enable-pinyin').checked,
                enable_arabic_reshaper: document.getElementById('enable-arabic-reshaper').checked,
                remove_trailing_period: document.getElementById('remove-trailing-period').checked,
                text_fancy_style: document.getElementById('text-fancy-style').value || 'none',
                enable_reverse_translation: document.getElementById('enable-reverse-translation').checked,
                show_original_and_lang_tag: document.getElementById('show-original-and-lang-tag')
                    ? document.getElementById('show-original-and-lang-tag').checked
                    : true,
            },
            mic_control: {
                enable_mic_control: document.getElementById('enable-mic-control').checked,
                mute_delay_seconds: parseFloat(document.getElementById('mute-delay').value),
                enable_double_mute_clear: document.getElementById('enable-double-mute-clear').checked,
                mic_device_id: document.getElementById('mic-device')?.value || null,
            },
            vrchat_mic: getVrchatMicSettingsFromForm(),
            asr: {
                // 兼容模式里填百炼地址 + 实时流式模型时，实际走百炼实时识别（api-mode.js 的 compatRealtimeAsr）
                preferred_backend: apiPlan && apiPlan.asrRealtime
                    ? 'qwen_audio3'
                    : sanitizeAsrBackendValue(document.getElementById('asr-backend').value),
                qwen_audio3_model: apiPlan
                    ? apiPlan.asrModel
                    : ((document.getElementById('qwen-audio3-model')?.value || '').trim() || DEFAULT_QWEN_AUDIO3_MODEL),
                enable_hot_words: document.getElementById('enable-hot-words').checked,
                use_international_endpoint: apiPlan
                    ? apiPlan.international
                    : document.getElementById('use-international-endpoint').checked,
            },
            custom_api: {
                base_url: document.getElementById('custom-api-base-url')?.value.trim() || '',
                key: document.getElementById('custom-api-key')?.value.trim() || '',
                model: document.getElementById('custom-api-model')?.value.trim() || '',
                target_language: document.getElementById('custom-api-target-language')?.value.trim() || 'ja',
                timeout: parseInt(document.getElementById('custom-api-timeout')?.value || '30', 10) || 30,
                prompt: document.getElementById('custom-api-prompt')?.value || '',
                audio_format: document.getElementById('custom-api-audio-format')?.value || 'wav',
            },
            vad: getVadConfigFromForm(),
            language_detector: {
                type: document.getElementById('language-detector').value,
            },
            smart_target_language: {
                primary_enabled: document.getElementById('smart-target-primary-enabled')?.checked ?? true,
                secondary_enabled: document.getElementById('smart-target-secondary-enabled')?.checked ?? false,
                strategy: document.getElementById('smart-target-strategy')?.value || 'auto',
                window_size: parseInt(document.getElementById('smart-target-window-size')?.value || '5'),
                exclude_self_language: document.getElementById('smart-target-exclude-self')?.checked ?? true,
                min_samples: parseInt(document.getElementById('smart-target-min-samples')?.value || '3'),
            },
            others_translation: {
                asr_backend: '',   // 1.1.2：跟随「API 配置」，不再单独选
                target_language: (document.getElementById('others-target-language')?.value || '').trim() || 'zh-CN',
            },
            screen_translation: getScreenTranslationConfigFromForm(true),
            display: { mode: currentDisplayMode },
            panel: {
                width: getNormalizedPanelWidth(),
            },
            osc: {
                send_target_port: getOscSendTargetPortFromForm(),
                compat_mode: isOscCompatModeEnabled(),
                compat_listen_port: getOscCompatListenPortFromForm(),
                bypass_udp_port_check:
                    document.getElementById('bypass-osc-udp-port-check')?.checked === true,
                send_error_messages:
                    document.getElementById('osc-send-error-messages')?.checked === true,
            },
            local_asr: isLocalAsrUiEnabled() ? getLocalAsrConfigFromForm() : null,
            api_keys: {
                dashscope: getEffectiveDashscopeKey(),
                llm: getEffectiveLLMApiKeyForCurrentMode(),
                screen_ocr: getScreenOcrApiKey(),
            }
        };

        const response = await fetch(`${API_BASE}/config`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(config),
        });

        const result = await response.json();

        if (result.success) {
            if (result.config_applied_at_ms != null && result.config_applied_at_ms !== undefined) {
                setMainConfigTouchMs(result.config_applied_at_ms);
            }
            if (!autoSave) {
                showMessage(t('msg.configSaved'), 'success');
            }
            return true;
        } else {
            const localizedMsg = localizeBackendMessage(result.message_id, result.message);
            showMessage(t('msg.saveConfigFailed') + ': ' + localizedMsg, 'error');
            return false;
        }
    } catch (error) {
        console.error('保存配置失败:', error);
        if (!autoSave) {
            showMessage(t('msg.saveConfigFailed'), 'error');
        }
        return false;
    }
}

// 更新服务状态
function getServiceLifecycle(status) {
    return status?.lifecycle || (status?.running ? 'running' : 'stopped');
}

function setElementI18nText(element, key, t) {
    if (!element) return;
    element.setAttribute('data-i18n', key);
    element.textContent = t(key);
}

function renderServiceLifecycle(status, t) {
    const lifecycle = getServiceLifecycle(status);
    const statusText = document.getElementById('status-text');
    const statusDot = document.getElementById('status-dot');
    const startBtn = document.getElementById('start-btn');
    const stopBtn = document.getElementById('stop-btn');
    const modes = status?.modes || {};

    document.body.dataset.serviceLifecycle = lifecycle;
    statusDot.classList.remove('running', 'starting', 'stopping');
    renderModeRows(modes);

    const runningModes = SERVICE_MODES.filter((m) => modeStateKind(modes[m]) === 'running');
    if (lifecycle === 'running') {
        statusText.textContent = runningModes.length
            ? t('status.modesRunning', { modes: runningModes.map((m) => t(`mode.${m}`)).join(' · ') })
            : t('status.running');
        statusDot.classList.add('running');
    } else if (lifecycle === 'starting') {
        statusText.textContent = t('status.starting');
        statusDot.classList.add('starting');
    } else if (lifecycle === 'stopping') {
        statusText.textContent = t('status.stopping');
        statusDot.classList.add('stopping');
    } else {
        statusText.textContent = t('status.notRunning');
    }

    const anyBusy = SERVICE_MODES.some((m) => modeBusy[m]);
    startBtn.disabled = lifecycle === 'stopping' || anyBusy || runningModes.length === SERVICE_MODES.length;
    setElementI18nText(startBtn, 'btn.startAllModes', t);
    stopBtn.disabled = lifecycle === 'stopped' || lifecycle === 'stopping';
    setElementI18nText(stopBtn, lifecycle === 'stopping' ? 'btn.stopping' : 'btn.stopAllModes', t);
}

async function updateStatus() {
    try {
        const response = await fetch(`${API_BASE}/status`);
        const status = await response.json();

        const t = window.i18n ? window.i18n.t : (key) => key;
        renderServiceLifecycle(status, t);
        // VR 附属程序那边也能切桌面/VR，跟着后端走
        if (status.display_mode && status.display_mode !== currentDisplayMode && !displayModeBusy) {
            applyDisplayMode(status.display_mode);
            saveConfigToLocalStorage();
        }
        renderVrOverlayStatus(status.vr_overlay, t);

        // 后端时间戳比大面板记录更新 → 有人（小面板等）在别处改了配置 → 拉到大面板
        const serverTs = Number(status.config_applied_at_ms) || 0;
        const mainTs = getMainConfigTouchMs();
        if (serverTs > mainTs && status.target_language) {
            // 主语言、第二语言、他人 / 屏幕的译文语言（悬浮窗快捷按钮、语音控制插件会改）
            const synced = [
                ['target-language', status.target_language],
                ['secondary-target-language', status.secondary_target_language],
                ['others-target-language', status.others_target_language],
                ['screen-target-language', status.screen_target_language],
            ];
            let changed = false;
            synced.forEach(([id, value]) => {
                const input = document.getElementById(id);
                if (!input || typeof value !== 'string') return;
                if (id !== 'secondary-target-language' && !value) return;
                if (input.value !== value) {
                    input.value = value;
                    changed = true;
                }
            });
            if (changed) {
                renderLanguageComboMenus();
                syncAllLanguageComboClearButtons();
                saveConfigToLocalStorage();
            }
            setMainConfigTouchMs(serverTs);
        }
        return true;
    } catch (error) {
        console.error('更新状态失败:', error);
        return false;
    }
}

/** 根据 /api/udp-port-check 或带 udp_port_conflicts 的响应构建 UDP 端口冲突提示（无冲突则返回空字符串）。 */
function buildUdpPortConflictWarning(payload, t) {
    if (!payload || !Array.isArray(payload.udp_port_conflicts) || !payload.udp_port_conflicts.length) {
        return '';
    }
    const oscPort = payload.osc_udp_port ?? 9000;
    const programs = payload.udp_port_conflicts.map((p) => `${p.name} (PID ${p.pid})`).join(', ');
    return t('msg.udpPortConflictWarning', { port: oscPort, programs });
}

function buildVrchatOscNotListeningWarning(payload, t) {
    if (!payload || payload.vrchat_osc_listening === true) {
        return '';
    }
    if (!payload.vrchat_osc_warning_message_id && !payload.vrchat_osc_warning_message) {
        return '';
    }
    const oscPort = payload.osc_udp_port ?? 9000;
    const localized = t('msg.vrchatOscNotListeningWarning', { port: oscPort });
    if (localized && localized !== 'msg.vrchatOscNotListeningWarning') {
        return localized;
    }
    return payload.vrchat_osc_warning_message || '';
}

async function fetchOscUdpPortCheck() {
    const res = await fetch(`${API_BASE}/udp-port-check`);
    if (!res.ok) {
        throw new Error(`udp-port-check HTTP ${res.status}`);
    }
    return res.json();
}

// 启动模式（self / others / screen，可一次启动多个）
async function startService(modes = ['self']) {
    modes = (Array.isArray(modes) ? modes : [modes]).filter((m) => SERVICE_MODES.includes(m));
    if (!modes.length) return;
    const needsSelf = modes.includes('self');
    const needsAsr = needsSelf || modes.includes('others');
    const startBtn = document.getElementById('start-btn');
    modes.forEach((m) => setModeBusy(m, true));
    const t = window.i18n ? window.i18n.t : (key) => key;

    applyAsrBackendLocks();

    startBtn.disabled = true;
    startBtn.textContent = t('btn.starting');

    const bypassOscUdp = shouldSkipOscUdpPortCheck();
    if (needsSelf && !bypassOscUdp) {
        try {
            const udpPayload = await fetchOscUdpPortCheck();
            const conflictWarning = buildUdpPortConflictWarning(udpPayload, t);
            const vrchatOscWarning = buildVrchatOscNotListeningWarning(udpPayload, t);
            if (conflictWarning) {
                showMessage('⚠️ ' + conflictWarning, 'warning');
            }
            if (vrchatOscWarning) {
                showMessage('⚠️ ' + vrchatOscWarning, 'warning');
            }
        } catch (e) {
            console.warn('OSC UDP port check failed:', e);
        }
    }

    try {
        const asrBackend = document.getElementById('asr-backend').value;
        const dashscopeKey = getEffectiveDashscopeKey();
        const llmKey = getEffectiveLLMApiKeyForCurrentMode();
        const apiPlan = window.GanlanApiMode ? window.GanlanApiMode.effective() : null;
        const llmBaseUrl = apiPlan ? apiPlan.llm.baseUrl : document.getElementById('llm-base-url').value.trim();
        const llmModel = apiPlan ? apiPlan.llm.model : document.getElementById('llm-model').value.trim();
        const hasLLMKey = runtimeCredentialStatus.llm.api_key_set || !!llmKey;

        const enableTranslation = document.getElementById('enable-translation').checked;
        const translationApiSelect = document.getElementById('translation-api-type');
        let translationApiType = translationApiSelect.value;

        const requiresDashscopeKey = needsAsr && currentConfigRequiresDashscopeKey();

        // 1.1.2「API 配置」：按当前模式（百炼 / OpenAI 兼容）检查要用到的那几项填了没有
        if (window.GanlanApiMode && asrBackend !== 'local') {
            const translationNeeded = (needsSelf && enableTranslation) || modes.includes('others') || modes.includes('screen');
            const missing = window.GanlanApiMode.missingFor(modes, translationNeeded);
            if (missing) {
                showMessage('❌ ' + missing.message, 'error');
                startBtn.disabled = false;
                startBtn.textContent = t('btn.startAllModes');
                window.GanlanApiMode.reveal([missing.id]);
                return;
            }
        }

        if (needsAsr && asrBackend === 'local') {
            if (!isLocalAsrUiEnabled()) {
                showMessage('❌ ' + t('msg.localAsrDisabledInBuild'), 'error');
                startBtn.disabled = false;
                startBtn.textContent = t('btn.startAllModes');
                return;
            }
            const localResponse = await fetch(`${API_BASE}/local-asr/status`);
            const localPayload = await localResponse.json();
            const localEngine = document.getElementById('local-asr-engine')?.value || 'sensevoice';
            const engineStatus = localPayload?.engines?.[localEngine];
            if (!engineStatus || !engineStatus.ready) {
                if (!engineStatus) {
                    showMessage('❌ ' + t('msg.localAsrNotReady'), 'error');
                } else if (engineStatus.model_cached && Array.isArray(engineStatus.runtime_issues) && engineStatus.runtime_issues.length) {
                    showMessage(
                        '❌ ' + t('msg.localAsrNeedPythonDeps', { deps: engineStatus.runtime_issues.join(', ') }),
                        'error',
                    );
                } else if (engineStatus.model_cached === false) {
                    showMessage('❌ ' + t('msg.localAsrNotReady'), 'error');
                } else {
                    showMessage('❌ ' + t('msg.localAsrNeedSilero'), 'error');
                }
                ensureCollapsibleExpanded('local-asr-settings');
                startBtn.disabled = false;
                startBtn.textContent = t('btn.startAllModes');
                return;
            }
        }

        if (requiresDashscopeKey && !dashscopeKey) {
            showMessage('❌ ' + t('msg.dashscopeRequired'), 'error');
            startBtn.disabled = false;
            startBtn.textContent = t('btn.startAllModes');

            // 展开 API 配置，高亮百炼 Key 输入框
            if (window.GanlanApiMode) {
                window.GanlanApiMode.reveal(['dashscope-api-key']);
            } else {
                const apiKeysSection = document.getElementById('api-keys');
                if (apiKeysSection.classList.contains('collapsed')) {
                    toggleCollapsible('api-keys');
                }
                highlightInput('dashscope-api-key');
            }

            return;
        }

        if (needsAsr && (asrBackend === 'custom_stt' || asrBackend === 'custom_audio')) {
            const customBase = document.getElementById('custom-api-base-url')?.value.trim() || '';
            const customKey = document.getElementById('custom-api-key')?.value.trim() || '';
            const customModel = document.getElementById('custom-api-model')?.value.trim() || '';
            const customMissing = [];
            if (!customBase) customMissing.push('custom-api-base-url');
            if (!customKey) customMissing.push('custom-api-key');
            if (!customModel) customMissing.push('custom-api-model');
            if (customMissing.length > 0) {
                showMessage('❌ ' + t('msg.customAsrRequired'), 'error');
                startBtn.disabled = false;
                startBtn.textContent = t('btn.startAllModes');
                expandLLMSettingsPanel([customMissing[0]]);
                return;
            }
        }

        if (requiresDashscopeKey) {
            const checkDashscopeResponse = await fetch(`${API_BASE}/check-api-key`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ api_key: dashscopeKey }),
            });

            const checkDashscopeResult = await checkDashscopeResponse.json();

            if (!checkDashscopeResult.valid) {
                const localizedMsg = localizeBackendMessage(checkDashscopeResult.message_id, checkDashscopeResult.message);
                showMessage('❌ ' + t('msg.dashscopeValidationFailed') + localizedMsg, 'error');
                startBtn.disabled = false;
                startBtn.textContent = t('btn.startAllModes');
                expandLLMSettingsPanel(['dashscope-api-key']);
                return;
            }
        }

        if ((needsSelf && enableTranslation) || modes.includes('others') || modes.includes('screen')) {
            if (translationApiType === 'openrouter') {
                const missingFields = [];
                const missingLabels = [];

                if (!llmBaseUrl) {
                    missingFields.push('llm-base-url');
                    missingLabels.push(t('label.llmBaseUrl'));
                }
                if (!llmModel) {
                    missingFields.push('llm-model');
                    missingLabels.push(t('label.llmModel'));
                }
                if (!hasLLMKey) {
                    missingFields.push('llm-api-key');
                    missingLabels.push(t('label.llmKey'));
                }

                if (missingFields.length > 0) {
                    const message = t('msg.llmFieldRequired', { field: missingLabels[0] });
                    showMessage('❌ ' + message, 'error');
                    startBtn.disabled = false;
                    startBtn.textContent = t('btn.startAllModes');
                    expandLLMSettingsPanel(missingFields);
                    return;
                }
            }

        }

        const configSaved = await saveConfig(true);
        if (!configSaved) {
            console.error('同步配置失败');
            showMessage('❌ ' + t('msg.syncConfigFailed'), 'error');
            startBtn.disabled = false;
            startBtn.textContent = t('btn.startAllModes');
            return;
        }
        console.log('✓ 配置已同步到服务器');

        const apiKeys = {
            dashscope: dashscopeKey,
            llm: llmKey,
            screen_ocr: getScreenOcrApiKey(),
        };

        const response = await fetch(`${API_BASE}/modes/start`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                modes,
                api_keys: apiKeys,
                bypass_osc_udp_port_check:
                    document.getElementById('bypass-osc-udp-port-check')?.checked === true,
            }),
        });

        const result = await response.json();

        if (result.success) {
            const acceleratorWarning = localizeBackendMessage(
                result.accelerator_warning_message_id,
                result.accelerator_warning_message,
            );
            const udpConflictWarning = buildUdpPortConflictWarning(result, t);
            const vrchatOscWarning = buildVrchatOscNotListeningWarning(result, t);
            showMessage('✅ ' + t('msg.serviceStarting'), 'success');
            if (udpConflictWarning) {
                showMessage('⚠️ ' + udpConflictWarning, 'warning');
            }
            if (vrchatOscWarning) {
                showMessage('⚠️ ' + vrchatOscWarning, 'warning');
            }
            if (acceleratorWarning) {
                showMessage('⚠️ ' + acceleratorWarning, 'warning');
            }
            await updateStatus();
            setTimeout(updateStatus, 500);
        } else {
            const localizedMsg = localizeBackendMessage(result.message_id, result.message);
            showMessage('❌ ' + t('msg.serviceStartFailed') + localizedMsg, 'error');
            startBtn.disabled = false;
        }
    } catch (error) {
        console.error('启动服务失败:', error);
        showMessage('❌ ' + t('msg.startServiceFailed'), 'error');
        startBtn.disabled = false;
    } finally {
        modes.forEach((m) => setModeBusy(m, false));
        void updateStatus();
    }
}

const SERVICE_MODES = ['self', 'others', 'screen'];
const modeBusy = {};
let lastModeStatus = { self: 'stopped', others: 'stopped', screen: 'stopped' };

function setModeBusy(mode, busy) {
    modeBusy[mode] = !!busy;
    renderModeRows(lastModeStatus);
}

function modeStateKind(raw) {
    const value = String(raw || 'stopped');
    if (value.startsWith('error')) return 'error';
    return ['running', 'starting', 'stopping'].includes(value) ? value : 'stopped';
}

function renderModeRows(modes) {
    const t = window.i18n ? window.i18n.t : (key) => key;
    lastModeStatus = Object.assign({ self: 'stopped', others: 'stopped', screen: 'stopped' }, modes || {});
    SERVICE_MODES.forEach((mode) => {
        const row = document.querySelector(`.mode-row[data-mode="${mode}"]`);
        if (!row) return;
        const raw = String(lastModeStatus[mode] || 'stopped');
        let kind = modeStateKind(raw);
        if (modeBusy[mode] && (kind === 'stopped' || kind === 'error')) kind = 'starting';
        row.dataset.state = kind;
        const stateEl = document.getElementById(`mode-state-${mode}`);
        if (stateEl) {
            stateEl.textContent = kind === 'error' ? raw.replace(/^error:\s*/, '') : t(`mode.state.${kind}`);
            stateEl.title = raw;
        }
        const btn = document.getElementById(`mode-btn-${mode}`);
        if (btn) {
            setElementI18nText(btn, kind === 'running' ? 'btn.modeStop' : 'btn.modeStart', t);
            btn.disabled = kind === 'starting' || kind === 'stopping' || !!modeBusy[mode];
        }
    });
}

async function toggleMode(mode) {
    const kind = modeStateKind(lastModeStatus[mode]);
    if (modeBusy[mode] || kind === 'starting' || kind === 'stopping') return;
    if (kind === 'running') {
        await stopModes([mode]);
    } else {
        await startService([mode]);
    }
}

async function stopModes(modes) {
    const t = window.i18n ? window.i18n.t : (key) => key;
    modes.forEach((m) => setModeBusy(m, true));
    try {
        const response = await fetch(`${API_BASE}/modes/stop`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ modes }),
        });
        const result = await response.json();
        if (!result.success) {
            showMessage(t('msg.serviceStopFailed') + (result.message || ''), 'error');
        }
    } catch (error) {
        console.error('停止模式失败:', error);
        showMessage(t('msg.stopServiceFailed'), 'error');
    } finally {
        modes.forEach((m) => setModeBusy(m, false));
        await updateStatus();
    }
}

// 停止全部模式
async function stopService() {
    const stopBtn = document.getElementById('stop-btn');
    const t = window.i18n ? window.i18n.t : (key) => key;

    stopBtn.disabled = true;
    stopBtn.textContent = t('btn.stopping');

    try {
        const response = await fetch(`${API_BASE}/service/stop`, {
            method: 'POST',
        });

        const result = await response.json();

        if (result.success) {
            const stopMessageKey = getServiceLifecycle(result) === 'stopped'
                ? 'msg.serviceStopSuccess'
                : 'msg.serviceStopping';
            showMessage(t(stopMessageKey), 'success');
            await updateStatus();
            setTimeout(updateStatus, 500);
        } else {
            const localizedMsg = localizeBackendMessage(result.message_id, result.message);
            showMessage(t('msg.serviceStopFailed') + localizedMsg, 'error');
            stopBtn.disabled = false;
        }
    } catch (error) {
        console.error('停止服务失败:', error);
        showMessage(t('msg.stopServiceFailed'), 'error');
        stopBtn.disabled = false;
    }
}

// 重启服务
async function restartService() {
    const tr = window.i18n ? window.i18n.t : (key) => key;
    try {
        const bypassOscRestart = shouldSkipOscUdpPortCheck();
        if (!bypassOscRestart) {
            try {
                const udpPayload = await fetchOscUdpPortCheck();
                const conflictWarning = buildUdpPortConflictWarning(udpPayload, tr);
                const vrchatOscWarning = buildVrchatOscNotListeningWarning(udpPayload, tr);
                if (conflictWarning) {
                    showMessage('⚠️ ' + conflictWarning, 'warning');
                }
                if (vrchatOscWarning) {
                    showMessage('⚠️ ' + vrchatOscWarning, 'warning');
                }
            } catch (e) {
                console.warn('OSC UDP port check failed:', e);
            }
        }

        const response = await fetch(`${API_BASE}/service/restart`, {
            method: 'POST',
        });

        const result = await response.json();

        if (result.success) {
            console.log('服务已重启');
            const udpConflictWarning = buildUdpPortConflictWarning(result, tr);
            const vrchatOscWarning = buildVrchatOscNotListeningWarning(result, tr);
            showMessage('✅ ' + tr('msg.serviceRestarted'), 'success');
            if (udpConflictWarning) {
                showMessage('⚠️ ' + udpConflictWarning, 'warning');
            }
            if (vrchatOscWarning) {
                showMessage('⚠️ ' + vrchatOscWarning, 'warning');
            }
            setTimeout(updateStatus, 500);
            return true;
        } else {
            const localizedMsg = localizeBackendMessage(result.message_id, result.message);
            showMessage('❌ ' + localizedMsg, 'error');
            return false;
        }
    } catch (error) {
        console.error('重启服务失败:', error);
        showMessage('❌ ' + tr('msg.restartFailed'), 'error');
        return false;
    }
}

const RESET_MANAGED_STORAGE_KEYS = new Set([
    CONFIG_STORAGE_KEY,
    CONFIG_STORAGE_KEY + '_timestamp',
    CONFIG_TOUCH_MAIN_MS_KEY,
    LAST_SEEN_BOOT_MS_KEY,
    VRCX_BRIDGE_BUTTON_VISIBLE_STORAGE_KEY,
    PANEL_FLOATING_MODE_STORAGE_KEY,
    QUICK_LANG_STORAGE_KEY,
    QUICK_LANG_BAR_ENABLED_KEY,
]);

function isResetManagedStorageKey(key) {
    return RESET_MANAGED_STORAGE_KEYS.has(key);
}

/**
 * 「恢复默认设置」不动「API 配置」这张卡（地址、模型、国际版、模式；Key 本来就保留）。
 * 以前靠模板把翻译的地址、模型放回去，图像识别的却被默认值冲掉了；现在整张卡原样留着。
 */
function snapshotApiCardFields() {
    const snap = [];
    document.querySelectorAll('#api-keys input[id], #api-keys select[id], #api-keys textarea[id]').forEach((el) => {
        snap.push([el.id, el.type === 'checkbox' || el.type === 'radio' ? el.checked : el.value]);
    });
    return snap;
}

function restoreApiCardFields(snap) {
    snap.forEach(([id, value]) => {
        const el = document.getElementById(id);
        if (!el) return;
        if (typeof value === 'boolean') el.checked = value;
        else el.value = value;
    });
    updateAsrOptionsForInternational(document.getElementById('use-international-endpoint')?.checked ?? false);
    if (window.GanlanApiMode) window.GanlanApiMode.syncFromForm();
}

function snapshotResetManagedStorage() {
    const snapshot = new Map();
    for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key && isResetManagedStorageKey(key)) {
            snapshot.set(key, localStorage.getItem(key));
        }
    }
    return snapshot;
}

function restoreResetManagedStorage(snapshot) {
    const currentKeys = [];
    for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key && isResetManagedStorageKey(key)) {
            currentKeys.push(key);
        }
    }
    currentKeys.forEach((key) => localStorage.removeItem(key));
    snapshot.forEach((value, key) => localStorage.setItem(key, value));

    loadConfigFromLocalStorage();
    loadPanelFloatingModeSetting();
    loadQuickLanguageSettings();
    updateVrcxBridgeControlsVisibility();
}

// 恢复默认设置
async function resetToDefaults() {
    const t = window.i18n ? window.i18n.t : (key) => key;

    const ok = await glConfirm({
        title: t('btn.resetDefaults'),
        message: t('msg.confirmReset'),
        confirmText: t('dialog.reset'),
    });
    if (!ok) {
        return;
    }

    // 把当前表单完整保存为回滚点；重置只有在后端确认保存后才提交本地状态。
    saveConfigToLocalStorage();
    const storageSnapshot = snapshotResetManagedStorage();
    const apiCardSnapshot = snapshotApiCardFields();

    try {
        const defaultsResponse = await fetch(`${API_BASE}/config/defaults`);
        if (!defaultsResponse.ok) {
            throw new Error(`defaults HTTP ${defaultsResponse.status}`);
        }
        const defaults = await defaultsResponse.json();
        applyServerConfigPayload(defaults);
        resetPanelFloatingModeSetting();
        resetQuickLanguageSettings();
        localStorage.setItem(VRCX_BRIDGE_BUTTON_VISIBLE_STORAGE_KEY, 'false');
        updateVrcxBridgeControlsVisibility();

        // 根据翻译开关显示/隐藏翻译选项
        toggleTranslationOptions();

        restoreApiCardFields(apiCardSnapshot);

        const saveSucceeded = await saveConfig(true);
        if (!saveSucceeded) {
            throw new Error('default configuration was rejected by backend');
        }

        // 后端保存成功后才提交浏览器持久化状态。
        saveConfigToLocalStorage();
        showMessage('✅ ' + t('msg.defaultsRestored'), 'success');
    } catch (error) {
        restoreResetManagedStorage(storageSnapshot);
        console.error('恢复默认设置失败:', error);
        showMessage(t('msg.restoreDefaultsFailed'), 'error');
        return;
    }

    // 配置已经成功保存；后续状态检查或重启失败不会回滚配置。
    try {
        const statusResponse = await fetch(`${API_BASE}/status`);
        if (!statusResponse.ok) {
            throw new Error(`status HTTP ${statusResponse.status}`);
        }
        const status = await statusResponse.json();
        if (status.running) {
            await restartService();
        }
    } catch (error) {
        console.error('默认设置已保存，但识别服务状态检查或重启失败:', error);
        showMessage('❌ ' + t('msg.restartFailed'), 'error');
    }
}

function dismissToastMessage(messageEl) {
    if (!messageEl) return;
    if (messageEl._replaceFlashTimer != null) {
        clearTimeout(messageEl._replaceFlashTimer);
        messageEl._replaceFlashTimer = null;
    }
    if (messageEl._hideTimer != null) {
        clearTimeout(messageEl._hideTimer);
        messageEl._hideTimer = null;
    }
    delete messageEl.dataset.signature;
    messageEl.className = 'message';
    setTimeout(() => {
        if (messageEl.parentNode) {
            messageEl.parentNode.removeChild(messageEl);
        }
    }, 320);
}

function flashToastMessage(messageEl) {
    if (!messageEl) return;
    if (messageEl._replaceFlashTimer != null) {
        clearTimeout(messageEl._replaceFlashTimer);
        messageEl._replaceFlashTimer = null;
    }
    messageEl.classList.remove('message-replace-flash');
    void messageEl.offsetWidth;
    messageEl.classList.add('message-replace-flash');
    messageEl._replaceFlashTimer = setTimeout(() => {
        messageEl.classList.remove('message-replace-flash');
        messageEl._replaceFlashTimer = null;
    }, 520);
}

function scheduleToastAutoHide(messageEl, type) {
    if (!messageEl) return;
    if (messageEl._hideTimer != null) {
        clearTimeout(messageEl._hideTimer);
        messageEl._hideTimer = null;
    }
    if (type === 'success') {
        messageEl._hideTimer = setTimeout(() => {
            dismissToastMessage(messageEl);
        }, 10000);
    }
}

// 显示消息：不同消息可同时展示；相同消息刷新已有 toast，避免重复堆叠。
// success 自动消失；warning/error 保留到用户关闭。
function showMessage(text, type) {
    const toastArea = document.getElementById('toast-area');
    if (!toastArea) return;

    const signature = `${type}\u0000${text}`;
    const existing = Array.from(toastArea.querySelectorAll('.message')).find(
        (el) => el.dataset.signature === signature,
    );
    if (existing) {
        flashToastMessage(existing);
        scheduleToastAutoHide(existing, type);
        return;
    }

    const messageEl = document.createElement('div');
    messageEl.className = 'message';
    messageEl.dataset.signature = signature;

    const messageTextEl = document.createElement('span');
    messageTextEl.className = 'message-text';
    messageTextEl.textContent = text;
    messageEl.appendChild(messageTextEl);

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'message-close';
    closeBtn.setAttribute('aria-label', 'Close');
    closeBtn.innerHTML = '&times;';
    messageEl.appendChild(closeBtn);

    toastArea.appendChild(messageEl);
    messageEl.className = 'message ' + type;
    flashToastMessage(messageEl);
    scheduleToastAutoHide(messageEl, type);
}

// 显示本地化消息（使用消息ID）
function showLocalizedMessage(messageId, type, params = {}) {
    const text = window.i18n ? window.i18n.t(messageId, params) : messageId;
    showMessage(text, type);
}

// 本地化后端消息
// 后端返回 message_id（消息ID）和 message（默认消息）
// 前端根据 message_id 获取本地化文本，如果没有则使用默认消息
function localizeBackendMessage(messageId, defaultMessage) {
    if (!messageId) {
        return defaultMessage || '';
    }
    const t = window.i18n ? window.i18n.t : (key) => key;
    const localized = t(messageId);
    // 如果翻译结果和 key 相同，说明没有找到翻译，使用默认消息
    return localized !== messageId ? localized : (defaultMessage || messageId);
}

// 图标是 HTML 里的 chevron-up SVG，折叠时由 CSS 旋转 180°，这里只切类名
function updateCollapsibleIcon(icon, collapsed) {
    if (!icon) return;
    icon.classList.toggle('collapsed', collapsed);
}

// 给带 data-restart-required 的字段标题后面加「需重启」小标签
function decorateRestartRequiredFields() {
    document.querySelectorAll('[data-restart-required="true"]').forEach((el) => {
        let title = el.id ? document.querySelector(`label[for="${el.id}"]`) : null;
        if (!title) {
            const row = el.closest('label');
            if (row) title = row.querySelector('[data-i18n^="label."]');
        }
        if (!title) {
            const group = el.closest('.flex-col');
            if (group) title = group.querySelector('[data-i18n^="label."]');
        }
        if (!title || title.dataset.restartBadge === 'true') return;
        title.dataset.restartBadge = 'true';
        const badge = document.createElement('span');
        badge.className = 'restart-badge';
        badge.dataset.i18n = 'badge.restartRequired';
        badge.textContent = window.i18n ? window.i18n.t('badge.restartRequired') : '需重启';
        // 标题外包一行，避免在 flex-col 里被挤到下一行；徽标不能放进标题里，i18n 会重写标题文本
        const row = document.createElement('div');
        row.className = 'restart-title-row';
        title.parentNode.insertBefore(row, title);
        row.appendChild(title);
        row.appendChild(badge);
    });
}

const COLLAPSIBLE_FOCUSABLE_SELECTOR = [
    'a[href]',
    'area[href]',
    'button',
    'input',
    'select',
    'textarea',
    'iframe',
    'object',
    'embed',
    '[contenteditable]:not([contenteditable="false"])',
    '[tabindex]',
].join(',');

function getCollapsibleHeader(content) {
    if (!content) return null;

    const header = content.previousElementSibling;
    if (header && header.classList.contains('collapsible-header')) {
        return header;
    }

    return null;
}

function setCollapsedTabStop(element, disabled) {
    if (!element) {
        return;
    }

    if (disabled) {
        if (element.dataset.collapseOriginalTabindex === undefined) {
            element.dataset.collapseOriginalTabindex = element.hasAttribute('tabindex')
                ? element.getAttribute('tabindex')
                : '';
        }
        element.setAttribute('tabindex', '-1');
        element.dataset.collapseTabDisabled = 'true';
        return;
    }

    if (element.dataset.collapseTabDisabled !== 'true') {
        return;
    }

    const originalTabindex = element.dataset.collapseOriginalTabindex || '';
    if (originalTabindex) {
        element.setAttribute('tabindex', originalTabindex);
    } else {
        element.removeAttribute('tabindex');
    }

    delete element.dataset.collapseOriginalTabindex;
    delete element.dataset.collapseTabDisabled;
}

function syncCollapsedDescendantTabStops() {
    document.querySelectorAll(COLLAPSIBLE_FOCUSABLE_SELECTOR).forEach((element) => {
        setCollapsedTabStop(element, !!element.closest('.collapsible-content.collapsed'));
    });
}

function syncCollapsibleAccessibility(content) {
    if (!content) return;

    const collapsed = content.classList.contains('collapsed');
    const header = getCollapsibleHeader(content);
    if (header) {
        header.setAttribute('role', 'button');
        header.setAttribute('tabindex', '0');
        if (content.id) {
            header.setAttribute('aria-controls', content.id);
        }
        header.setAttribute('aria-expanded', collapsed ? 'false' : 'true');

        if (header.dataset.collapsibleKeyboardBound !== 'true') {
            header.addEventListener('keydown', (event) => {
                if (event.key !== 'Enter' && event.key !== ' ') {
                    return;
                }

                event.preventDefault();
                if (content.id) {
                    toggleCollapsible(content.id);
                }
            });
            header.dataset.collapsibleKeyboardBound = 'true';
        }
    }

    const shouldMoveFocusToHeader = collapsed && content.contains(document.activeElement) && header;

    content.setAttribute('aria-hidden', collapsed ? 'true' : 'false');
    if ('inert' in content) {
        content.inert = collapsed;
    }

    if (shouldMoveFocusToHeader) {
        header.focus({ preventScroll: true });
    }

    syncCollapsedDescendantTabStops();
}

function syncCollapsibleContainerState(content) {
    if (!content) return;

    const container = content.parentElement;
    if (!container) return;

    container.classList.add('collapsible-container');

    container.classList.toggle(
        'collapsible-container-collapsed',
        content.classList.contains('collapsed'),
    );
}

function initializeCollapsibleStates() {
    document.querySelectorAll('.collapsible-content').forEach((content) => {
        bindCollapsibleTransitionCleanup(content);
        syncCollapsibleContainerState(content);
        syncCollapsibleAccessibility(content);

        if (!content.id) return;
        const icon = document.getElementById(`${content.id}-icon`);
        updateCollapsibleIcon(icon, content.classList.contains('collapsed'));
    });
}

function bindCollapsibleTransitionCleanup(content) {
    if (!content || content.dataset.collapseTransitionBound === 'true') {
        return;
    }

    content.addEventListener('transitionend', (event) => {
        if (event.propertyName !== 'max-height') {
            return;
        }

        if (content.classList.contains('collapsed')) {
            content.style.maxHeight = '0px';
            content.style.overflow = 'hidden';
            return;
        }

        content.style.maxHeight = 'none';
        content.style.overflow = 'visible';
    });

    content.dataset.collapseTransitionBound = 'true';
}

const COLLAPSE_ANIMATION_MS = 260;
const COLLAPSE_ANIMATION_EASING = 'cubic-bezier(0.25, 0.8, 0.25, 1)';

// 展开 / 收起时高度平滑过渡（2026-10-17 岚：展开没有动画）。
// collapsed 类照常立刻切换（别处靠它判断状态），收起时用行内 display 让内容在动画期间还显示着。
function animateCollapsible(content, expanding) {
    if (content._collapseAnimation) {
        content._collapseAnimation.cancel();
    }
    const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced || typeof content.animate !== 'function') {
        return;
    }

    let height;
    if (expanding) {
        height = content.getBoundingClientRect().height;
    } else {
        // 已经加了 collapsed，先用行内 display 把它留在页面上
        content.style.setProperty('display', content.dataset.collapseDisplay || 'block', 'important');
        height = content.getBoundingClientRect().height;
    }
    if (!height) {
        content.style.removeProperty('display');
        return;
    }

    content.style.overflow = 'hidden';
    const frames = [
        { height: '0px', opacity: 0 },
        { height: `${height}px`, opacity: 1 },
    ];
    const animation = content.animate(expanding ? frames : frames.reverse(), {
        duration: COLLAPSE_ANIMATION_MS,
        easing: COLLAPSE_ANIMATION_EASING,
    });
    content._collapseAnimation = animation;
    const cleanup = () => {
        if (content._collapseAnimation !== animation) return;
        content._collapseAnimation = null;
        content.style.removeProperty('overflow');
        content.style.removeProperty('display');
    };
    animation.onfinish = cleanup;
    animation.oncancel = cleanup;
}

// 折叠/展开面板
function toggleCollapsible(id) {
    const content = document.getElementById(id);
    const icon = document.getElementById(id + '-icon');
    if (!content) return;

    const isCollapsed = content.classList.contains('collapsed');
    if (isCollapsed) {
        content.classList.remove('collapsed');
        content.style.removeProperty('max-height');
        updateCollapsibleIcon(icon, false);
        animateCollapsible(content, true);
    } else {
        // 记下展开时的 display（有的内容是 flex），收起动画期间照它显示
        content.dataset.collapseDisplay = getComputedStyle(content).display;
        content.classList.add('collapsed');
        content.style.removeProperty('max-height');
        updateCollapsibleIcon(icon, true);
        animateCollapsible(content, false);
    }
    syncCollapsibleContainerState(content);
    syncCollapsibleAccessibility(content);
}

// 打开状态小面板
// ===================== 显示方式：桌面 / VR =====================

let currentDisplayMode = 'desktop';
let displayModeBusy = false;

function applyDisplayMode(mode) {
    currentDisplayMode = mode === 'vr' ? 'vr' : 'desktop';
    document.body.dataset.display = currentDisplayMode;
    document.querySelectorAll('.display-opt').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.display === currentDisplayMode);
    });
}

async function setDisplayMode(mode) {
    const t = window.i18n ? window.i18n.t : (key) => key;
    const next = mode === 'vr' ? 'vr' : 'desktop';
    if (next === currentDisplayMode || displayModeBusy) return;
    displayModeBusy = true;
    applyDisplayMode(next);
    touchMainPanelUserEditedAt();
    saveConfigToLocalStorage();
    try {
        const response = await fetch(`${API_BASE}/display-mode`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: next }),
        });
        const result = await response.json();
        if (result.success && result.config_applied_at_ms) {
            setMainConfigTouchMs(result.config_applied_at_ms);
        }
        showMessage(t(next === 'vr' ? 'msg.displayVr' : 'msg.displayDesktop'), 'success');
        updateStatus();
    } catch (error) {
        showMessage('❌ ' + error, 'error');
    } finally {
        displayModeBusy = false;
    }
}

function renderVrOverlayStatus(info, t) {
    const row = document.getElementById('vr-status-row');
    const text = document.getElementById('vr-status-text');
    const btn = document.getElementById('vr-launch-btn');
    if (!row || !text) return;
    const connected = !!info?.connected;
    row.dataset.connected = connected ? 'true' : 'false';
    let key = connected ? 'vr.status.connected' : 'vr.status.disconnected';
    if (info && info.openvr_installed === false) key = 'vr.status.noOpenvr';
    setElementI18nText(text, key, t);
    if (btn) btn.hidden = connected;
}

async function launchVrOverlay() {
    try {
        const response = await fetch(`${API_BASE}/vr/launch`, { method: 'POST' });
        const result = await response.json();
        showMessage((result.success ? '' : '❌ ') + (result.message || ''), result.success ? 'success' : 'error');
        setTimeout(updateStatus, 3000);
    } catch (error) {
        showMessage('❌ ' + error, 'error');
    }
}

async function openMiniPanel(role) {
    const t = window.i18n ? window.i18n.t : (key) => key;
    const panelRole = role === 'others' || role === 'screen' ? role : 'self';

    try {
        // 先将当前配置同步到后端
        const configSaved = await saveConfig(true);
        if (!configSaved) {
            showMessage('❌ ' + t('msg.syncConfigFailed'), 'error');
            return;
        }

        // 收集 API Keys 一并发送给后端，写入进程内运行配置
        const apiKeys = {
            dashscope: getEffectiveDashscopeKey(),
            llm: getEffectiveLLMApiKeyForCurrentMode(),
            screen_ocr: getScreenOcrApiKey(),
        };
        const floatingMode = document.getElementById('panel-floating-mode')?.checked ?? false;
        const quickLanguageSettings = getQuickLanguageSettingsForPanel();

        const response = await fetch('/api/open-panel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                api_keys: apiKeys,
                floating_mode: floatingMode,
                quick_language_settings: quickLanguageSettings,
                role: panelRole,
            }),
        });
        const result = await response.json();

        if (result.success) {
            const openedKey = {
                others: 'msg.othersPanelOpened',
                screen: 'msg.screenPanelOpened',
            }[panelRole] || 'msg.selfPanelOpened';
            showMessage(t(openedKey) || t('msg.panelOpened') || '小面板已打开', 'success');
        } else {
            showMessage((t('msg.panelFailed') || '无法打开小面板: ') + result.error, 'error');
        }
    } catch (error) {
        showMessage('请求失败: ' + error, 'error');
    }
}

async function updateIpcStatus() {
    try {
        const response = await fetch(`${API_BASE}/ipc_status`);
        const status = await response.json();
        
        const textSpan = document.getElementById('ipc-status-text');
        const dot = document.getElementById('ipc-status-dot');
        const container = document.getElementById('ipc-status-indicator');
        const smartSection = document.getElementById('smart-target-language-section');
        
        if (!textSpan || !dot || !container) return;
        
        const t = window.i18n ? window.i18n.t : (key) => key;
        if (status.connected) {
            container.style.display = 'flex';
            if (status.mode === 'delegate') {
                textSpan.textContent = t('status.ipcConnectedDelegate');
            } else {
                textSpan.textContent = t('status.ipcConnected');
            }
            dot.style.backgroundColor = '#4caf50';
            if (smartSection) {
                smartSection.style.display = '';
                
                fetch(`${API_BASE}/smart-target-status`)
                    .then(r => r.json())
                    .then(status => {
                        const recentEl = document.getElementById('smart-target-recent-langs');
                        if (!recentEl) return;
                        if (status.recent_languages && status.recent_languages.length > 0) {
                            recentEl.innerHTML = status.recent_languages.map(lang => 
                                `<span style="background: #e8f0fe; color: #1a73e8; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 500;">${lang}</span>`
                            ).join('');
                        } else {
                            const hintText = (typeof t === 'function') ? (t('hint.noRecentLanguages') || '暂无数据') : '暂无数据';
                            recentEl.innerHTML = `<span style="color: #888; font-size: 12px;">${hintText}</span>`;
                        }
                    })
                    .catch(() => {});
            }
        } else {
            container.style.display = 'none';
            if (smartSection) {
                smartSection.style.display = 'none';
            }
        }
    } catch (error) {
        console.error('更新 IPC 状态失败:', error);
    }
}

function updateSmartTargetVisibility() {
    const primaryEnabled = document.getElementById('smart-target-primary-enabled')?.checked ?? true;
    const secondaryEnabled = document.getElementById('smart-target-secondary-enabled')?.checked ?? false;
    const details = document.getElementById('smart-target-settings');
    const advanced = document.getElementById('smart-target-advanced-settings');
    const badge = document.getElementById('smart-target-activated-badge');

    if (details) {
        details.style.display = '';
    }
    if (badge) {
        badge.hidden = !(primaryEnabled || secondaryEnabled);
    }
    if (details && !details.classList.contains('collapsed') && details.style.maxHeight !== 'none') {
        details.style.maxHeight = `${details.scrollHeight}px`;
    }
}

document.addEventListener('change', (e) => {
    if (e.target.id === 'smart-target-primary-enabled' || e.target.id === 'smart-target-secondary-enabled') {
        updateSmartTargetVisibility();
    }
});

// ==========================================================================
// 自定义 API（OpenAI 兼容）
// ==========================================================================
function applyCustomApiConfigToForm(config) {
    const setVal = (id, v) => {
        const el = document.getElementById(id);
        if (el && (v !== undefined && v !== null)) el.value = v;
    };
    if (!config) return;
    setVal('custom-api-base-url', config.CUSTOM_API_BASE_URL ?? '');
    setVal('custom-api-key', config.CUSTOM_API_KEY ?? '');
    setVal('custom-api-model', config.CUSTOM_API_MODEL ?? '');
    setVal('custom-api-target-language', config.CUSTOM_API_TARGET_LANGUAGE ?? 'ja');
    setVal('custom-api-timeout', config.CUSTOM_API_TIMEOUT ?? 30);
    setVal('custom-api-prompt', config.CUSTOM_API_PROMPT ?? '');
    setVal('custom-api-audio-format', config.CUSTOM_API_AUDIO_FORMAT ?? 'wav');
}

function getCustomApiConfigFromForm() {
    const getVal = (id) => document.getElementById(id)?.value ?? '';
    const getNum = (id, def) => {
        const v = parseInt(document.getElementById(id)?.value ?? '', 10);
        return Number.isFinite(v) ? v : def;
    };
    return {
        CUSTOM_API_BASE_URL: getVal('custom-api-base-url').trim(),
        CUSTOM_API_KEY: getVal('custom-api-key').trim(),
        CUSTOM_API_MODEL: getVal('custom-api-model').trim(),
        CUSTOM_API_TARGET_LANGUAGE: getVal('custom-api-target-language').trim() || 'ja',
        CUSTOM_API_TIMEOUT: getNum('custom-api-timeout', 30),
        CUSTOM_API_PROMPT: getVal('custom-api-prompt'),
        CUSTOM_API_AUDIO_FORMAT: getVal('custom-api-audio-format') || 'wav',
    };
}

// 语音识别（OpenAI 兼容）的「测试连通性」：发半秒静音到 /audio/transcriptions（api-mode.js 的 testApi）
function testCustomApi() {
    if (window.GanlanApiMode) return window.GanlanApiMode.testApi('asr');
}

// ==========================================================================
// 预设管理（presets.json）
// ==========================================================================
async function refreshPresetList() {
    const select = document.getElementById('preset-select');
    if (!select) return;
    try {
        const resp = await fetch('/api/presets');
        const data = await resp.json();
        const list = (data.presets || []);
        // 清空除占位项外的选项
        while (select.options.length > 1) select.remove(1);
        list.forEach((p) => {
            const opt = document.createElement('option');
            opt.value = p.name;
            opt.textContent = p.name;
            select.appendChild(opt);
        });
    } catch (err) {
        console.error('refreshPresetList failed', err);
    }
}

function onPresetSelectChange() {
    const select = document.getElementById('preset-select');
    const nameInput = document.getElementById('preset-name');
    if (!select || !nameInput) return;
    if (select.value) nameInput.value = select.value;
}

function onPresetNameInput() {
    const select = document.getElementById('preset-select');
    const nameInput = document.getElementById('preset-name');
    if (!select || !nameInput) return;
    // 编辑预设名时取消下拉选中
    if (select.value && select.value !== nameInput.value) {
        select.value = '';
    }
}

async function saveCurrentPreset() {
    const status = document.getElementById('preset-status');
    const nameInput = document.getElementById('preset-name');
    const name = (nameInput?.value || '').trim();
    if (!name) {
        if (status) { status.style.color = 'var(--danger)'; status.textContent = '请输入预设名'; }
        return;
    }
    if (status) { status.style.color = 'var(--fg-muted)'; status.textContent = '保存中…'; }
    try {
        // 从所有可序列化字段聚合当前值
        const values = collectAllSerializableConfig();
        const resp = await fetch('/api/presets', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, values }),
        });
        const data = await resp.json();
        if (data.success) {
            if (status) { status.style.color = 'var(--success)'; status.textContent = `✓ 已保存「${name}」（${data.fields.length} 项）`; }
            await refreshPresetList();
            const sel = document.getElementById('preset-select');
            if (sel) sel.value = name;
        } else {
            if (status) { status.style.color = 'var(--danger)'; status.textContent = `✗ ${data.message || '保存失败'}`; }
        }
    } catch (err) {
        if (status) { status.style.color = 'var(--danger)'; status.textContent = `✗ ${err.message || err}`; }
    }
}

async function applySelectedPreset() {
    const status = document.getElementById('preset-status');
    const select = document.getElementById('preset-select');
    const name = select?.value;
    if (!name) {
        if (status) { status.style.color = 'var(--danger)'; status.textContent = '请先在下拉中选择一个预设'; }
        return;
    }
    try {
        const resp = await fetch(`/api/presets/${encodeURIComponent(name)}`);
        const data = await resp.json();
        if (!data.success) {
            if (status) { status.style.color = 'var(--danger)'; status.textContent = `✗ ${data.message || '未找到'}`; }
            return;
        }
        applyPresetValuesToForm(data.values);
        if (status) { status.style.color = 'var(--success)'; status.textContent = `✓ 已应用「${name}」`; }
        if (typeof onSettingChange === 'function') onSettingChange();
    } catch (err) {
        if (status) { status.style.color = 'var(--danger)'; status.textContent = `✗ ${err.message || err}`; }
    }
}

async function deleteSelectedPreset() {
    const status = document.getElementById('preset-status');
    const select = document.getElementById('preset-select');
    const name = select?.value;
    if (!name) {
        if (status) { status.style.color = 'var(--danger)'; status.textContent = '请先选择要删除的预设'; }
        return;
    }
    const t = window.i18n ? window.i18n.t : (key) => key;
    const ok = await glConfirm({
        title: t('dialog.presetDeleteTitle'),
        message: t('msg.presetDeleteConfirm', { name }),
        confirmText: t('dialog.delete'),
        danger: true,
    });
    if (!ok) return;
    try {
        const resp = await fetch(`/api/presets/${encodeURIComponent(name)}`, { method: 'DELETE' });
        const data = await resp.json();
        if (data.deleted) {
            if (status) { status.style.color = 'var(--success)'; status.textContent = `✓ 已删除「${name}」`; }
            const nameInput = document.getElementById('preset-name');
            if (nameInput) nameInput.value = '';
            await refreshPresetList();
        } else {
            if (status) { status.style.color = 'var(--fg-muted)'; status.textContent = '未找到该预设'; }
        }
    } catch (err) {
        if (status) { status.style.color = 'var(--danger)'; status.textContent = `✗ ${err.message || err}`; }
    }
}

// --------------------------------------------------------------------------
// 下载文件（导出预设、个性化配置、日志共用）
// --------------------------------------------------------------------------
// Windows 文件名里不能有的字符换成 _（预设名可以是中文，Blob + download 属性按 UTF-8 存名字，不会乱码）
function safeFileName(name, fallback = 'GanlanTranslate') {
    const cleaned = String(name || '')
        .replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_')
        .replace(/^[\s.]+|[\s.]+$/g, '')
        .slice(0, 120);
    if (!cleaned || /^(con|prn|aux|nul|com\d|lpt\d)$/i.test(cleaned)) return fallback;
    return cleaned;
}

function downloadTextFile(filename, text, type = 'text/plain') {
    const blob = new Blob([text], { type: `${type};charset=utf-8` });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 4000);
}

// --------------------------------------------------------------------------
// 预设文件导出 / 导入（1.1.2：文件名是「预设名.json」；可以选择带上 API Key，要确认三次，默认不带）
// --------------------------------------------------------------------------
const PRESET_EXPORT_KIND = 'ganlan-preset';

function _setPresetStatus(text, kind = 'muted') {
    const status = document.getElementById('preset-status');
    if (!status) return;
    status.dataset.kind = kind;
    status.textContent = text;
}

function collectBrowserApiKeys() {
    const keys = {
        dashscope: document.getElementById('dashscope-api-key')?.value.trim() || '',
        llm: document.getElementById('llm-api-key')?.value.trim() || '',
        custom_asr: document.getElementById('custom-api-key')?.value.trim() || '',
        screen_ocr: document.getElementById('screen-ocr-api-key')?.value.trim() || '',
    };
    Object.keys(keys).forEach((k) => { if (!keys[k]) delete keys[k]; });
    return keys;
}

async function confirmExportApiKeys(t) {
    const steps = [
        { title: t('preset.keyWarn1Title'), message: t('preset.keyWarn1'), confirm: t('preset.keyWarnNext') },
        { title: t('preset.keyWarn2Title'), message: t('preset.keyWarn2'), confirm: t('preset.keyWarnNext') },
        { title: t('preset.keyWarn3Title'), message: t('preset.keyWarn3'), confirm: t('preset.keyWarnFinal') },
    ];
    for (const step of steps) {
        const ok = await glConfirm({ title: step.title, message: step.message, confirmText: step.confirm, danger: true });
        if (!ok) return false;
    }
    return true;
}

async function exportPresetsFile() {
    const t = window.i18n ? window.i18n.t : (key) => key;
    const select = document.getElementById('preset-select');
    const nameInput = document.getElementById('preset-name');
    const selected = select?.value || '';
    const typed = (nameInput?.value || '').trim();
    const name = selected || typed;
    if (!name) {
        _setPresetStatus('✗ ' + t('preset.exportNeedName'), 'error');
        highlightInput(selected ? 'preset-select' : 'preset-name');
        return;
    }

    // 选了预设导出存好的那份；没选、只写了名字就导出当前设置
    let values;
    try {
        if (selected) {
            const resp = await fetch(`/api/presets/${encodeURIComponent(selected)}`);
            const data = await resp.json();
            if (!data.success) throw new Error(data.message || t('preset.notFound'));
            values = data.values || {};
        } else {
            values = collectAllSerializableConfig();
        }
    } catch (err) {
        _setPresetStatus(`✗ ${err.message || err}`, 'error');
        return;
    }
    delete values.CUSTOM_API_KEY;   // Key 只按下面的选项决定带不带

    const filename = `${safeFileName(name, 'preset')}.json`;
    const keys = collectBrowserApiKeys();
    const body = document.createElement('div');
    body.className = 'preset-export-body';
    const fileRow = document.createElement('div');
    fileRow.className = 'preset-export-file';
    fileRow.textContent = filename;
    const option = document.createElement('label');
    option.className = 'preset-export-option';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.className = 'preset-export-check';
    checkbox.checked = false;
    checkbox.disabled = Object.keys(keys).length === 0;
    const optionText = document.createElement('span');
    optionText.className = 'preset-export-option-text';
    const optionTitle = document.createElement('span');
    optionTitle.className = 'preset-export-option-title';
    optionTitle.textContent = t('preset.includeKeys');
    const optionHint = document.createElement('span');
    optionHint.className = 'preset-export-option-hint';
    optionHint.textContent = checkbox.disabled ? t('preset.noKeysToExport') : t('preset.includeKeysHint');
    optionText.append(optionTitle, optionHint);
    option.append(checkbox, optionText);
    body.append(fileRow, option);

    const go = await glDialog({
        title: t('preset.exportTitle'),
        message: t('preset.exportMessage', { name }),
        body,
        cancelValue: false,
        buttons: [
            { text: t('dialog.cancel'), value: false, kind: 'secondary' },
            { text: t('preset.exportButton'), value: true, kind: 'primary', focus: true },
        ],
    });
    if (!go) return;

    const includeKeys = checkbox.checked;
    if (includeKeys && !(await confirmExportApiKeys(t))) {
        _setPresetStatus(t('preset.exportCancelled'), 'muted');
        return;
    }

    const file = {
        kind: PRESET_EXPORT_KIND,
        schema_version: 1,
        app: 'GanlanTranslate',
        name,
        exported_at: new Date().toISOString(),
        values,
    };
    if (includeKeys) file.api_keys = keys;
    downloadTextFile(filename, JSON.stringify(file, null, 2), 'application/json');
    _setPresetStatus('✓ ' + t(includeKeys ? 'preset.exportedWithKeys' : 'preset.exported', { file: filename }), includeKeys ? 'warn' : 'success');
}

function applyImportedApiKeys(keys) {
    const map = { dashscope: 'dashscope-api-key', llm: 'llm-api-key', custom_asr: 'custom-api-key', screen_ocr: 'screen-ocr-api-key' };
    Object.entries(map).forEach(([field, id]) => {
        const value = typeof keys[field] === 'string' ? keys[field].trim() : '';
        const input = document.getElementById(id);
        if (!value || !input) return;
        input.value = value;
        input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    onSettingChange();
}

async function importPresetsFile(inputEl) {
    const t = window.i18n ? window.i18n.t : (key) => key;
    const file = inputEl?.files?.[0];
    if (!file) return;
    _setPresetStatus(t('preset.importing'), 'muted');
    try {
        const text = await file.text();
        let parsed;
        try {
            parsed = JSON.parse(text.replace(/^\uFEFF/, ''));
        } catch (e) {
            _setPresetStatus('✗ ' + t('preset.importBadJson'), 'error');
            return;
        }
        const resp = await fetch('/api/presets/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ data: parsed, overwrite: true }),
        });
        const data = await resp.json();
        if (!data.success) {
            _setPresetStatus(`✗ ${data.message || t('preset.importFailed')}`, 'error');
            return;
        }
        const parts = [];
        if (data.imported) parts.push(t('preset.importedNew', { n: data.imported }));
        if (data.overwritten) parts.push(t('preset.importedReplaced', { n: data.overwritten }));
        if (data.skipped) parts.push(t('preset.importedSkipped', { n: data.skipped }));
        _setPresetStatus('✓ ' + t('preset.importDone', { detail: parts.join(t('preset.listSep')) || t('preset.importNoChange') }), 'success');
        await refreshPresetList();
        if (parsed && typeof parsed.name === 'string') {
            const sel = document.getElementById('preset-select');
            if (sel) sel.value = parsed.name;
            onPresetSelectChange();
        }
        // 文件里带了 Key：问一下要不要填进来（Key 只放浏览器表单，不进 presets.json）
        const keys = parsed && parsed.api_keys && typeof parsed.api_keys === 'object' ? parsed.api_keys : null;
        if (keys && Object.values(keys).some((v) => typeof v === 'string' && v.trim())) {
            const ok = await glConfirm({
                title: t('preset.importKeysTitle'),
                message: t('preset.importKeysMessage'),
                confirmText: t('preset.importKeysConfirm'),
                cancelText: t('preset.importKeysSkip'),
            });
            if (ok) {
                applyImportedApiKeys(keys);
                showMessage(t('preset.importKeysDone'), 'success');
            }
        }
    } catch (err) {
        _setPresetStatus(`✗ ${err.message || err}`, 'error');
    } finally {
        inputEl.value = '';
    }
}

// 把当前表单所有可序列化字段聚合为单一对象（用于保存预设）。
// 调用方负责白名单；这里先聚合所有 form 字段，preset API 再按白名单过滤。
function collectAllSerializableConfig() {
    const out = {};
    const ids = [
        'asr-backend', 'source-language', 'target-language', 'secondary-target-language', 'fallback-language',
        'translation-api-type', 'llm-base-url', 'llm-model', 'llm-translation-formality', 'llm-translation-style',
        'openrouter-streaming-mode', 'openai-compat-extra-body-json', 'llm-parallel-fastest-mode',
        'enable-translation', 'show-partial-results', 'enable-furigana', 'enable-pinyin',
        'enable-arabic-reshaper', 'remove-trailing-period', 'show-original-and-lang-tag', 'text-fancy-style',
        'enable-hot-words', 'enable-double-mute-clear', 'vad-enabled',
        'vad-mode', 'vad-threshold', 'vad-min-speech-duration', 'vad-max-speech-duration',
        'vad-silence-duration', 'vad-pre-speech-duration',
        'osc-compat-mode', 'osc-compat-listen-port', 'osc-send-target-port', 'osc-send-error-messages',
        'language-detector', 'mute-delay', 'panel-width', 'show-vrcx-bridge-button',
        'panel-floating-mode', 'enable-quick-lang-bar', 'enable-reverse-translation',
        'quick-lang-1', 'quick-lang-2', 'quick-lang-3', 'quick-lang-4',
        'use-international-endpoint',
        'smart-target-primary-enabled', 'smart-target-secondary-enabled',
        'smart-target-strategy', 'smart-target-window-size', 'smart-target-min-samples',
        'smart-target-exclude-self',
        'custom-api-base-url', 'custom-api-key', 'custom-api-model',
        'custom-api-target-language', 'custom-api-timeout',
        'custom-api-prompt', 'custom-api-audio-format',
        'qwen-audio3-model',
        'api-mode', 'bailian-llm-model', 'bailian-ocr-model',
        'others-target-language',
        'screen-target-language', 'screen-hotkey',
        'screen-ocr-base-url', 'screen-ocr-model',
        'vrchat-mic-detection-enabled', 'vrchat-mic-user-open',
    ];
    ids.forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;
        if (el.type === 'checkbox') {
            out[idToConfigKey(id)] = el.checked;
        } else if (el.tagName === 'SELECT' || el.tagName === 'INPUT') {
            out[idToConfigKey(id)] = el.value;
        }
    });
    return out;
}

// 把预设值映射回 config 字段名 → form 元素 id（与 collectAllSerializableConfig 对应）
const PRESET_ID_TO_CONFIG = {
    'asr-backend': 'PREFERRED_ASR_BACKEND',
    'source-language': 'SOURCE_LANGUAGE',
    'target-language': 'TARGET_LANGUAGE',
    'secondary-target-language': 'SECONDARY_TARGET_LANGUAGE',
    'fallback-language': 'FALLBACK_LANGUAGE',
    'translation-api-type': 'TRANSLATION_API_TYPE',
    'llm-base-url': 'LLM_BASE_URL',
    'llm-model': 'LLM_MODEL',
    'llm-translation-formality': 'LLM_TRANSLATION_FORMALITY',
    'llm-translation-style': 'LLM_TRANSLATION_STYLE',
    'openrouter-streaming-mode': 'LLM_STREAMING_TRANSLATION',
    'openai-compat-extra-body-json': 'OPENAI_COMPAT_EXTRA_BODY_JSON',
    'llm-parallel-fastest-mode': 'LLM_PARALLEL_FASTEST_MODE',
    'enable-translation': 'ENABLE_TRANSLATION',
    'show-partial-results': 'SHOW_PARTIAL_RESULTS',
    'enable-furigana': 'ENABLE_JA_FURIGANA',
    'enable-pinyin': 'ENABLE_ZH_PINYIN',
    'enable-arabic-reshaper': 'ENABLE_ARABIC_RESHAPER',
    'remove-trailing-period': 'REMOVE_TRAILING_PERIOD',
    'show-original-and-lang-tag': 'SHOW_ORIGINAL_AND_LANG_TAG',
    'text-fancy-style': 'TEXT_FANCY_STYLE',
    'enable-hot-words': 'ENABLE_HOT_WORDS',
    'enable-double-mute-clear': 'ENABLE_DOUBLE_MUTE_CLEAR',
    'vad-enabled': 'VAD_ENABLED',
    'vad-mode': 'LOCAL_VAD_MODE',
    'vad-threshold': 'LOCAL_VAD_THRESHOLD',
    'vad-min-speech-duration': 'LOCAL_VAD_MIN_SPEECH_DURATION',
    'vad-max-speech-duration': 'LOCAL_VAD_MAX_SPEECH_DURATION',
    'vad-silence-duration': 'LOCAL_VAD_SILENCE_DURATION',
    'vad-pre-speech-duration': 'LOCAL_VAD_PRE_SPEECH_DURATION',
    'osc-compat-mode': 'OSC_COMPAT_MODE',
    'osc-compat-listen-port': 'OSC_COMPAT_LISTEN_PORT',
    'osc-send-target-port': 'OSC_SEND_TARGET_PORT',
    'osc-send-error-messages': 'OSC_SEND_ERROR_MESSAGES',
    'language-detector': 'LANGUAGE_DETECTOR',
    'mute-delay': 'MUTE_DELAY_SECONDS',
    'panel-width': 'PANEL_WIDTH',
    'show-vrcx-bridge-button': 'SHOW_VRCX_BRIDGE_BUTTON',
    'panel-floating-mode': 'PANEL_FLOATING_MODE',
    'enable-quick-lang-bar': 'ENABLE_QUICK_LANG_BAR',
    'enable-reverse-translation': 'ENABLE_REVERSE_TRANSLATION',
    'quick-lang-1': 'QUICK_LANG_1',
    'quick-lang-2': 'QUICK_LANG_2',
    'quick-lang-3': 'QUICK_LANG_3',
    'quick-lang-4': 'QUICK_LANG_4',
    'use-international-endpoint': 'USE_INTERNATIONAL_ENDPOINT',
    'smart-target-primary-enabled': 'SMART_TARGET_PRIMARY_ENABLED',
    'smart-target-secondary-enabled': 'SMART_TARGET_SECONDARY_ENABLED',
    'smart-target-strategy': 'SMART_TARGET_LANGUAGE_STRATEGY',
    'smart-target-window-size': 'SMART_TARGET_LANGUAGE_WINDOW_SIZE',
    'smart-target-min-samples': 'SMART_TARGET_LANGUAGE_MIN_SAMPLES',
    'smart-target-exclude-self': 'SMART_TARGET_LANGUAGE_EXCLUDE_SELF_LANGUAGE',
    'custom-api-base-url': 'CUSTOM_API_BASE_URL',
    'custom-api-key': 'CUSTOM_API_KEY',
    'custom-api-model': 'CUSTOM_API_MODEL',
    'custom-api-target-language': 'CUSTOM_API_TARGET_LANGUAGE',
    'custom-api-timeout': 'CUSTOM_API_TIMEOUT',
    'custom-api-prompt': 'CUSTOM_API_PROMPT',
    'custom-api-audio-format': 'CUSTOM_API_AUDIO_FORMAT',
    'qwen-audio3-model': 'QWEN_AUDIO3_ASR_MODEL',
    'api-mode': 'API_MODE',
    'bailian-llm-model': 'BAILIAN_LLM_MODEL',
    'bailian-ocr-model': 'BAILIAN_OCR_MODEL',
    'others-target-language': 'OTHERS_TARGET_LANGUAGE',
    'screen-target-language': 'SCREEN_TARGET_LANGUAGE',
    'screen-hotkey': 'SCREEN_HOTKEY',
    'screen-ocr-base-url': 'SCREEN_OCR_BASE_URL',
    'screen-ocr-model': 'SCREEN_OCR_MODEL',
    'vrchat-mic-detection-enabled': 'VRCHAT_MIC_DETECTION_ENABLED',
    'vrchat-mic-user-open': 'VRCHAT_MIC_USER_OPEN',
};

function idToConfigKey(id) {
    return PRESET_ID_TO_CONFIG[id] || id.replace(/-/g, '_').toUpperCase();
}

function applyPresetValuesToForm(values) {
    if (!values) return;
    for (const [id, configKey] of Object.entries(PRESET_ID_TO_CONFIG)) {
        if (!(configKey in values)) continue;
        const el = document.getElementById(id);
        if (!el) continue;
        const v = values[configKey];
        if (el.type === 'checkbox') el.checked = !!v;
        else if (typeof v === 'string' || typeof v === 'number') el.value = String(v);
    }
    // 旧预设没有 API_MODE：按里面的地址猜（百炼的地址 = 百炼模式）
    if (window.GanlanApiMode) {
        if (!('API_MODE' in values) && 'LLM_BASE_URL' in values) {
            const dash = window.GanlanApiMode.isDashscopeUrl(values.LLM_BASE_URL) || !values.LLM_BASE_URL;
            const modeEl = document.getElementById('api-mode');
            if (modeEl) modeEl.value = dash ? 'bailian' : 'custom';
            if (dash && values.LLM_MODEL) document.getElementById('bailian-llm-model').value = values.LLM_MODEL;
        }
        window.GanlanApiMode.syncFromForm();
    }
}

// 启动时刷新预设下拉
document.addEventListener('DOMContentLoaded', () => {
    refreshPresetList();
});

// ==========================================================================
// VRChat 麦克风检测
// ==========================================================================
const VRCHAT_MIC_DOT_ON = '#5FC08D';
const VRCHAT_MIC_DOT_OFF = '#B7C9BB';

function _vrchatMicT(key, params) {
    if (window.i18n && typeof window.i18n.t === 'function') {
        return window.i18n.t(key, params || {});
    }
    return key;
}

function _setVrchatMicStatusRow(dotId, textId, active, text) {
    const dot = document.getElementById(dotId);
    const textEl = document.getElementById(textId);
    if (dot) dot.style.backgroundColor = active ? VRCHAT_MIC_DOT_ON : VRCHAT_MIC_DOT_OFF;
    if (textEl) textEl.textContent = text;
}

function formatVrchatMicUpdatedAgo(checkedAtSec) {
    if (checkedAtSec == null || !Number.isFinite(Number(checkedAtSec))) {
        return _vrchatMicT('status.vrchatMicUnknown');
    }
    const ageSec = Math.max(0, Math.floor(Date.now() / 1000 - Number(checkedAtSec)));
    if (ageSec < 3) return _vrchatMicT('status.vrchatMicJustNow');
    if (ageSec < 60) return _vrchatMicT('status.vrchatMicSecondsAgo', { n: ageSec });
    return _vrchatMicT('status.vrchatMicMinutesAgo', { n: Math.floor(ageSec / 60) });
}

function getVrchatMicSettingsFromForm() {
    const detectionEl = document.getElementById('vrchat-mic-detection-enabled');
    const userOpenEl = document.getElementById('vrchat-mic-user-open');
    let userOpen = null;
    if (userOpenEl) {
        if (userOpenEl.value === 'true') userOpen = true;
        else if (userOpenEl.value === 'false') userOpen = false;
    }
    return {
        detection_enabled: detectionEl ? detectionEl.checked : true,
        user_open: userOpen,
    };
}

function onVrchatMicSettingChange() {
    const cfg = getVrchatMicSettingsFromForm();
    fetch('/api/vrchat/mic-setting', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cfg),
    }).then((r) => r.json()).then((data) => {
        if (data.success) {
            if (typeof showMessage === 'function') showMessage('麦克风检测设置已保存', 'success');
            if (typeof saveConfigToLocalStorage === 'function') saveConfigToLocalStorage();
        }
    }).catch(() => {});
    refreshVrchatMicStatus();
}

async function refreshVrchatMicStatus() {
    const statusEl = document.getElementById('vrchat-mic-status');
    try {
        const resp = await fetch('/api/vrchat/mic-status');
        const data = await resp.json();
        if (!statusEl) return;

        const running = !!data.is_running;
        const should = data.live_open !== undefined ? !!data.live_open : !!data.should_translate;
        // 服务未跑时仍用瞬时检测结果；有 live_open 时优先展示运行态
        const micOpen = should;

        _setVrchatMicStatusRow(
            'vrchat-mic-process-dot',
            'vrchat-mic-process-text',
            running,
            running ? _vrchatMicT('status.vrchatMicRunning') : _vrchatMicT('status.vrchatMicNotRunning'),
        );
        _setVrchatMicStatusRow(
            'vrchat-mic-open-dot',
            'vrchat-mic-open-text',
            micOpen,
            micOpen ? _vrchatMicT('status.vrchatMicOpen') : _vrchatMicT('status.vrchatMicClosed'),
        );

        const checkedAt = data.checked_at != null ? data.checked_at : (Date.now() / 1000);
        const hasFresh = data.checked_at != null || data.is_running !== undefined;
        _setVrchatMicStatusRow(
            'vrchat-mic-updated-dot',
            'vrchat-mic-updated-text',
            hasFresh,
            formatVrchatMicUpdatedAgo(checkedAt),
        );

        statusEl.setAttribute('data-state', micOpen ? 'ready' : '');
        const detail = data.live_detail || data.detail || '';
        if (detail) statusEl.title = detail;
    } catch (err) {
        _setVrchatMicStatusRow(
            'vrchat-mic-process-dot',
            'vrchat-mic-process-text',
            false,
            _vrchatMicT('status.vrchatMicUnknown'),
        );
        _setVrchatMicStatusRow(
            'vrchat-mic-open-dot',
            'vrchat-mic-open-text',
            false,
            _vrchatMicT('status.vrchatMicUnknown'),
        );
        _setVrchatMicStatusRow(
            'vrchat-mic-updated-dot',
            'vrchat-mic-updated-text',
            false,
            _vrchatMicT('status.vrchatMicUnknown'),
        );
    }
}

function applyVrchatMicSettingsToForm(configData) {
    if (!configData || !configData.vrchat_mic) return;
    const detEl = document.getElementById('vrchat-mic-detection-enabled');
    const userEl = document.getElementById('vrchat-mic-user-open');
    if (detEl) detEl.checked = !!configData.vrchat_mic.detection_enabled;
    if (userEl) {
        const v = configData.vrchat_mic.user_open;
        userEl.value = v === true ? 'true' : (v === false ? 'false' : '');
    }
}

// 在配置加载时应用 + 定时刷新
document.addEventListener('DOMContentLoaded', () => {
    // 初始化时读取一次（在配置应用后）
    setTimeout(() => {
        refreshVrchatMicStatus();
        refreshOthersStatus();
    }, 1500);
    // 每 5 秒刷新状态
    setInterval(() => {
        refreshVrchatMicStatus();
        refreshOthersStatus();
    }, 5000);
});

async function refreshOthersStatus() {
    const el = document.getElementById('others-status-text');
    if (!el) return;
    try {
        const resp = await fetch('/api/subtitles');
        const data = await resp.json();
        const st = data.others_status || {};
        const enabled = !!st.enabled;
        const status = st.capture_status || 'idle';
        const pid = st.capture_pid;
        const speakers = st.speaker_count || 0;
        if (!enabled) {
            el.textContent = (typeof t === 'function' ? t('label.othersEnabled') : '启用翻译他人') + ': OFF';
            return;
        }
        let msg = `ON · ${status}`;
        if (pid) msg += ` · PID ${pid}`;
        msg += ` · speakers ${speakers}`;
        el.textContent = msg;
    } catch (err) {
        el.textContent = '-';
    }
}

// ==========================================================================
// 运行日志
// ==========================================================================
let logsLastTs = null;
let logsLastKey = null; // 已显示的最后一条 (t,line)，用于去重
let logsViewerEl = null;
let logsStatusEl = null;

function _setLogsEmptyPlaceholder() {
    if (!logsViewerEl) return;
    logsViewerEl.textContent = '暂无日志';
    logsViewerEl.dataset.raw = '';
    // 首次空结果不标记 inited，后续仍可全量渲染真实日志
    if (logsStatusEl) logsStatusEl.textContent = '共 0 行';
}

async function refreshLogs() {
    try {
        if (!logsViewerEl) logsViewerEl = document.getElementById('logs-viewer');
        if (!logsViewerEl) return;
        if (!logsStatusEl) logsStatusEl = document.getElementById('logs-status');

        const params = new URLSearchParams({ limit: '600' });
        if (logsLastTs != null) params.set('after_ts', String(logsLastTs));
        const resp = await fetch(`/api/logs?${params.toString()}`);
        const data = await resp.json();
        if (!data.success) return;

        let newLogs = data.logs || [];
        // 增量：跳过已显示的最后一条（后端 >= 语义会产生重复）
        if (logsViewerEl.dataset.inited === 'true' && newLogs.length && logsLastKey) {
            const newest = newLogs[0];
            if (newest.t === logsLastTs && escKey(newest) === logsLastKey) {
                newLogs = newLogs.slice(1);
            }
        }
        if (newLogs.length) {
            logsLastTs = newLogs[0].t;
            logsLastKey = escKey(newLogs[0]);
        }

        let combined;
        if (logsViewerEl.dataset.inited !== 'true') {
            // 首次：全量渲染（顺序 = 最早在前）
            combined = newLogs.slice().reverse().map(escLogLine).join('\n');
            if (!combined) {
                _setLogsEmptyPlaceholder();
                return;
            }
        } else {
            // 增量：新日志追加到末尾；无新日志时保持原样，避免产生空行
            const existing = logsViewerEl.dataset.raw || '';
            const appendLines = newLogs.slice().reverse().map(escLogLine).join('\n');
            if (appendLines) {
                combined = existing ? existing + '\n' + appendLines : appendLines;
            } else {
                combined = existing;
            }
            if (!combined) {
                _setLogsEmptyPlaceholder();
                return;
            }
        }

        if (combined.length > 200000) {
            combined = '…(日志过长已截断)…\n' + combined.slice(-200000);
        }
        const firstRender = logsViewerEl.dataset.inited !== 'true';
        if (firstRender || combined !== logsViewerEl.dataset.raw) {
            // 只有原本就停在底部时才跟随最新日志；用户往上翻着看时保持原位置
            const distanceFromBottom =
                logsViewerEl.scrollHeight - logsViewerEl.scrollTop - logsViewerEl.clientHeight;
            const stickToBottom = firstRender || distanceFromBottom <= 24;
            const previousScrollTop = logsViewerEl.scrollTop;
            logsViewerEl.dataset.raw = combined;
            logsViewerEl.dataset.inited = 'true';
            logsViewerEl.innerHTML = combined;
            logsViewerEl.scrollTop = stickToBottom ? logsViewerEl.scrollHeight : previousScrollTop;
        }

        if (logsStatusEl) {
            const raw = logsViewerEl.dataset.raw || '';
            const lineCount = raw ? raw.split('\n').length : 0;
            logsStatusEl.textContent = `共 ${lineCount} 行`;
        }
    } catch (err) {
        // 静默
    }
}

function escLogLine(item) {
    // 兼容两种格式：纯字符串，或后端返回的 {t, line} 对象
    let line = item;
    if (item && typeof item === 'object' && !Array.isArray(item)) {
        line = item.line ?? '';
    }
    const s = String(line || '');
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function escKey(item) {
    // 生成用于去重的键：时间戳 + 行内容
    if (item && typeof item === 'object' && !Array.isArray(item)) {
        return item.t + '|' + String(item.line ?? '');
    }
    return String(item ?? '');
}

async function clearLogs() {
    const status = document.getElementById('logs-status');
    try {
        await fetch('/api/logs/clear', { method: 'POST' });
        if (logsViewerEl) {
            logsViewerEl.dataset.inited = '';
            logsLastTs = null;
            logsLastKey = null;
            if (!logsStatusEl) logsStatusEl = status;
            _setLogsEmptyPlaceholder();
        } else {
            logsLastTs = null;
            logsLastKey = null;
        }
        if (status) { status.style.color = 'var(--success)'; status.textContent = '已清空'; }
    } catch (err) {
        if (status) { status.style.color = 'var(--danger)'; status.textContent = '清空失败'; }
    }
}

// --------------------------------------------------------------------------
// 分析错误（1.1.2，岚：没填 API Key 直接给结果；填了就调用大模型分析，用弹窗给出结果）
// --------------------------------------------------------------------------
function _analysisEl(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
}

// 模型回的是带「- 」「1.」「**粗体**」的纯文字：按行拆成段落和列表，全部用 textContent，不拼 HTML
function renderAnalysisText(text) {
    const box = _analysisEl('div', 'analysis-text');
    let list = null;
    const appendInline = (parent, line) => {
        line.split(/(\*\*[^*]+\*\*)/g).forEach((part) => {
            if (!part) return;
            if (/^\*\*[^*]+\*\*$/.test(part)) parent.append(_analysisEl('strong', '', part.slice(2, -2)));
            else parent.append(document.createTextNode(part));
        });
    };
    String(text || '').replace(/\r/g, '').split('\n').forEach((raw) => {
        const line = raw.trim();
        if (!line) { list = null; return; }
        const bullet = /^[-*•]\s+(.*)$/.exec(line);
        const numbered = /^(\d+)[.、)]\s*(.*)$/.exec(line);
        const heading = /^#{1,4}\s+(.*)$/.exec(line);
        if (bullet || numbered) {
            const kind = bullet ? 'ul' : 'ol';
            if (!list || list.tagName.toLowerCase() !== kind) {
                list = _analysisEl(kind, 'analysis-list');
                box.append(list);
            }
            const li = _analysisEl('li');
            appendInline(li, bullet ? bullet[1] : numbered[2]);
            list.append(li);
            return;
        }
        list = null;
        const p = _analysisEl(heading ? 'h4' : 'p', heading ? 'analysis-heading' : 'analysis-para');
        appendInline(p, heading ? heading[1] : line);
        box.append(p);
    });
    return box;
}

function renderAnalysisFindings(findings, t) {
    const list = _analysisEl('ul', 'analysis-findings');
    findings.forEach((item, index) => {
        const li = _analysisEl('li', `analysis-finding is-${item.id === 'no_key' ? 'key' : 'rule'}`);
        li.style.setProperty('--i', String(index));
        li.append(_analysisEl('span', 'analysis-finding-dot'));
        const body = _analysisEl('div', 'analysis-finding-body');
        body.append(_analysisEl('div', 'analysis-finding-title', t(`analyze.rule.${item.id}.title`)));
        let fix = t(`analyze.rule.${item.id}.fix`);
        if (item.id === 'no_key' && Array.isArray(item.missing)) {
            fix = t('analyze.rule.no_key.fix', { parts: item.missing.map((m) => t(`analyze.part.${m}`)).join(t('preset.listSep')) });
        }
        body.append(_analysisEl('div', 'analysis-finding-fix', fix));
        if (item.line) body.append(_analysisEl('code', 'analysis-finding-line', item.line));
        li.append(body);
        list.append(li);
    });
    return list;
}

async function analyzeLogs() {
    const t = window.i18n ? window.i18n.t : (key) => key;
    const button = document.getElementById('log-analyze-btn');
    const label = button?.querySelector('span');
    if (button?.disabled) return;
    const plan = window.GanlanApiMode ? window.GanlanApiMode.effective() : null;
    const keysFilled = window.GanlanApiMode ? window.GanlanApiMode.keysFilled() : {};
    const needs = ['asr', 'llm'];
    if (['running', 'starting'].includes(modeStateKind(lastModeStatus.screen)) || modeStateKind(lastModeStatus.screen) === 'error') {
        needs.push('ocr');
    }
    const llm = plan && plan.llm.key ? {
        base_url: plan.llm.baseUrl, api_key: plan.llm.key, model: plan.llm.model, extra_body: plan.llm.extraBody,
    } : {};
    if (button) {
        button.disabled = true;
        button.classList.add('is-busy');
        if (label) label.textContent = llm.api_key ? t('btn.analyzingLogs') : t('btn.checkingLogs');
    }
    let data;
    try {
        const resp = await fetch('/api/logs/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                keys_filled: keysFilled,
                needs,
                llm,
                language: window.i18n ? window.i18n.getCurrentLanguage() : 'zh-CN',
            }),
        });
        data = await resp.json();
        if (!data.success) throw new Error(data.message || resp.status);
    } catch (err) {
        showMessage(t('msg.analyzeFailed') + (err.message || err), 'error');
        return;
    } finally {
        if (button) {
            button.disabled = false;
            button.classList.remove('is-busy');
            if (label) label.textContent = t('btn.analyzeLogs');
        }
    }

    const findings = Array.isArray(data.findings) ? data.findings : [];
    const body = _analysisEl('div', 'analysis-body');
    const noKey = findings.some((f) => f.id === 'no_key');
    if (data.mode === 'llm') {
        body.append(renderAnalysisText(data.analysis));
        if (findings.length) {
            const details = _analysisEl('details', 'analysis-details');
            details.append(_analysisEl('summary', '', t('analyze.ruleDetails', { n: findings.length })));
            details.append(renderAnalysisFindings(findings, t));
            body.append(details);
        }
        body.append(_analysisEl('p', 'analysis-footnote', t('analyze.byModel', { model: data.model || '' })));
    } else {
        const banner = _analysisEl('div', `analysis-banner ${data.llm_error ? 'is-warn' : ''}`);
        if (data.llm_error) banner.textContent = t('analyze.llmFailed', { error: data.llm_error });
        else banner.textContent = data.reason === 'no_key' ? t('analyze.localNoKey') : t('analyze.localOnly');
        body.append(banner);
        if (findings.length) body.append(renderAnalysisFindings(findings, t));
        else body.append(_analysisEl('p', 'analysis-empty', t('analyze.noFindings')));
    }
    if (!data.line_count) body.append(_analysisEl('p', 'analysis-footnote', t('analyze.emptyLog')));

    const buttons = [{ text: t('dialog.ok'), value: 'ok', kind: 'primary', focus: true }];
    if (noKey) buttons.unshift({ text: t('analyze.goFillKey'), value: 'fill', kind: 'secondary' });
    const choice = await glDialog({
        title: data.mode === 'llm' ? t('analyze.titleLlm') : t('analyze.titleLocal'),
        body,
        size: 'wide',
        className: 'log-analysis-dialog',
        cancelValue: 'ok',
        buttons,
    });
    if (choice === 'fill') {
        if (window.GanlanUiPages) window.GanlanUiPages.setAppPage('core', { save: true });
        const plan2 = window.GanlanApiMode ? window.GanlanApiMode.effective() : null;
        const target = !plan2 || plan2.mode === 'bailian' ? 'dashscope-api-key' : (keysFilled.llm ? 'custom-api-key' : 'llm-api-key');
        setTimeout(() => expandLLMSettingsPanel([target]), 60);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        refreshLogs();
    }, 1200);
    setInterval(() => {
        refreshLogs();
    }, 2000);
});
