/**
 * 核心设置的「API 配置」（1.1.2，岚：API 可以选择两种大模式，百炼（懒人）模式及 OpenAI 兼容自定义）。
 *
 * - 百炼：只填一个阿里云百炼 Key，语音识别（Qwen-Audio-3 实时）、翻译、图像识别都用它，地址固定
 *   （勾了「国际版」就是 dashscope-intl）。三个模型可以改，放在「模型」里，每次打开都是折叠的。
 * - OpenAI 兼容：语音识别 / 翻译 / 图像识别各填各的地址、Key、模型。没填完的那一项打开时展开，
 *   填完的折叠。语音识别只用 OpenAI 兼容接口（/audio/transcriptions）——岚要求去掉「百炼实时识别」那个选择。
 *
 * 两种模式的输入框各是各的，切换模式不会把另一边填好的冲掉：
 *   百炼用 dashscope-api-key / use-international-endpoint / qwen-audio3-model / bailian-llm-model / bailian-ocr-model，
 *   兼容用 custom-api-* / llm-base-url / llm-api-key / llm-model / openai-compat-extra-body-json / screen-ocr-*。
 * 发给后端时按当前模式取（effective()），main.js 的 saveConfig、启动检查、悬浮窗都走这里。
 * Key 只在浏览器表单里（和以前一样）。
 */
(function () {
    'use strict';

    const MODE_KEY = 'ganlan-api-mode';
    const LEGACY_PROVIDER_KEY = 'ganlan-asr-provider';
    const BAILIAN_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1';
    const BAILIAN_URL_INTL = 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1';
    const BAILIAN_CONSOLE = 'https://bailian.console.aliyun.com/';
    const BAILIAN_CONSOLE_INTL = 'https://modelstudio.console.alibabacloud.com/';
    const BAILIAN_EXTRA_BODY = '{"enable_thinking": false}';
    const DEFAULT_MODELS = {
        asr: 'qwen-audio-3.1-asr-flash-streaming',
        llm: 'qwen3.7-flash',
        ocr: 'qwen3.5-ocr',
    };
    // 以前的默认模型（用户没改过才会是它）→ 现在的默认。2026-11 岚：「不要用这些老模型了」
    const OLD_DEFAULT_MODELS = {
        'qwen-audio3-model': ['qwen-audio-3.0-asr-flash-streaming', DEFAULT_MODELS.asr],
        'bailian-llm-model': ['qwen3.5-flash', DEFAULT_MODELS.llm],
        'bailian-ocr-model': ['qwen-vl-ocr', DEFAULT_MODELS.ocr],
    };

    function upgradeOldDefaultModels() {
        let changed = false;
        Object.entries(OLD_DEFAULT_MODELS).forEach(([id, [oldValue, newValue]]) => {
            const el = byId(id);
            if (el && el.value.trim() === oldValue) { el.value = newValue; changed = true; }
        });
        // 兼容模式里填的是百炼地址、模型也是旧默认的，一起换
        [['llm-base-url', 'llm-model', 'bailian-llm-model'], ['screen-ocr-base-url', 'screen-ocr-model', 'bailian-ocr-model']]
            .forEach(([urlId, modelId, bailianId]) => {
                const model = byId(modelId);
                const [oldValue, newValue] = OLD_DEFAULT_MODELS[bailianId];
                if (model && isDashscopeUrl(val(urlId)) && model.value.trim() === oldValue) { model.value = newValue; changed = true; }
            });
        return changed;
    }
    const SUBS = ['asr', 'llm', 'ocr'];

    const byId = (id) => document.getElementById(id);
    const val = (id) => (byId(id)?.value || '').trim();
    const t = (key, params) => (window.i18n ? window.i18n.t(key, params) : key);
    const reducedMotion = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let mode = 'bailian';
    let ready = false;

    function isDashscopeUrl(url) {
        return /^https?:\/\/dashscope(-intl)?\.aliyuncs\.com\b/i.test(String(url || '').trim());
    }

    function international() {
        return byId('use-international-endpoint')?.checked === true;
    }

    function bailianBaseUrl() {
        return international() ? BAILIAN_URL_INTL : BAILIAN_URL;
    }

    // ---------- 当前模式实际用的值 ----------

    function usesBailianAsr() {
        return mode === 'bailian';
    }

    /**
     * 兼容模式里语音识别填的是百炼地址 + 实时流式模型（…-streaming / …-realtime）：这种模型没有 HTTP 接口，
     * 改走百炼实时识别（和百炼模式一样，有半句、热词），Key 用这一块填的，国际版看地址。不是这种情况返回 null。
     */
    function compatRealtimeAsr() {
        if (mode !== 'custom') return null;
        const url = val('custom-api-base-url');
        const model = val('custom-api-model');
        if (!isDashscopeUrl(url) || !/(streaming|realtime)/i.test(model)) return null;
        return { model, key: val('custom-api-key'), international: /dashscope-intl/i.test(url) };
    }

    function effective() {
        const dashscopeKey = val('dashscope-api-key');
        const realtime = compatRealtimeAsr();
        const plan = {
            mode,
            asrBackend: usesBailianAsr() || realtime ? 'qwen_audio3' : 'custom_stt',
            dashscopeKey,
            asrModel: realtime ? realtime.model : (val('qwen-audio3-model') || DEFAULT_MODELS.asr),
            // 发给后端的百炼 Key / 国际版（兼容模式走百炼实时识别时用语音识别那块的）
            asrDashscopeKey: realtime ? realtime.key : dashscopeKey,
            international: realtime ? realtime.international : international(),
            asrRealtime: Boolean(realtime),
        };
        if (mode === 'bailian') {
            plan.llm = { baseUrl: bailianBaseUrl(), model: val('bailian-llm-model') || DEFAULT_MODELS.llm, key: dashscopeKey, extraBody: BAILIAN_EXTRA_BODY };
            // 识别图片的地址给国内的；后端勾了「国际版」时自己换成 intl。Key 留空 = 后端用百炼 Key
            plan.ocr = { baseUrl: BAILIAN_URL, model: val('bailian-ocr-model') || DEFAULT_MODELS.ocr, key: '' };
        } else {
            plan.llm = { baseUrl: val('llm-base-url'), model: val('llm-model'), key: val('llm-api-key'), extraBody: val('openai-compat-extra-body-json') };
            plan.ocr = { baseUrl: val('screen-ocr-base-url'), model: val('screen-ocr-model'), key: val('screen-ocr-api-key') };
        }
        return plan;
    }

    function subComplete(name) {
        if (name === 'asr') return Boolean(val('custom-api-base-url') && val('custom-api-key') && val('custom-api-model'));
        if (name === 'llm') return Boolean(val('llm-base-url') && val('llm-api-key') && val('llm-model'));
        if (name === 'ocr') return Boolean(val('screen-ocr-base-url') && val('screen-ocr-api-key') && val('screen-ocr-model'));
        return false;
    }

    function modeComplete() {
        if (mode === 'bailian') return Boolean(val('dashscope-api-key'));
        return SUBS.every(subComplete);
    }

    /** 「分析错误」用：现在哪几个 Key 填了 */
    function keysFilled() {
        if (mode === 'bailian') {
            const has = Boolean(val('dashscope-api-key'));
            return { asr: has, llm: has, ocr: has };
        }
        return { asr: subComplete('asr'), llm: Boolean(val('llm-api-key')), ocr: Boolean(val('screen-ocr-api-key')) };
    }

    // ---------- 画界面 ----------

    function placeIndicator(animate) {
        const bar = byId('api-mode-switch');
        const indicator = bar?.querySelector('.api-mode-indicator');
        const active = bar?.querySelector(`.api-mode-btn[data-api-mode="${mode}"]`);
        if (!indicator || !active || !bar.offsetWidth) return;
        if (!animate) bar.classList.remove('indicator-ready');
        indicator.style.width = `${active.offsetWidth}px`;
        indicator.style.transform = `translateX(${active.offsetLeft - 4}px)`;
        if (!bar.classList.contains('indicator-ready')) {
            void indicator.offsetWidth;
            requestAnimationFrame(() => bar.classList.add('indicator-ready'));
        }
    }

    function showPanel(panel, show, animate) {
        if (!panel) return;
        if (panel._anim) { panel._anim.cancel(); panel._anim = null; }
        if (!show) {
            panel.hidden = true;
            return;
        }
        const wasHidden = panel.hidden;
        panel.hidden = false;
        if (animate && wasHidden && !reducedMotion() && panel.animate) {
            panel._anim = panel.animate([
                { opacity: 0, transform: 'translateY(6px)' },
                { opacity: 1, transform: 'none' },
            ], { duration: 260, easing: 'cubic-bezier(0.2, 0.7, 0.2, 1)' });
            panel._anim.onfinish = () => { panel._anim = null; };
        }
    }

    function setBadge(el, complete) {
        if (!el) return;
        el.className = 'api-sub-status ' + (complete ? 'is-ok' : 'is-missing');
        el.textContent = complete ? t('api.status.ok') : t('api.status.missing');
    }

    function summarize() {
        const llmModel = val('bailian-llm-model') || DEFAULT_MODELS.llm;
        const ocrModel = val('bailian-ocr-model') || DEFAULT_MODELS.ocr;
        const asrModel = val('qwen-audio3-model') || DEFAULT_MODELS.asr;
        const modelsSummary = byId('bailian-models-summary');
        if (modelsSummary) modelsSummary.textContent = [asrModel, llmModel, ocrModel].join(' · ');

        const asrSummary = byId('api-sub-asr-summary');
        if (asrSummary) asrSummary.textContent = val('custom-api-model') || t('api.status.notSet');
        const llmSummary = byId('api-sub-llm-summary');
        if (llmSummary) llmSummary.textContent = val('llm-model') || t('api.status.notSet');
        const ocrSummary = byId('api-sub-ocr-summary');
        if (ocrSummary) ocrSummary.textContent = val('screen-ocr-model') || t('api.status.notSet');
        SUBS.forEach((name) => setBadge(byId(`api-sub-${name}-status`), subComplete(name)));

        const address = byId('bailian-address');
        if (address) address.textContent = international() ? 'dashscope-intl.aliyuncs.com' : 'dashscope.aliyuncs.com';
        const link = byId('bailian-console-link');
        if (link) link.href = international() ? BAILIAN_CONSOLE_INTL : BAILIAN_CONSOLE;

        const cardSummary = byId('api-card-summary');
        if (cardSummary) {
            const complete = modeComplete();
            const name = mode === 'bailian' ? t('api.mode.bailian') : t('api.mode.custom');
            let state;
            if (mode === 'bailian') {
                state = complete ? t('api.status.ready') : t('api.status.needKey');
            } else {
                const done = SUBS.filter(subComplete).length;
                state = complete ? t('api.status.ready') : t('api.status.partial', { done, total: SUBS.length });
            }
            cardSummary.textContent = `${name} · ${state}`;
            cardSummary.classList.toggle('is-missing', !complete);
        }
    }

    function render(animate) {
        document.querySelectorAll('#api-mode-switch .api-mode-btn').forEach((btn) => {
            const active = btn.dataset.apiMode === mode;
            btn.classList.toggle('active', active);
            btn.setAttribute('aria-checked', active ? 'true' : 'false');
        });
        showPanel(byId('api-panel-bailian'), mode === 'bailian', animate);
        showPanel(byId('api-panel-custom'), mode === 'custom', animate);
        const hiddenMode = byId('api-mode');
        if (hiddenMode) hiddenMode.value = mode;
        placeIndicator(animate);
        summarize();
    }

    // 隐藏的 asr-backend 下拉框跟着模式走（别处还在读它）
    function syncBackendSelect(dispatch) {
        const select = byId('asr-backend');
        const backend = usesBailianAsr() ? 'qwen_audio3' : 'custom_stt';
        if (!select || select.value === 'local' || select.value === backend) return;
        select.value = backend;
        if (dispatch) select.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function afterChange() {
        if (typeof window.updateDashscopeKeyFieldState === 'function') window.updateDashscopeKeyFieldState();
        if (typeof window.onSettingChange === 'function') window.onSettingChange();
    }

    function setMode(next, options = {}) {
        const value = next === 'custom' ? 'custom' : 'bailian';
        const changed = value !== mode;
        mode = value;
        try { localStorage.setItem(MODE_KEY, mode); } catch (e) { /* ignore */ }
        syncBackendSelect(false);
        render(options.animate !== false && changed);
        if (changed && options.user) {
            if (mode === 'custom') collapseSubsByCompleteness();
            afterChange();
        }
    }

    // ---------- 折叠 ----------

    function setCollapsed(id, collapsed) {
        const content = byId(id);
        if (!content) return;
        const isCollapsed = content.classList.contains('collapsed');
        if (isCollapsed === collapsed) return;
        content.classList.toggle('collapsed', collapsed);
        content.style.removeProperty('max-height');
        if (typeof window.updateCollapsibleIcon === 'function') window.updateCollapsibleIcon(byId(`${id}-icon`), collapsed);
        if (typeof window.syncCollapsibleContainerState === 'function') window.syncCollapsibleContainerState(content);
        if (typeof window.syncCollapsibleAccessibility === 'function') window.syncCollapsibleAccessibility(content);
    }

    function collapseSubsByCompleteness() {
        SUBS.forEach((name) => setCollapsed(`api-sub-${name}`, subComplete(name)));
    }

    /** 打开页面、读完配置和 Key 之后调一次：填好的折叠，没填的展开；「模型」每次都折叠 */
    function onConfigReady() {
        ready = true;
        render(false);
        setCollapsed('bailian-models', true);
        collapseSubsByCompleteness();
        setCollapsed('api-keys', modeComplete());
        if (pendingModelUpgrade) {
            pendingModelUpgrade = false;
            render(false);
            afterChange();   // 存浏览器 + 推给后端
        }
        if (pendingMigrationNotice) {
            pendingMigrationNotice = false;
            // 马上存回浏览器，下次打开不再提示
            if (typeof window.saveConfigToLocalStorage === 'function') window.saveConfigToLocalStorage();
            setTimeout(() => {
                if (typeof window.showMessage === 'function') window.showMessage(t('api.msg.asrMovedToBailian'), 'warning');
            }, 600);
        }
    }

    /** 启动检查发现少填了：展开所在的地方，把框标红 */
    function reveal(ids) {
        const list = (Array.isArray(ids) ? ids : [ids]).filter(Boolean);
        if (!list.length) return;
        if (byId('api-keys')?.classList.contains('collapsed') && typeof window.toggleCollapsible === 'function') {
            window.toggleCollapsible('api-keys');
        }
        list.forEach((id) => {
            const input = byId(id);
            if (!input) return;
            const content = input.closest('.collapsible-content.collapsed');
            if (content && content.id && content.id !== 'api-keys') window.toggleCollapsible(content.id);
        });
        if (typeof window.highlightInput === 'function') list.forEach(window.highlightInput);
        const first = byId(list[0]);
        if (first) setTimeout(() => first.scrollIntoView({ block: 'center', behavior: reducedMotion() ? 'auto' : 'smooth' }), 80);
    }

    /** 启动前检查：返回第一个缺的输入框 id 和提示，不缺返回 null */
    function missingFor(modes, translationNeeded) {
        const needsAsr = modes.includes('self') || modes.includes('others');
        const needsLlm = translationNeeded;
        const needsOcr = modes.includes('screen');
        if (mode === 'bailian') {
            if ((needsAsr || needsLlm || needsOcr) && !val('dashscope-api-key')) {
                return { id: 'dashscope-api-key', message: t('api.msg.needBailianKey') };
            }
            return null;
        }
        const groups = [];
        if (needsAsr) groups.push(['api.sub.asr', [['custom-api-base-url', 'label.customApiBaseUrl'], ['custom-api-key', 'label.customApiKey'], ['custom-api-model', 'label.customApiModel']]]);
        if (needsLlm) groups.push(['api.sub.llm', [['llm-base-url', 'label.llmBaseUrl'], ['llm-api-key', 'label.llmKey'], ['llm-model', 'label.llmModel']]]);
        if (needsOcr) groups.push(['api.sub.ocr', [['screen-ocr-base-url', 'label.screenOcrBaseUrl'], ['screen-ocr-api-key', 'label.screenOcrKey'], ['screen-ocr-model', 'label.screenOcrModel']]]);
        for (const [part, fields] of groups) {
            for (const [id, key] of fields) {
                if (!val(id)) return { id, message: t('api.msg.needField', { part: t(part), field: t(key) }) };
            }
        }
        return null;
    }

    // ---------- 存取 ----------

    let pendingMigrationNotice = false;
    let pendingModelUpgrade = false;

    function serialize() {
        return {
            mode,
            bailian_llm_model: val('bailian-llm-model') || DEFAULT_MODELS.llm,
            bailian_ocr_model: val('bailian-ocr-model') || DEFAULT_MODELS.ocr,
        };
    }

    function setInput(id, value) {
        const el = byId(id);
        if (el && typeof value === 'string' && value.trim()) el.value = value.trim();
    }

    function customAsrFilled() {
        return Boolean(val('custom-api-base-url') && val('custom-api-model'));
    }

    // 以前（1.1.1 及以前）没有「模式」：识别用的是自定义接口才算兼容模式，其余都归百炼
    // （识别用百炼、翻译用别家的老配置：兼容模式里已经不能用百炼识别了，归到百炼最不容易坏；别家的设置留在兼容那边）
    function inferFromLegacy() {
        let legacyProvider = '';
        try { legacyProvider = localStorage.getItem(LEGACY_PROVIDER_KEY) || ''; } catch (e) { /* ignore */ }
        const backend = val('asr-backend');
        const customAsr = legacyProvider === 'openai' || backend === 'custom_stt' || backend === 'custom_audio';
        if (isDashscopeUrl(val('llm-base-url')) && val('llm-model')) setInput('bailian-llm-model', val('llm-model'));
        if (isDashscopeUrl(val('screen-ocr-base-url')) && val('screen-ocr-model')) setInput('bailian-ocr-model', val('screen-ocr-model'));
        return customAsr ? 'custom' : 'bailian';
    }

    /** loadConfigFromLocalStorage 读到的 config.api（可能没有） */
    function restore(api) {
        if (api && typeof api === 'object' && (api.mode === 'bailian' || api.mode === 'custom')) {
            setInput('bailian-llm-model', api.bailian_llm_model);
            setInput('bailian-ocr-model', api.bailian_ocr_model);
            if (upgradeOldDefaultModels()) pendingModelUpgrade = true;
            mode = api.mode;
            // 1.1.2 测试版里兼容模式能选「百炼实时识别」；去掉之后，用着它又没填自定义识别接口的，换到百炼模式免得识别用不了
            if (mode === 'custom' && api.custom_asr_provider === 'bailian' && !customAsrFilled()) {
                mode = 'bailian';
                pendingMigrationNotice = true;
            }
        } else {
            let stored = '';
            try { stored = localStorage.getItem(MODE_KEY) || ''; } catch (e) { /* ignore */ }
            const guess = inferFromLegacy();
            mode = stored === 'custom' || stored === 'bailian' ? stored : guess;
        }
        try { localStorage.setItem(MODE_KEY, mode); } catch (e) { /* ignore */ }
        syncBackendSelect(false);
        render(false);
    }

    /** 读了预设（隐藏的 api-mode 被改了）之后 */
    function syncFromForm() {
        const hiddenMode = val('api-mode');
        if (hiddenMode === 'bailian' || hiddenMode === 'custom') mode = hiddenMode;
        try { localStorage.setItem(MODE_KEY, mode); } catch (e) { /* ignore */ }
        syncBackendSelect(false);
        render(true);
    }

    /**
     * 后端发回来的配置（对账时服务器较新）里是「实际用的值」。百炼模式下那是百炼的地址，
     * 不能写进兼容模式的框：先记下兼容的框，写完再放回去，模型名记到百炼那边。
     */
    function snapshotCustomFields() {
        if (mode !== 'bailian') {
            // 兼容模式走百炼实时识别时，后端存的模型、国际版是语音识别那块的，不能写进百炼那边的框
            if (!compatRealtimeAsr()) return null;
            return {
                kind: 'bailian-asr',
                values: { 'qwen-audio3-model': byId('qwen-audio3-model')?.value ?? '' },
                checked: { 'use-international-endpoint': byId('use-international-endpoint')?.checked ?? false },
            };
        }
        const ids = ['llm-base-url', 'llm-model', 'openai-compat-extra-body-json', 'screen-ocr-base-url', 'screen-ocr-model'];
        const values = {};
        ids.forEach((id) => { values[id] = byId(id)?.value ?? ''; });
        return { kind: 'custom', values, checked: {} };
    }

    function absorbServerConfig(config, snap) {
        if (snap) {
            if (snap.kind === 'custom') {
                const llmBase = config?.translation?.llm_base_url;
                if (isDashscopeUrl(llmBase)) setInput('bailian-llm-model', config.translation.llm_model);
                const ocr = config?.screen_translation;
                if (ocr && isDashscopeUrl(ocr.ocr_base_url)) setInput('bailian-ocr-model', ocr.ocr_model);
            }
            Object.entries(snap.values || {}).forEach(([id, value]) => { const el = byId(id); if (el) el.value = value; });
            Object.entries(snap.checked || {}).forEach(([id, value]) => { const el = byId(id); if (el) el.checked = value; });
        }
        syncBackendSelect(false);
        render(false);
    }

    function resetBailianModels() {
        byId('qwen-audio3-model').value = DEFAULT_MODELS.asr;
        byId('bailian-llm-model').value = DEFAULT_MODELS.llm;
        byId('bailian-ocr-model').value = DEFAULT_MODELS.ocr;
        summarize();
        afterChange();
        if (typeof window.showMessage === 'function') window.showMessage(t('api.msg.modelsReset'), 'success');
    }

    /**
     * 「测试连通性」：asr 发半秒静音到 /audio/transcriptions，llm 发一句 ping，ocr 发一张写着字的小图（显示模型认出的字）。
     * 语音识别只在兼容模式里有按钮；翻译、图像识别按当前模式实际用的地址和 Key 测。
     */
    async function testApi(kind) {
        const out = byId(kind === 'asr' ? 'custom-api-test-result' : `${kind}-test-result`);
        const plan = effective();
        const target = kind === 'asr'
            ? { baseUrl: val('custom-api-base-url'), key: val('custom-api-key'), model: val('custom-api-model') }
            : (kind === 'ocr' ? { ...plan.ocr, key: plan.ocr.key || plan.dashscopeKey } : plan.llm);
        if (out) {
            out.style.removeProperty('color');
            out.className = out.className.replace(/\s?api-test-(ok|fail)/g, '');
            out.textContent = t('api.msg.testing');
        }
        try {
            const resp = await fetch('/api/custom-api/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ kind, base_url: target.baseUrl, api_key: target.key, model: target.model }),
            });
            const data = await resp.json();
            if (!out) return;
            if (data.success && kind === 'ocr') {
                const recognized = data.reply && data.expected
                    && data.reply.toUpperCase().replace(/\s+/g, '').includes(data.expected.replace(/\s+/g, ''));
                out.classList.add(recognized ? 'api-test-ok' : 'api-test-fail');
                out.textContent = data.reply
                    ? `${recognized ? '✓' : '✗'} ${t('api.msg.ocrReply', { text: data.reply, expected: data.expected })}`
                    : `✗ ${t('api.msg.ocrNoText')}`;
            } else if (data.success) {
                out.classList.add('api-test-ok');
                out.textContent = `✓ ${data.status_code || 200} OK` + (data.realtime ? ` · ${t('api.msg.asrRealtime')}` : '');
            } else {
                out.classList.add('api-test-fail');
                const code = data.status_code ? `HTTP ${data.status_code}` : '';
                out.textContent = `✗ ${code} ${(data.message || data.snippet || '').slice(0, 120)}`;
            }
        } catch (err) {
            if (out) { out.classList.add('api-test-fail'); out.textContent = `✗ ${err.message || err}`; }
        }
    }

    // ---------- 事件 ----------

    document.querySelectorAll('#api-mode-switch .api-mode-btn').forEach((btn) => {
        btn.addEventListener('click', () => setMode(btn.dataset.apiMode, { user: true }));
    });
    byId('api-mode-switch')?.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        event.preventDefault();
        setMode(mode === 'bailian' ? 'custom' : 'bailian', { user: true });
        byId('api-mode-switch').querySelector(`[data-api-mode="${mode}"]`)?.focus();
    });
    // 填的时候更新右边的「已填写 / 未填写」
    byId('api-keys')?.addEventListener('input', () => { if (ready) summarize(); });
    byId('api-keys')?.addEventListener('change', () => { if (ready) summarize(); });
    ['bailian-llm-model', 'bailian-ocr-model'].forEach((id) => {
        byId(id)?.addEventListener('change', () => afterChange());
    });
    window.addEventListener('resize', () => placeIndicator(false));
    document.addEventListener('i18n:languageChanged', () => { render(false); });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => placeIndicator(false));
    // API 卡片收着的时候量不到宽度，展开时再放一次滑块
    byId('api-keys-card')?.addEventListener('click', () => requestAnimationFrame(() => placeIndicator(false)));

    // 先按存过的模式把界面摆好（配置读完后 restore 会再来一次）
    try {
        const stored = localStorage.getItem(MODE_KEY);
        if (stored === 'custom' || stored === 'bailian') mode = stored;
    } catch (e) { /* ignore */ }
    render(false);

    window.GanlanApiMode = {
        DEFAULT_MODELS,
        BAILIAN_EXTRA_BODY,
        getMode: () => mode,
        setMode,
        effective,
        usesBailianAsr,
        compatRealtimeAsr,
        keysFilled,
        modeComplete,
        subComplete,
        missingFor,
        reveal,
        serialize,
        restore,
        syncFromForm,
        snapshotCustomFields,
        absorbServerConfig,
        onConfigReady,
        resetBailianModels,
        testApi,
        testLlm: () => testApi('llm'),
        isDashscopeUrl,
    };
})();
