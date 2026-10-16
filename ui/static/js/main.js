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
const LLM_SELECTED_TEMPLATE_STORAGE_KEY = 'llm_selected_template';
const LLM_TEMPLATE_KEY_STORAGE_PREFIX = 'llm_template_key_';
const LLM_TEMPLATE_BASEURL_STORAGE_PREFIX = 'llm_template_baseurl_';
const LLM_TEMPLATE_MODEL_STORAGE_PREFIX = 'llm_template_model_';
const LLM_TEMPLATE_EXTRABODY_STORAGE_PREFIX = 'llm_template_extrabody_';
const LLM_TEMPLATE_PARALLEL_STORAGE_PREFIX = 'llm_template_parallel_';

// 待显示的警告消息（用于自动切换翻译API）
let pendingWarningMessage = null;

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

function sanitizeOthersAsrBackendValue(value) {
    const raw = (value == null ? '' : String(value)).trim();
    if (!raw) return '';
    return sanitizeAsrBackendValue(raw);
}

function applyOthersTranslationForm(ot) {
    if (!ot) return;
    const el = document.getElementById('others-translation-enabled');
    if (el && Object.prototype.hasOwnProperty.call(ot, 'enabled')) {
        el.checked = !!ot.enabled;
    }
    const asrEl = document.getElementById('others-asr-backend');
    if (asrEl && Object.prototype.hasOwnProperty.call(ot, 'asr_backend')) {
        asrEl.value = sanitizeOthersAsrBackendValue(ot.asr_backend);
    }
    const langEl = document.getElementById('others-target-language');
    if (langEl) {
        langEl.value = (ot.target_language || '').trim() || 'zh-CN';
    }
}

const SCREEN_OCR_PRESETS = {
    qwen: {
        baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        model: 'qwen-vl-ocr',
    },
    openai: {
        baseUrl: 'https://api.openai.com/v1',
        model: 'gpt-4o-mini',
    },
};

function applyScreenTranslationForm(sc) {
    if (!sc) return;
    const enabledEl = document.getElementById('screen-translation-enabled');
    if (enabledEl && Object.prototype.hasOwnProperty.call(sc, 'enabled')) {
        enabledEl.checked = !!sc.enabled;
    }
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

function getScreenTranslationConfigFromForm() {
    return {
        enabled: document.getElementById('screen-translation-enabled')?.checked === true,
        target_language: (document.getElementById('screen-target-language')?.value || '').trim() || 'zh-CN',
        hotkey: (document.getElementById('screen-hotkey')?.value || '').trim(),
        ocr_base_url: (document.getElementById('screen-ocr-base-url')?.value || '').trim()
            || SCREEN_OCR_PRESETS.qwen.baseUrl,
        ocr_model: (document.getElementById('screen-ocr-model')?.value || '').trim()
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
const DEFAULT_LLM_TEMPLATE_NAME = 'custom1';
const SIMPLE_MODE_LLM_TEMPLATE_NAME = 'custom1';
const SIMPLE_MODE_LLM_BASE_URL = '';
const SIMPLE_MODE_LLM_EXTRA_BODY_JSON = '';
const DEFAULT_QWEN_AUDIO3_MODEL = 'qwen-audio-3.0-asr-flash-streaming';

const LLM_TEMPLATE_CONFIGS = {
    'dashscope-qwen35-flash': {
        baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        model: 'qwen3.5-flash',
        extraBody: '{"enable_thinking": false}',
        parallelFastestMode: 'off',
        providerLabelKey: 'btn.llmTemplateDashscopeQwenFlash',
        copyDashscopeKey: true,
    },
    'dashscope-qwen35-plus': {
        baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        model: 'qwen3.5-plus',
        extraBody: '{"enable_thinking": false}',
        parallelFastestMode: 'off',
        providerLabelKey: 'btn.llmTemplateDashscopeQwenPlus',
        copyDashscopeKey: true,
    },
    'deepseek-v4-flash': {
        baseUrl: 'https://api.deepseek.com',
        model: 'deepseek-v4-flash',
        extraBody: '{"thinking": {"type": "disabled"}}',
        parallelFastestMode: 'off',
        providerLabelKey: 'btn.llmTemplateDeepSeekV4Flash',
    },
    openrouter: {
        baseUrl: 'https://openrouter.ai/api/v1',
        parallelFastestMode: 'off',
        providerLabelKey: 'btn.llmTemplateOpenRouter',
    },
    mercury2: {
        baseUrl: 'https://api.inceptionlabs.ai/v1',
        model: 'mercury-2',
        extraBody: '',
        parallelFastestMode: 'off',
        providerLabelKey: 'btn.llmTemplateMercury2',
    },
    custom1: {
        baseUrl: '',
        model: '',
        extraBody: '',
        parallelFastestMode: 'off',
        providerLabelKey: 'btn.llmTemplateCustom1',
        isCustom: true,
    },
    custom2: {
        baseUrl: '',
        model: '',
        extraBody: '',
        parallelFastestMode: 'off',
        providerLabelKey: 'btn.llmTemplateCustom2',
        isCustom: true,
    },
    custom3: {
        baseUrl: '',
        model: '',
        extraBody: '',
        parallelFastestMode: 'off',
        providerLabelKey: 'btn.llmTemplateCustom3',
        isCustom: true,
    },
};

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

let activeLLMTemplate = null;

function isValidLLMTemplateName(templateName) {
    return !!(templateName && Object.prototype.hasOwnProperty.call(LLM_TEMPLATE_CONFIGS, templateName));
}

function normalizeLLMTemplateName(templateName) {
    return isValidLLMTemplateName(templateName) ? templateName : '';
}

function isCustomLLMTemplate(templateName) {
    return !!(templateName && LLM_TEMPLATE_CONFIGS[templateName]?.isCustom);
}

function inferLLMTemplateFromCurrentFields() {
    const baseUrl = (document.getElementById('llm-base-url')?.value || '').trim();
    const model = (document.getElementById('llm-model')?.value || '').trim();

    for (const [templateName, templateConfig] of Object.entries(LLM_TEMPLATE_CONFIGS)) {
        if (templateConfig.isCustom) {
            const storedBaseUrl = getStoredLLMTemplateBaseUrl(templateName);
            const storedModel = getStoredLLMTemplateModel(templateName);
            if (!(storedBaseUrl || storedModel)) {
                continue;
            }
            if (baseUrl === storedBaseUrl && model === storedModel) {
                return templateName;
            }
            continue;
        }
        if (baseUrl !== templateConfig.baseUrl) {
            continue;
        }
        if (Object.prototype.hasOwnProperty.call(templateConfig, 'model')) {
            if (model === templateConfig.model) {
                return templateName;
            }
            continue;
        }
        if (baseUrl === templateConfig.baseUrl) {
            return templateName;
        }
    }

    return null;
}

function updateLLMTemplateButtonStates() {
    const selectedTemplate = getSelectedLLMTemplateName();
    document.querySelectorAll('[data-llm-template]').forEach((button) => {
        const isSelected = button.getAttribute('data-llm-template') === selectedTemplate;
        button.classList.toggle('active', isSelected);
        button.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
    });
}

function updateLLMTemplateFieldLocks() {
    const baseUrlInput = document.getElementById('llm-base-url');
    if (!baseUrlInput) return;
    // 与简易模式一致：API 地址始终可自定义填写
    baseUrlInput.readOnly = false;
    baseUrlInput.classList.remove('readonly-field');
}

function setSelectedLLMTemplateName(templateName, persist = true) {
    const normalized = normalizeLLMTemplateName(SIMPLE_MODE_LLM_TEMPLATE_NAME) || DEFAULT_LLM_TEMPLATE_NAME;
    activeLLMTemplate = normalized;
    if (persist) {
        localStorage.setItem(LLM_SELECTED_TEMPLATE_STORAGE_KEY, normalized);
    }
    updateLLMTemplateButtonStates();
    updateLLMTemplateFieldLocks();
    return normalized;
}

function getSelectedLLMTemplateName() {
    return SIMPLE_MODE_LLM_TEMPLATE_NAME;
}

function hasAnyLLMConnectionFieldValue() {
    return !!(
        (document.getElementById('llm-base-url')?.value || '').trim()
        || (document.getElementById('llm-model')?.value || '').trim()
        || (document.getElementById('llm-api-key')?.value || '').trim()
        || (document.getElementById('openai-compat-extra-body-json')?.value || '').trim()
        || getLLMParallelFastestModeSelect() !== 'off'
    );
}

function ensureSelectedLLMTemplate(configTranslation = null) {
    if (false) {
        setSelectedLLMTemplateName(SIMPLE_MODE_LLM_TEMPLATE_NAME);
        return SIMPLE_MODE_LLM_TEMPLATE_NAME;
    }
    // Check if the template saved in backend config or localStorage is non-existent/invalid.
    // If it was non-empty and is now invalid, we default to deepseek-v4-flash.
    let invalidTemplateDetected = false;
    let originalTemplateName = '';

    if (configTranslation && configTranslation.llm_template) {
        originalTemplateName = configTranslation.llm_template;
        if (!isValidLLMTemplateName(originalTemplateName)) {
            invalidTemplateDetected = true;
        }
    } else {
        const storedTemplate = localStorage.getItem(LLM_SELECTED_TEMPLATE_STORAGE_KEY);
        if (storedTemplate) {
            originalTemplateName = storedTemplate;
            if (!isValidLLMTemplateName(originalTemplateName)) {
                invalidTemplateDetected = true;
            }
        }
    }

    if (invalidTemplateDetected) {
        console.warn(`Detected non-existent/removed LLM template '${originalTemplateName}'. Defaulting to deepseek-v4-flash.`);
        // Default to deepseek-v4-flash and apply its configurations
        applyLLMTemplate(SIMPLE_MODE_LLM_TEMPLATE_NAME);
        return SIMPLE_MODE_LLM_TEMPLATE_NAME;
    }

    const configTemplate = normalizeLLMTemplateName(configTranslation?.llm_template);
    if (configTemplate) {
        return setSelectedLLMTemplateName(configTemplate);
    }

    const storedTemplate = normalizeLLMTemplateName(localStorage.getItem(LLM_SELECTED_TEMPLATE_STORAGE_KEY));
    if (storedTemplate) {
        activeLLMTemplate = storedTemplate;
        updateLLMTemplateButtonStates();
        updateLLMTemplateFieldLocks();
        return storedTemplate;
    }

    const inferredTemplate = inferLLMTemplateFromCurrentFields();
    if (inferredTemplate) {
        setSelectedLLMTemplateName(inferredTemplate);
        persistCurrentLLMTemplateState();
        return inferredTemplate;
    }

    setSelectedLLMTemplateName(DEFAULT_LLM_TEMPLATE_NAME);
    if (hasAnyLLMConnectionFieldValue()) {
        persistCurrentLLMTemplateState();
    }
    return DEFAULT_LLM_TEMPLATE_NAME;
}

function getLLMTemplateKeyStorageKey(templateName) {
    return templateName ? `${LLM_TEMPLATE_KEY_STORAGE_PREFIX}${templateName}` : null;
}

function getLLMTemplateBaseUrlStorageKey(templateName) {
    return templateName ? `${LLM_TEMPLATE_BASEURL_STORAGE_PREFIX}${templateName}` : null;
}

function getLLMTemplateModelStorageKey(templateName) {
    return templateName ? `${LLM_TEMPLATE_MODEL_STORAGE_PREFIX}${templateName}` : null;
}

function getLLMTemplateExtraBodyStorageKey(templateName) {
    return templateName ? `${LLM_TEMPLATE_EXTRABODY_STORAGE_PREFIX}${templateName}` : null;
}

function getLLMTemplateParallelStorageKey(templateName) {
    return templateName ? `${LLM_TEMPLATE_PARALLEL_STORAGE_PREFIX}${templateName}` : null;
}

function getCurrentLLMTemplateName() {
    return getSelectedLLMTemplateName();
}

function getStoredLLMTemplateKey(templateName) {
    const storageKey = getLLMTemplateKeyStorageKey(templateName);
    if (!storageKey) return '';
    return localStorage.getItem(storageKey) || '';
}

function setStoredLLMTemplateKey(templateName, value) {
    const storageKey = getLLMTemplateKeyStorageKey(templateName);
    if (!storageKey) return;

    const normalized = (value || '').trim();
    if (normalized) {
        localStorage.setItem(storageKey, normalized);
    } else {
        localStorage.removeItem(storageKey);
    }
}

function getStoredLLMTemplateBaseUrl(templateName) {
    const storageKey = getLLMTemplateBaseUrlStorageKey(templateName);
    if (!storageKey) return '';
    return localStorage.getItem(storageKey) || '';
}

function setStoredLLMTemplateBaseUrl(templateName, value) {
    const storageKey = getLLMTemplateBaseUrlStorageKey(templateName);
    if (!storageKey) return;

    const normalized = (value || '').trim();
    if (normalized) {
        localStorage.setItem(storageKey, normalized);
    } else {
        localStorage.removeItem(storageKey);
    }
}

function getStoredLLMTemplateModel(templateName) {
    const storageKey = getLLMTemplateModelStorageKey(templateName);
    if (!storageKey) return '';
    return localStorage.getItem(storageKey) || '';
}

function setStoredLLMTemplateModel(templateName, value) {
    const storageKey = getLLMTemplateModelStorageKey(templateName);
    if (!storageKey) return;

    const normalized = (value || '').trim();
    if (normalized) {
        localStorage.setItem(storageKey, normalized);
    } else {
        localStorage.removeItem(storageKey);
    }
}

/** @returns {string | null} 有记录则为字符串（可为 "" 表示用户明确留空）；从未保存过则为 null */
function getStoredLLMTemplateExtraBody(templateName) {
    const storageKey = getLLMTemplateExtraBodyStorageKey(templateName);
    if (!storageKey) return null;
    return localStorage.getItem(storageKey);
}

function setStoredLLMTemplateExtraBody(templateName, value) {
    const storageKey = getLLMTemplateExtraBodyStorageKey(templateName);
    if (!storageKey) return;
    const normalized = value == null ? '' : String(value).trim();
    localStorage.setItem(storageKey, normalized);
}

function getStoredLLMTemplateParallelMode(templateName) {
    const storageKey = getLLMTemplateParallelStorageKey(templateName);
    if (!storageKey) return '';
    const value = localStorage.getItem(storageKey) || '';
    return LLM_PARALLEL_FASTEST_MODES.includes(value) ? value : '';
}

function setStoredLLMTemplateParallelMode(templateName, value) {
    const storageKey = getLLMTemplateParallelStorageKey(templateName);
    if (!storageKey) return;

    const normalized = LLM_PARALLEL_FASTEST_MODES.includes(value) ? value : 'off';
    localStorage.setItem(storageKey, normalized);
}

function persistCurrentLLMTemplateBaseUrl() {
    persistLLMTemplateBaseUrl(getCurrentLLMTemplateName());
}

function persistLLMTemplateBaseUrl(templateName) {
    const baseUrlInput = document.getElementById('llm-base-url');
    if (!templateName || !baseUrlInput || !isCustomLLMTemplate(templateName)) return;

    setStoredLLMTemplateBaseUrl(templateName, baseUrlInput.value);
}

function persistCurrentLLMTemplateKey() {
    persistLLMTemplateKey(getCurrentLLMTemplateName());
}

function persistLLMTemplateKey(templateName) {
    const keyInput = document.getElementById('llm-api-key');
    if (!templateName || !keyInput) return;

    setStoredLLMTemplateKey(templateName, keyInput.value);
}

function persistCurrentLLMTemplateModel() {
    persistLLMTemplateModel(getCurrentLLMTemplateName());
}

function persistLLMTemplateModel(templateName) {
    const modelInput = document.getElementById('llm-model');
    if (!templateName || !modelInput) return;

    if (templateName === 'openrouter' || isCustomLLMTemplate(templateName)) {
        setStoredLLMTemplateModel(templateName, modelInput.value);
    }
}

function persistCurrentLLMTemplateExtraBody() {
    persistLLMTemplateExtraBody(getCurrentLLMTemplateName());
}

function persistLLMTemplateExtraBody(templateName) {
    const extraInput = document.getElementById('openai-compat-extra-body-json');
    if (!templateName || !extraInput) return;

    setStoredLLMTemplateExtraBody(templateName, extraInput.value);
}

function persistCurrentLLMTemplateParallelMode() {
    persistLLMTemplateParallelMode(getCurrentLLMTemplateName());
}

function persistLLMTemplateParallelMode(templateName) {
    if (!templateName || !isCustomLLMTemplate(templateName)) return;

    setStoredLLMTemplateParallelMode(templateName, getLLMParallelFastestModeSelect());
}

function persistCurrentLLMTemplateState() {
    persistLLMTemplateState(getCurrentLLMTemplateName());
}

function persistLLMTemplateState(templateName) {
    persistLLMTemplateBaseUrl(templateName);
    persistLLMTemplateKey(templateName);
    persistLLMTemplateModel(templateName);
    persistLLMTemplateExtraBody(templateName);
    persistLLMTemplateParallelMode(templateName);
}

function syncSimpleModeLLMTemplateFields() {
    const baseUrlInput = document.getElementById('llm-base-url');
    const modelInput = document.getElementById('llm-model');
    const keyInput = document.getElementById('llm-api-key');
    const extraInput = document.getElementById('openai-compat-extra-body-json');

    // 简易模式固定用可编辑的 custom1，保留用户已填的地址/模型/Key
    const baseUrl = (baseUrlInput?.value || '').trim();
    const model = (modelInput?.value || '').trim();
    const apiKey = (keyInput?.value || '').trim();
    const extraBodyJson = (extraInput?.value || '').trim();

    setSelectedLLMTemplateName(SIMPLE_MODE_LLM_TEMPLATE_NAME);
    if (baseUrlInput) baseUrlInput.value = baseUrl;
    if (modelInput) modelInput.value = model;
    if (keyInput) keyInput.value = apiKey;
    if (extraInput) extraInput.value = extraBodyJson;
    updateLLMTemplateFieldLocks();

    return {
        template: SIMPLE_MODE_LLM_TEMPLATE_NAME,
        baseUrl,
        model,
        extraBodyJson,
        apiKey,
    };
}

function getEffectiveLLMApiKeyForCurrentMode() {
    return (document.getElementById('llm-api-key')?.value || '').trim();
}

// 重置时按模板类型保留用户数据，其余参数回退到 LLM_TEMPLATE_CONFIGS 中的默认值：
//   - custom1~3：完整保留（不清除任何参数）
//   - openrouter：保留 API Key 和模型名，清除其余参数
//   - 其他预设：仅保留 API Key，清除其余参数
// 注：各模板的 API Key 存储在独立的键名下，此处不会被清除，因此始终保留。
function resetStoredLLMTemplateParametersToDefaults() {
    Object.entries(LLM_TEMPLATE_CONFIGS).forEach(([templateName, templateConfig]) => {
        if (templateConfig.isCustom) {
            return;
        }

        const storageKeys = [
            getLLMTemplateBaseUrlStorageKey(templateName),
            getLLMTemplateExtraBodyStorageKey(templateName),
            getLLMTemplateParallelStorageKey(templateName),
        ];
        // openrouter 额外保留模型名；其余预设连模型一起清除
        if (templateName !== 'openrouter') {
            storageKeys.push(getLLMTemplateModelStorageKey(templateName));
        }

        storageKeys.forEach((storageKey) => {
            if (storageKey) {
                localStorage.removeItem(storageKey);
            }
        });
    });
}

/** 若当前 Base URL 命中某一 LLM 预设且该预设下曾保存过 extra_body，则覆盖输入框（优先于配置里的全局值） */
function applyStoredExtraBodyForActiveLLMTemplate() {
    const templateName = getCurrentLLMTemplateName();
    const extraBodyInput = document.getElementById('openai-compat-extra-body-json');
    if (!templateName || !extraBodyInput) return;

    const storedExtra = getStoredLLMTemplateExtraBody(templateName);
    if (storedExtra !== null) {
        extraBodyInput.value = storedExtra;
    }
}

function resolveLLMTemplateKeySource(templateName) {
    const t = window.i18n ? window.i18n.t : (key) => key;
    const useInternationalEndpoint = document.getElementById('use-international-endpoint')?.checked ?? false;
    const templateConfig = LLM_TEMPLATE_CONFIGS[templateName];
    if (!templateConfig) return null;
    if (templateConfig.isCustom) return null;

    let url = '';
    if (templateName === 'dashscope-qwen35-flash' || templateName === 'dashscope-qwen35-plus') {
        url = useInternationalEndpoint
            ? 'https://modelstudio.console.aliyun.com/ap-southeast-1?tab=doc#/api-key'
            : 'https://bailian.console.aliyun.com/cn-beijing/?tab=model#/api-key';
    } else if (templateName === 'openrouter') {
        url = 'https://openrouter.ai/settings/keys';
    } else if (templateName === SIMPLE_MODE_LLM_TEMPLATE_NAME) {
        url = 'https://platform.deepseek.com/api_keys';
    } else if (templateName === 'mercury2') {
        url = 'https://platform.inceptionlabs.ai/dashboard/api-keys';
    }

    if (!url) return null;

    return {
        providerName: t(templateConfig.providerLabelKey),
        url,
    };
}

function updateLLMTemplateKeySourceHint(templateName = activeLLMTemplate) {
    const container = document.getElementById('llm-template-key-source-hint');
    const label = document.getElementById('llm-template-key-source-label');
    const link = document.getElementById('llm-template-key-source-link');
    const t = window.i18n ? window.i18n.t : (key) => key;

    if (!container || !label || !link) return;

    const source = resolveLLMTemplateKeySource(templateName);
    if (!source) {
        container.style.display = 'none';
        label.textContent = '';
        link.textContent = '';
        link.removeAttribute('href');
        activeLLMTemplate = templateName || null;
        return;
    }

    activeLLMTemplate = templateName;
    label.textContent = t('hint.llmTemplateKeySource', { provider: source.providerName });
    link.href = source.url;
    link.textContent = source.url;
    link.title = source.url;
    container.style.display = 'block';
}

function syncLLMTemplateKeySourceHintFromInputs() {
    updateLLMTemplateKeySourceHint(getCurrentLLMTemplateName());
    updateLLMTemplateButtonStates();
    updateLLMTemplateFieldLocks();
}

/** 翻译仅保留大模型模式：非 openrouter* 的旧配置统一迁移。 */
function normalizeTranslationApiTypeToLlm(apiType) {
    if (apiType === 'openrouter_streaming') return 'openrouter';
    if (apiType === 'openrouter' || apiType === 'openrouter_streaming_deepl_hybrid') return apiType;
    return 'openrouter';
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
    const simpleLLMFields = false
        ? syncSimpleModeLLMTemplateFields()
        : null;
    const base = simpleLLMFields?.baseUrl || (document.getElementById('llm-base-url')?.value || '').trim();
    const model = simpleLLMFields?.model || (document.getElementById('llm-model')?.value || '').trim();
    const llmKey = simpleLLMFields?.apiKey || (document.getElementById('llm-api-key')?.value || '').trim();
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
    return apiType === 'openrouter_streaming_deepl_hybrid'
        || (apiType === 'openrouter' && !!streamingMode?.checked);
}

function updateSensitiveWordsHint(apiType = null) {
    const actualApiType = apiType || (document.getElementById('translation-api-type')
        ? document.getElementById('translation-api-type').value
        : 'openrouter');
    const hint = document.getElementById('qwen-mt-sensitive-words-hint');
    if (!hint) return;

    hint.style.display = actualApiType === 'qwen_mt' ? 'block' : 'none';
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
        const isAdvancedMode = !false;
        if (need && !isAdvancedMode) {
            badge.hidden = false;
            badge.setAttribute('data-i18n', 'label.required');
            badge.textContent = window.i18n ? window.i18n.t('label.required') : '*必需';
        } else {
            badge.hidden = true;
            badge.textContent = '';
            badge.removeAttribute('data-i18n');
        }
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

// 把指定模板的已存/默认参数填入表单（不会持久化当前表单，供重置后刷新界面使用）
function populateLLMTemplateForm(templateName) {
    const isSimpleMode = false;
    const targetTemplate = isSimpleMode ? SIMPLE_MODE_LLM_TEMPLATE_NAME : templateName;

    const baseUrlInput = document.getElementById('llm-base-url');
    const modelInput = document.getElementById('llm-model');
    const keyInput = document.getElementById('llm-api-key');
    const extraBodyInput = document.getElementById('openai-compat-extra-body-json');
    const parallelFastestSelect = document.getElementById('llm-parallel-fastest-mode');
    const dashscopeKeyInput = document.getElementById('dashscope-api-key');
    const t = window.i18n ? window.i18n.t : (key) => key;
    const templateConfig = LLM_TEMPLATE_CONFIGS[targetTemplate];

    if (!baseUrlInput || !modelInput || !keyInput || !extraBodyInput || !parallelFastestSelect || !templateConfig) {
        return false;
    }

    setSelectedLLMTemplateName(targetTemplate);

    if (templateConfig.isCustom) {
        baseUrlInput.value = getStoredLLMTemplateBaseUrl(targetTemplate);
        modelInput.value = getStoredLLMTemplateModel(targetTemplate);
    } else {
        baseUrlInput.value = templateConfig.baseUrl;
    }
    if (!templateConfig.isCustom && Object.prototype.hasOwnProperty.call(templateConfig, 'model')) {
        modelInput.value = templateConfig.model;
    } else if (targetTemplate === 'openrouter') {
        modelInput.value = getStoredLLMTemplateModel(targetTemplate);
    }
    const storedExtra = getStoredLLMTemplateExtraBody(targetTemplate);
    if (storedExtra !== null) {
        extraBodyInput.value = storedExtra;
    } else if (Object.prototype.hasOwnProperty.call(templateConfig, 'extraBody')) {
        extraBodyInput.value = templateConfig.extraBody;
    } else {
        extraBodyInput.value = '';
    }
    if (templateConfig.isCustom) {
        parallelFastestSelect.value = getStoredLLMTemplateParallelMode(targetTemplate) || 'off';
    } else {
        parallelFastestSelect.value = templateConfig.parallelFastestMode || 'off';
    }

    const storedKey = getStoredLLMTemplateKey(targetTemplate);
    if (storedKey) {
        keyInput.value = storedKey;
        persistSecretInputValue('llm-api-key');
        runtimeCredentialStatus.llm.api_key_set = true;
    } else if (templateConfig.isCustom) {
        keyInput.value = '';
        persistSecretInputValue('llm-api-key');
        runtimeCredentialStatus.llm.api_key_set = false;
    } else if (templateConfig.copyDashscopeKey) {
        const dashscopeKey = dashscopeKeyInput ? dashscopeKeyInput.value.trim() : '';
        if (dashscopeKey) {
            keyInput.value = dashscopeKey;
            setStoredLLMTemplateKey(targetTemplate, dashscopeKey);
            persistSecretInputValue('llm-api-key');
            runtimeCredentialStatus.llm.api_key_set = true;
            showMessage('✅ ' + t('msg.llmTemplateDashscopeCopied'), 'success');
        } else {
            keyInput.value = '';
            persistSecretInputValue('llm-api-key');
            runtimeCredentialStatus.llm.api_key_set = false;
            showMessage('⚠️ ' + t('msg.llmTemplateDashscopeKeyMissing'), 'warning');
        }
    } else {
        keyInput.value = '';
        persistSecretInputValue('llm-api-key');
        runtimeCredentialStatus.llm.api_key_set = false;
    }

    updateLLMTemplateKeySourceHint(targetTemplate);
    return true;
}

function applyLLMTemplate(templateName) {
    const previousTemplateName = normalizeLLMTemplateName(activeLLMTemplate) || getCurrentLLMTemplateName();
    const baseUrlInput = document.getElementById('llm-base-url');

    if (previousTemplateName) {
        persistLLMTemplateState(previousTemplateName);
    }

    if (!populateLLMTemplateForm(templateName)) {
        return;
    }

    onSettingChange(baseUrlInput);
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
    initializeCollapsibleStates();
    applyStoredExtraBodyForActiveLLMTemplate();
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
    updateLLMTemplateKeySourceHint();
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
 * Windows/PortAudio 下 get_default_input_device_info 的设备名常被截断（约 32 字符），
 * 而枚举列表里的名称是完整的；且默认设备 index 可能因 Host API 过滤与列表不一致。
 * 用列表项解析「系统默认」应展示的完整名称。
 */
function resolveDefaultMicDisplayName(devices, defaultIndex, defaultNameApi) {
    const api = (defaultNameApi || '').trim();
    const fromIndex = devices.find((ad) => ad.index === defaultIndex);
    if (fromIndex && fromIndex.name) {
        return String(fromIndex.name).trim();
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

// 刷新后端输入设备列表（PyAudio）
async function refreshMicDevices(preserveSelection = true) {
    const micSelect = document.getElementById('mic-device');
    if (!micSelect) return;

    const previousValue = preserveSelection ? micSelect.value : '';

    try {
        const response = await fetch(`${API_BASE}/audio/input-devices`);
        const data = await response.json();
        const devices = Array.isArray(data.devices) ? data.devices : [];

        // 重建选项
        micSelect.innerHTML = '';
        const defaultOpt = document.createElement('option');
        defaultOpt.value = '';
        const t = window.i18n ? window.i18n.t : (key) => key;
        const defaultIndex = data.default_index;
        const defaultNameApi = data.default_name ? String(data.default_name).trim() : '';
        const displayDefaultName = resolveDefaultMicDisplayName(devices, defaultIndex, defaultNameApi);
        defaultOpt.textContent = displayDefaultName
            ? (window.i18n
                ? window.i18n.t('option.systemDefaultWithDevice', { name: displayDefaultName })
                : `系统默认（${displayDefaultName}）`)
            : (window.i18n ? window.i18n.t('option.systemDefault') : '系统默认');
        micSelect.appendChild(defaultOpt);

        const indices = new Set();
        devices.forEach((d) => {
            const idx = d.index;
            if (idx === undefined || idx === null) return;
            const idxStr = String(idx);
            indices.add(idxStr);

            const opt = document.createElement('option');
            opt.value = idxStr;
            const name = d.name ? String(d.name) : `${t('label.device')} ${idxStr}`;
            opt.textContent = `${name} (#${idxStr})`;
            micSelect.appendChild(opt);
        });

        // 优先保留当前选择；否则使用后端当前配置；最后回退默认
        const serverSelected = (data.selected_index === undefined || data.selected_index === null) ? '' : String(data.selected_index);
        if (previousValue && indices.has(previousValue)) {
            micSelect.value = previousValue;
        } else if (serverSelected && indices.has(serverSelected)) {
            micSelect.value = serverSelected;
        } else {
            micSelect.value = '';
        }

        const identity = `${defaultIndex ?? 'none'}|${displayDefaultName}`;
        const prevIdentity = lastMicDefaultIdentity;
        lastMicDefaultIdentity = identity;
        if (prevIdentity !== null && identity !== prevIdentity && micSelect.value === '') {
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

// 从localStorage加载API Keys
function loadAPIKeys() {
    const dashscopeKey = localStorage.getItem('dashscope_api_key');
    const deeplKey = localStorage.getItem('deepl_api_key');
    const llmKey = localStorage.getItem('llm_api_key');
    const doubaoKey = localStorage.getItem('doubao_api_key');
    const useInternational = localStorage.getItem('use_international_endpoint') === 'true';

    if (dashscopeKey) document.getElementById('dashscope-api-key').value = dashscopeKey;
    if (deeplKey) document.getElementById('deepl-api-key').value = deeplKey;
    if (doubaoKey) document.getElementById('doubao-api-key').value = doubaoKey;

    const sonioxKey = localStorage.getItem('soniox_api_key');
    if (sonioxKey) document.getElementById('soniox-api-key').value = sonioxKey;

    const screenOcrKey = localStorage.getItem('screen_ocr_api_key');
    if (screenOcrKey) document.getElementById('screen-ocr-api-key').value = screenOcrKey;

    const activeTemplate = ensureSelectedLLMTemplate();
    const storedTemplateKey = getStoredLLMTemplateKey(activeTemplate);
    if (storedTemplateKey) {
        document.getElementById('llm-api-key').value = storedTemplateKey;
    } else if (llmKey && !false) {
        document.getElementById('llm-api-key').value = llmKey;
        persistCurrentLLMTemplateKey();
    } else if (false) {
        document.getElementById('llm-api-key').value = '';
    }

    if (activeTemplate === 'openrouter') {
        const storedOpenRouterModel = getStoredLLMTemplateModel(activeTemplate);
        if (storedOpenRouterModel) {
            document.getElementById('llm-model').value = storedOpenRouterModel;
        }
    }

    document.getElementById('use-international-endpoint').checked = useInternational;

    // 应用国际版设置对 ASR 选项的影响
    updateAsrOptionsForInternational(useInternational);

    // 如果已有DashScope API Key，自动折叠API Keys区域
    if (dashscopeKey) {
        const apiKeysSection = document.getElementById('api-keys');
        const apiKeysIcon = document.getElementById('api-keys-icon');
        if (apiKeysSection && !apiKeysSection.classList.contains('collapsed')) {
            apiKeysSection.classList.add('collapsed');
            updateCollapsibleIcon(apiKeysIcon, true);
            syncCollapsibleContainerState(apiKeysSection);
            syncCollapsibleAccessibility(apiKeysSection);
        }
    }

    // 添加API Key change事件监听
    document.getElementById('dashscope-api-key').addEventListener('input', saveAPIKey);
    document.getElementById('deepl-api-key').addEventListener('input', saveAPIKey);
    document.getElementById('llm-api-key').addEventListener('input', saveAPIKey);
    document.getElementById('doubao-api-key').addEventListener('input', saveAPIKey);
    document.getElementById('soniox-api-key').addEventListener('input', saveAPIKey);
    document.getElementById('screen-ocr-api-key').addEventListener('input', saveAPIKey);

    ['llm-base-url', 'llm-model', 'openai-compat-extra-body-json'].forEach((inputId) => {
        const input = document.getElementById(inputId);
        if (!input) return;
        input.addEventListener('input', (event) => {
            if (event.target.id === 'llm-base-url') {
                persistCurrentLLMTemplateBaseUrl();
            }
            if (event.target.id === 'llm-model') {
                persistCurrentLLMTemplateModel();
            }
            if (event.target.id === 'openai-compat-extra-body-json') {
                persistCurrentLLMTemplateExtraBody();
            }
            syncLLMTemplateKeySourceHintFromInputs();
            onSettingChange(event.target);
        });
    });

    const llmBaseUrlInput = document.getElementById('llm-base-url');
    if (llmBaseUrlInput && llmBaseUrlInput.dataset.llmExtrabodySyncBound !== 'true') {
        llmBaseUrlInput.dataset.llmExtrabodySyncBound = 'true';
        llmBaseUrlInput.addEventListener('blur', () => {
            applyStoredExtraBodyForActiveLLMTemplate();
        });
    }

    const extraBodyEl = document.getElementById('openai-compat-extra-body-json');
    if (extraBodyEl && extraBodyEl.dataset.llmExtrabodyBlurPersistBound !== 'true') {
        extraBodyEl.dataset.llmExtrabodyBlurPersistBound = 'true';
        extraBodyEl.addEventListener('blur', () => {
            persistCurrentLLMTemplateExtraBody();
        });
    }

    const parallelModeEl = document.getElementById('llm-parallel-fastest-mode');
    if (parallelModeEl && parallelModeEl.dataset.llmParallelPersistBound !== 'true') {
        parallelModeEl.dataset.llmParallelPersistBound = 'true';
        parallelModeEl.addEventListener('change', () => {
            persistCurrentLLMTemplateParallelMode();
            syncLLMTemplateKeySourceHintFromInputs();
            onSettingChange(parallelModeEl);
        });
    }
}

// 处理国际版端点开关变化
function handleInternationalEndpointChange(event) {
    const useInternational = event.target.checked;

    // 保存到 localStorage
    localStorage.setItem('use_international_endpoint', useInternational.toString());

    // 更新 ASR 选项
    updateAsrOptionsForInternational(useInternational);
    updateLLMTemplateKeySourceHint();

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

    const isDoubaoFile = asrBackendSelect.value === 'doubao_file';
    const apiTypeVal = translationApiSelect.value;
    const isStreamingTranslation = enableTranslationToggle.checked
        && (
            (apiTypeVal === 'openrouter' && streamingModeToggle.checked)
            || apiTypeVal === 'openrouter_streaming_deepl_hybrid'
        );

    // 固定开启：VRChat 麦克风没开时不跑自我识别（后端也固定开启）
    micControlToggle.checked = true;
    micControlToggle.disabled = true;

    if (isDoubaoFile || isStreamingTranslation) {
        partialResultsToggle.checked = false;
        partialResultsToggle.disabled = true;
    } else {
        partialResultsToggle.disabled = false;
    }
}

// 保存API Key到localStorage
function saveAPIKey(event) {
    persistSecretInputValue(event.target.id);

    if (event.target.id === 'llm-api-key') {
        persistCurrentLLMTemplateKey();
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
                // 处理 LLM 流式模式的特殊情况
                const apiType = normalizeTranslationApiTypeToLlm(config.translation.api_type || 'openrouter_streaming');
                if (apiType === 'openrouter') {
                    document.getElementById('translation-api-type').value = 'openrouter';
                    document.getElementById('openrouter-streaming-mode').checked =
                        (config.translation.api_type || 'openrouter_streaming') === 'openrouter_streaming'
                        || config.translation.api_type === 'openrouter';
                    document.getElementById('openrouter-streaming-mode').disabled = false;
                } else {
                    document.getElementById('translation-api-type').value = 'openrouter';
                    document.getElementById('openrouter-streaming-mode').checked = true;
                    document.getElementById('openrouter-streaming-mode').disabled = false;
                }
                if (false) {
                    // 简易模式允许用户自定义 LLM 地址/模型，按已保存值回填
                    document.getElementById('llm-base-url').value = config.translation.llm_base_url || '';
                    document.getElementById('llm-model').value = config.translation.llm_model || '';
                    document.getElementById('openai-compat-extra-body-json').value = config.translation.openai_compat_extra_body_json || '';
                    setSelectedLLMTemplateName(SIMPLE_MODE_LLM_TEMPLATE_NAME);
                } else {
                    document.getElementById('llm-base-url').value = config.translation.llm_base_url || '';
                    document.getElementById('llm-model').value = config.translation.llm_model || '';
                    document.getElementById('openai-compat-extra-body-json').value = config.translation.openai_compat_extra_body_json || '';
                }
                document.getElementById('llm-translation-formality').value =
                    sanitizeLLMTranslationFormality(config.translation.llm_translation_formality);
                document.getElementById('llm-translation-style').value =
                    sanitizeLLMTranslationStyle(config.translation.llm_translation_style);
                setLLMParallelFastestModeSelect(
                    resolveLLMParallelFastestModeFromStoredTranslation(config.translation)
                );
                ensureSelectedLLMTemplate(config.translation);
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
                if (micSelect) {
                    const idx = config.mic_control.mic_device_index;
                    micSelect.value = (idx === undefined || idx === null) ? '' : String(idx);
                }
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
        updateSensitiveWordsHint();
        applyAsrBackendLocks();
        updateOscCompatModeUi();
        ensureSelectedLLMTemplate();
        syncLLMTemplateKeySourceHintFromInputs();
        syncAllLanguageComboClearButtons();
        updateDashscopeKeyFieldState();
        applyStoredExtraBodyForActiveLLMTemplate();
        updateLocalAsrUiVisibility();

    } catch (error) {
        console.error('加载本地配置失败:', error);
        // 出错时使用前端默认值
        loadDefaultConfig();
        toggleTranslationOptions();
        updateFuriganaVisibility();
        updateLLMSettingsVisibility();
        updateSensitiveWordsHint();
        applyAsrBackendLocks();
        updateOscCompatModeUi();
        ensureSelectedLLMTemplate();
        syncLLMTemplateKeySourceHintFromInputs();
        syncAllLanguageComboClearButtons();
        updateDashscopeKeyFieldState();
        applyStoredExtraBodyForActiveLLMTemplate();
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
    setSelectedLLMTemplateName(SIMPLE_MODE_LLM_TEMPLATE_NAME);

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
    ensureSelectedLLMTemplate({ llm_template: DEFAULT_LLM_TEMPLATE_NAME });
    updateOpenRouterStreamingUi();
    updateFuriganaVisibility();
    updateLLMSettingsVisibility();
    updateSensitiveWordsHint();
    applyAsrBackendLocks();
    updateOscCompatModeUi();
    syncLLMTemplateKeySourceHintFromInputs();
    syncAllLanguageComboClearButtons();
    updateDashscopeKeyFieldState();
    updateLocalAsrUiVisibility();
}

function refreshUiAfterServerConfigApply() {
    updateOpenRouterStreamingUi();
    toggleTranslationOptions();
    updateFuriganaVisibility();
    updateLLMSettingsVisibility();
    updateSensitiveWordsHint();
    applyAsrBackendLocks();
    updateOscCompatModeUi();
    syncLLMTemplateKeySourceHintFromInputs();
    syncAllLanguageComboClearButtons();
    updateDashscopeKeyFieldState();
    applyStoredExtraBodyForActiveLLMTemplate();
    updateLocalAsrUiVisibility();
    renderLanguageComboMenus();
}

/** 将 GET /api/config 的 JSON 填回表单（不写入 localStorage、不改 touch） */
function applyServerConfigPayload(config) {
    if (config.features) {
        featureFlags = config.features;
    }
    updateLocalAsrUiVisibility();

    document.getElementById('enable-translation').checked = config.translation.enable_translation;
    document.getElementById('target-language').value = config.translation.target_language;
    document.getElementById('secondary-target-language').value = config.translation.secondary_target_language || '';
    document.getElementById('fallback-language').value = config.translation.fallback_language || '';
    const serverApiType = config.translation.api_type;
    if (serverApiType === 'openrouter_streaming' || !serverApiType || normalizeTranslationApiTypeToLlm(serverApiType) === 'openrouter') {
        document.getElementById('translation-api-type').value = 'openrouter';
        document.getElementById('openrouter-streaming-mode').checked =
            serverApiType === 'openrouter_streaming' || serverApiType === 'openrouter' || !serverApiType;
        document.getElementById('openrouter-streaming-mode').disabled = false;
    } else {
        document.getElementById('translation-api-type').value = 'openrouter';
        document.getElementById('openrouter-streaming-mode').checked = true;
        document.getElementById('openrouter-streaming-mode').disabled = false;
    }
    if (false) {
        document.getElementById('llm-base-url').value = config.translation.llm_base_url || '';
        document.getElementById('llm-model').value = config.translation.llm_model || '';
        document.getElementById('openai-compat-extra-body-json').value = config.translation.openai_compat_extra_body_json || '';
        setSelectedLLMTemplateName(SIMPLE_MODE_LLM_TEMPLATE_NAME);
    } else {
        document.getElementById('llm-base-url').value = config.translation.llm_base_url || '';
        document.getElementById('llm-model').value = config.translation.llm_model || '';
        document.getElementById('openai-compat-extra-body-json').value = config.translation.openai_compat_extra_body_json || '';
    }
    document.getElementById('llm-translation-formality').value =
        sanitizeLLMTranslationFormality(config.translation.llm_translation_formality);
    document.getElementById('llm-translation-style').value =
        sanitizeLLMTranslationStyle(config.translation.llm_translation_style);
    setLLMParallelFastestModeSelect(
        resolveLLMParallelFastestModeFromStoredTranslation(config.translation),
    );
    ensureSelectedLLMTemplate(config.translation);
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
        const idx = config.mic_control.mic_device_index;
        micSelect.value = (idx === undefined || idx === null) ? '' : String(idx);
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

        // 确定实际的 API 类型（LLM + 流式开关 -> openrouter_streaming；混合模式保持独立值）
        let actualApiType = document.getElementById('translation-api-type').value;
        if (actualApiType === 'openrouter_streaming_deepl_hybrid') {
            // 保持 hybrid
        } else if (actualApiType === 'openrouter' && document.getElementById('openrouter-streaming-mode').checked) {
            actualApiType = 'openrouter_streaming';
        }

        const isSimpleMode = false;
        const simpleLLMFields = isSimpleMode ? syncSimpleModeLLMTemplateFields() : null;
        const llmTemplate = isSimpleMode ? simpleLLMFields.template : getSelectedLLMTemplateName();
        const llmBaseUrl = isSimpleMode ? simpleLLMFields.baseUrl : document.getElementById('llm-base-url').value.trim();
        const llmModel = isSimpleMode ? simpleLLMFields.model : document.getElementById('llm-model').value.trim();
        const openaiExtraBodyJson = isSimpleMode
            ? simpleLLMFields.extraBodyJson
            : document.getElementById('openai-compat-extra-body-json').value.trim();

        const config = {
            translation: {
                enable_translation: document.getElementById('enable-translation').checked,
                target_language: document.getElementById('target-language').value,
                secondary_target_language: document.getElementById('secondary-target-language').value || null,
                fallback_language: document.getElementById('fallback-language').value || null,
                api_type: actualApiType,
                llm_template: llmTemplate,
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
                mic_device_index: (() => {
                    const v = document.getElementById('mic-device') ? document.getElementById('mic-device').value : '';
                    return v === '' ? null : parseInt(v);
                })(),
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
                enabled: document.getElementById('others-translation-enabled')?.checked === true,
                asr_backend: sanitizeOthersAsrBackendValue(
                    document.getElementById('others-asr-backend')?.value || ''
                ),
                target_language: (document.getElementById('others-target-language')?.value || '').trim() || 'zh-CN',
            },
            screen_translation: getScreenTranslationConfigFromForm(),
            display: { mode: currentDisplayMode },
            local_asr: isLocalAsrUiEnabled() ? getLocalAsrConfigFromForm() : null,
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
    const expandLlmPanel = (newApi === 'openrouter' || newApi === 'openrouter_streaming_deepl_hybrid')
        && !isLLMConnectionFieldsComplete();
    updateLLMSettingsVisibility(newApi, expandLlmPanel);
    updateSensitiveWordsHint(newApi);
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
        updateSensitiveWordsHint(apiSelect.value);
        updateLLMStreamingPromoState();
        syncLLMTemplateKeySourceHintFromInputs();
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

        // 确定实际的 API 类型（LLM + 流式开关 -> openrouter_streaming；混合模式保持独立值）
        let actualApiType = document.getElementById('translation-api-type').value;
        if (actualApiType === 'openrouter_streaming_deepl_hybrid') {
            // 保持 hybrid
        } else if (actualApiType === 'openrouter' && document.getElementById('openrouter-streaming-mode').checked) {
            actualApiType = 'openrouter_streaming';
        }

        const isSimpleMode = false;
        const simpleLLMFields = isSimpleMode ? syncSimpleModeLLMTemplateFields() : null;
        const llmTemplate = isSimpleMode ? simpleLLMFields.template : getSelectedLLMTemplateName();
        const llmBaseUrl = isSimpleMode ? simpleLLMFields.baseUrl : document.getElementById('llm-base-url').value.trim();
        const llmModel = isSimpleMode ? simpleLLMFields.model : document.getElementById('llm-model').value.trim();
        const openaiExtraBodyJson = isSimpleMode
            ? simpleLLMFields.extraBodyJson
            : document.getElementById('openai-compat-extra-body-json').value.trim();

        const config = {
            translation: {
                enable_translation: document.getElementById('enable-translation').checked,
                target_language: document.getElementById('target-language').value,
                secondary_target_language: document.getElementById('secondary-target-language').value || null,
                fallback_language: document.getElementById('fallback-language').value || null,
                api_type: actualApiType,
                llm_template: llmTemplate,
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
                mic_device_index: (() => {
                    const v = document.getElementById('mic-device') ? document.getElementById('mic-device').value : '';
                    return v === '' ? null : parseInt(v);
                })(),
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
            smart_target_language: {
                primary_enabled: document.getElementById('smart-target-primary-enabled')?.checked ?? true,
                secondary_enabled: document.getElementById('smart-target-secondary-enabled')?.checked ?? false,
                strategy: document.getElementById('smart-target-strategy')?.value || 'auto',
                window_size: parseInt(document.getElementById('smart-target-window-size')?.value || '5'),
                exclude_self_language: document.getElementById('smart-target-exclude-self')?.checked ?? true,
                min_samples: parseInt(document.getElementById('smart-target-min-samples')?.value || '3'),
            },
            others_translation: {
                enabled: document.getElementById('others-translation-enabled')?.checked === true,
                asr_backend: sanitizeOthersAsrBackendValue(
                    document.getElementById('others-asr-backend')?.value || ''
                ),
                target_language: (document.getElementById('others-target-language')?.value || '').trim() || 'zh-CN',
            },
            screen_translation: getScreenTranslationConfigFromForm(),
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
                dashscope: document.getElementById('dashscope-api-key')?.value.trim() || '',
                deepl: document.getElementById('deepl-api-key')?.value.trim() || '',
                llm: getEffectiveLLMApiKeyForCurrentMode(),
                doubao: document.getElementById('doubao-api-key')?.value.trim() || '',
                soniox: document.getElementById('soniox-api-key')?.value.trim() || '',
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
            const targetLangInput = document.getElementById('target-language');
            if (targetLangInput && targetLangInput.value !== status.target_language) {
                targetLangInput.value = status.target_language;
                renderLanguageComboMenus();
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
    pendingWarningMessage = null;

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
        const dashscopeKey = document.getElementById('dashscope-api-key').value.trim();
        const doubaoKey = document.getElementById('doubao-api-key').value.trim();
        const sonioxKey = document.getElementById('soniox-api-key').value.trim();
        const deeplKey = document.getElementById('deepl-api-key').value.trim();
        const llmKey = getEffectiveLLMApiKeyForCurrentMode();
        const llmBaseUrl = document.getElementById('llm-base-url').value.trim();
        const llmModel = document.getElementById('llm-model').value.trim();
        const hasLLMKey = false
            ? !!llmKey
            : (runtimeCredentialStatus.llm.api_key_set || !!llmKey);

        const enableTranslation = document.getElementById('enable-translation').checked;
        const translationApiSelect = document.getElementById('translation-api-type');
        let translationApiType = translationApiSelect.value;

        const requiresDashscopeKey = needsAsr && currentConfigRequiresDashscopeKey();

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
            const messageKey = false
                ? 'msg.simpleDashscopeKeyMissing'
                : 'msg.dashscopeRequired';
            showMessage('❌ ' + t(messageKey), 'error');
            startBtn.disabled = false;
            startBtn.textContent = t('btn.startAllModes');

            // 展开API Keys配置区域，提示用户输入
            const apiKeysSection = document.getElementById('api-keys');
            if (apiKeysSection.classList.contains('collapsed')) {
                toggleCollapsible('api-keys');
            }

            // 高亮并震动 API Key 输入框
            highlightInput('dashscope-api-key');

            return;
        }

        if (needsAsr && asrBackend === 'doubao_file' && !doubaoKey) {
            showMessage('❌ ' + t('msg.doubaoKeyRequired'), 'error');
            startBtn.disabled = false;
            startBtn.textContent = t('btn.startAllModes');

            const apiKeysSection = document.getElementById('api-keys');
            if (apiKeysSection.classList.contains('collapsed')) {
                toggleCollapsible('api-keys');
            }
            highlightInput('doubao-api-key');

            return;
        }

        if (needsAsr && asrBackend === 'soniox' && !sonioxKey) {
            showMessage('❌ ' + t('msg.sonioxKeyRequired'), 'error');
            startBtn.disabled = false;
            startBtn.textContent = t('btn.startAllModes');
            highlightInput('soniox-api-key');

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
                highlightInput(customMissing[0]);
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
                highlightInput('dashscope-api-key');
                return;
            }
        }

        if ((needsSelf && enableTranslation) || modes.includes('others') || modes.includes('screen')) {
            if (translationApiType === 'openrouter' || translationApiType === 'openrouter_streaming_deepl_hybrid') {
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
                    const message = (
                        false
                        && missingFields[0] === 'llm-api-key'
                    )
                        ? t('msg.simpleLlmKeyMissing')
                        : t('msg.llmFieldRequired', { field: missingLabels[0] });
                    showMessage('❌ ' + message, 'error');
                    startBtn.disabled = false;
                    startBtn.textContent = t('btn.startAllModes');
                    expandLLMSettingsPanel(missingFields);
                    return;
                }
            }

            const requiresDeeplKey = translationApiType === 'deepl' && !deeplKey;
            const requiresHybridLLMKey = translationApiType === 'openrouter_streaming_deepl_hybrid' && !hasLLMKey;
            const requiresHybridDeeplKey = translationApiType === 'openrouter_streaming_deepl_hybrid' && !deeplKey;

            if (requiresDeeplKey || requiresHybridLLMKey || requiresHybridDeeplKey) {
                translationApiType = 'google_dictionary';
                translationApiSelect.value = translationApiType;
                translationApiSelect.dispatchEvent(new Event('change'));
                saveConfigToLocalStorage();
                pendingWarningMessage = t('msg.autoSwitchToGoogle');
                showMessage('⚠️ ' + pendingWarningMessage, 'warning');
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
            deepl: deeplKey,
            llm: llmKey,
            doubao: doubaoKey,
            soniox: sonioxKey,
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
            const hasPendingWarning = !!pendingWarningMessage;
            if (hasPendingWarning) {
                showMessage('⚠️ ' + pendingWarningMessage, 'warning');
            }
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
            pendingWarningMessage = null;
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
        pendingWarningMessage = null;
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
    LLM_SELECTED_TEMPLATE_STORAGE_KEY,
    QUICK_LANG_STORAGE_KEY,
    QUICK_LANG_BAR_ENABLED_KEY,
]);

function isResetManagedStorageKey(key) {
    return RESET_MANAGED_STORAGE_KEYS.has(key)
        || key.startsWith(LLM_TEMPLATE_KEY_STORAGE_PREFIX)
        || key.startsWith(LLM_TEMPLATE_BASEURL_STORAGE_PREFIX)
        || key.startsWith(LLM_TEMPLATE_MODEL_STORAGE_PREFIX)
        || key.startsWith(LLM_TEMPLATE_EXTRABODY_STORAGE_PREFIX)
        || key.startsWith(LLM_TEMPLATE_PARALLEL_STORAGE_PREFIX);
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

    if (!confirm(t('msg.confirmReset'))) {
        return;
    }

    // 把当前表单完整保存为回滚点；重置只有在后端确认保存后才提交本地状态。
    persistCurrentLLMTemplateState();
    saveConfigToLocalStorage();
    const storageSnapshot = snapshotResetManagedStorage();

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

        // 按模板类型重置参数（custom 全保留 / openrouter 保留 key+模型 / 其余仅保留 key），
        // 再用当前选中的（默认）模板刷新表单，使界面与存储一致
        resetStoredLLMTemplateParametersToDefaults();
        populateLLMTemplateForm(getSelectedLLMTemplateName());

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
            dashscope: document.getElementById('dashscope-api-key').value.trim(),
            deepl: document.getElementById('deepl-api-key').value.trim(),
            llm: getEffectiveLLMApiKeyForCurrentMode(),
            doubao: document.getElementById('doubao-api-key').value.trim(),
            soniox: document.getElementById('soniox-api-key').value.trim(),
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

async function testCustomApi() {
    const result = document.getElementById('custom-api-test-result');
    if (result) result.textContent = '测试中…';
    const cfg = getCustomApiConfigFromForm();
    try {
        const resp = await fetch('/api/custom-api/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                base_url: cfg.CUSTOM_API_BASE_URL,
                api_key: cfg.CUSTOM_API_KEY,
                model: cfg.CUSTOM_API_MODEL,
            }),
        });
        const data = await resp.json();
        if (result) {
            if (data.success) {
                result.style.color = 'var(--success)';
                result.textContent = `✓ ${data.status_code || 200} OK`;
            } else {
                result.style.color = 'var(--danger)';
                const code = data.status_code ? `HTTP ${data.status_code}` : '';
                result.textContent = `✗ ${code} ${(data.message || data.snippet || '').slice(0, 120)}`;
            }
        }
    } catch (err) {
        if (result) {
            result.style.color = 'var(--danger)';
            result.textContent = `✗ ${err.message || err}`;
        }
    }
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
    if (!confirm(`确定删除预设「${name}」？`)) return;
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
// 预设文件导出 / 导入
// --------------------------------------------------------------------------
async function exportPresetsFile() {
    const status = document.getElementById('preset-status');
    if (status) { status.style.color = 'var(--fg-muted)'; status.textContent = '导出中…'; }
    try {
        const resp = await fetch('/api/presets/export');
        const data = await resp.json();
        if (!data.success) {
            if (status) { status.style.color = 'var(--danger)'; status.textContent = `✗ ${data.message || '导出失败'}`; }
            return;
        }
        const count = data.count || 0;
        if (count === 0) {
            if (status) { status.style.color = 'var(--danger)'; status.textContent = '✗ 还没有可导出的预设'; }
            return;
        }
        const blob = new Blob([JSON.stringify(data.data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
        a.href = url;
        a.download = `ganlan-presets-${ts}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        if (status) { status.style.color = 'var(--success)'; status.textContent = `✓ 已导出 ${count} 个预设`; }
    } catch (err) {
        if (status) { status.style.color = 'var(--danger)'; status.textContent = `✗ ${err.message || err}`; }
    }
}

async function importPresetsFile(inputEl) {
    const status = document.getElementById('preset-status');
    const file = inputEl?.files?.[0];
    if (!file) return;
    if (status) { status.style.color = 'var(--fg-muted)'; status.textContent = '导入中…'; }
    try {
        const text = await file.text();
        let parsed;
        try {
            parsed = JSON.parse(text);
        } catch (e) {
            if (status) { status.style.color = 'var(--danger)'; status.textContent = '✗ 文件不是有效的 JSON'; }
            inputEl.value = '';
            return;
        }
        const resp = await fetch('/api/presets/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ data: parsed, overwrite: true }),
        });
        const data = await resp.json();
        if (data.success) {
            if (status) {
                status.style.color = 'var(--success)';
                const parts = [];
                if (data.imported) parts.push(`新增 ${data.imported} 个`);
                if (data.overwritten) parts.push(`覆盖 ${data.overwritten} 个`);
                if (data.skipped) parts.push(`跳过 ${data.skipped} 个`);
                status.textContent = `✓ 导入完成：${parts.join('，') || '无变化'}`;
            }
            await refreshPresetList();
        } else {
            if (status) { status.style.color = 'var(--danger)'; status.textContent = `✗ ${data.message || '导入失败'}`; }
        }
    } catch (err) {
        if (status) { status.style.color = 'var(--danger)'; status.textContent = `✗ ${err.message || err}`; }
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
        'enable-mic-control', 'enable-hot-words', 'enable-double-mute-clear', 'vad-enabled',
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
        'others-translation-enabled', 'others-asr-backend', 'others-target-language',
        'screen-translation-enabled', 'screen-target-language', 'screen-hotkey',
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
    'openrouter-streaming-mode': 'TRANSLATE_PARTIAL_RESULTS',
    'openai-compat-extra-body-json': 'OPENAI_COMPAT_EXTRA_BODY_JSON',
    'llm-parallel-fastest-mode': 'LLM_PARALLEL_FASTEST_MODE',
    'enable-translation': 'ENABLE_TRANSLATION',
    'show-partial-results': 'TRANSLATE_PARTIAL_RESULTS',
    'enable-furigana': 'ENABLE_JA_FURIGANA',
    'enable-pinyin': 'ENABLE_ZH_PINYIN',
    'enable-arabic-reshaper': 'ENABLE_ARABIC_RESHAPER',
    'remove-trailing-period': 'REMOVE_TRAILING_PERIOD',
    'show-original-and-lang-tag': 'SHOW_ORIGINAL_AND_LANG_TAG',
    'text-fancy-style': 'TEXT_FANCY_STYLE',
    'enable-mic-control': 'PAUSE_ON_MUTE',
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
    'others-translation-enabled': 'OTHERS_TRANSLATION_ENABLED',
    'others-asr-backend': 'OTHERS_ASR_BACKEND',
    'others-target-language': 'OTHERS_TARGET_LANGUAGE',
    'screen-translation-enabled': 'SCREEN_TRANSLATION_ENABLED',
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

document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        refreshLogs();
    }, 1200);
    setInterval(() => {
        refreshLogs();
    }, 2000);
});
