/**
 * ASR 提供商切换（百炼 / OpenAI 兼容）+ 识别/翻译模式按钮
 */
(function () {
    'use strict';

    const ASR_PROVIDER_KEY = 'ganlan-asr-provider';
    const DEFAULT_QWEN_AUDIO3_MODEL = 'qwen-audio-3.0-asr-flash-streaming';

    const byId = (id) => document.getElementById(id);

    let asrProvider = 'bailian';

    function setSelectValue(id, value) {
        const el = byId(id);
        if (el && el.value !== value) {
            el.value = value;
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }

    function setTextValue(id, value) {
        const el = byId(id);
        if (!el || el.value === value) return;
        el.value = value;
        if (typeof window.syncLanguageComboClearVisibility === 'function' && el.closest) {
            window.syncLanguageComboClearVisibility(el.closest('.language-combo'));
        }
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function detectAsrProviderFromForm() {
        const backend = (byId('asr-backend')?.value || '').trim();
        if (backend === 'custom_stt' || backend === 'custom_audio') return 'openai';
        return 'bailian';
    }

    function syncAsrProviderUi() {
        const isBailian = asrProvider === 'bailian';
        const bailian = byId('advanced-asr-bailian-block');
        const openai = byId('advanced-asr-openai-block');
        if (bailian) bailian.style.display = isBailian ? '' : 'none';
        if (openai) openai.style.display = isBailian ? 'none' : '';

        document.querySelectorAll('[data-simple-asr-provider]').forEach((button) => {
            const active = button.dataset.simpleAsrProvider === asrProvider;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
    }

    function setAsrProvider(provider, options = {}) {
        const { applyBackend = true, save = true } = options;
        asrProvider = provider === 'openai' ? 'openai' : 'bailian';
        if (applyBackend) {
            if (asrProvider === 'bailian') {
                setSelectValue('asr-backend', 'qwen_audio3');
                const modelEl = byId('qwen-audio3-model');
                if (modelEl && !modelEl.value.trim()) {
                    setTextValue('qwen-audio3-model', DEFAULT_QWEN_AUDIO3_MODEL);
                }
            } else {
                setSelectValue('asr-backend', 'custom_stt');
            }
        }
        syncAsrProviderUi();
        if (save) {
            try {
                localStorage.setItem(ASR_PROVIDER_KEY, asrProvider);
            } catch (e) { /* ignore */ }
        }
        if (typeof window.updateDashscopeKeyFieldState === 'function') {
            window.updateDashscopeKeyFieldState();
        }
        if (typeof onSettingChange === 'function' && applyBackend) {
            onSettingChange();
        }
    }

    function syncTranslationModeControl() {
        const enableEl = byId('enable-translation');
        if (!enableEl) return;
        document.querySelectorAll('[data-simple-translation-mode]').forEach((button) => {
            const isTranslation = button.dataset.simpleTranslationMode === 'translation';
            const active = enableEl.checked === isTranslation;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
    }

    function setTranslationEnabled(enabled) {
        const enableEl = byId('enable-translation');
        if (!enableEl || enableEl.checked === enabled) {
            syncTranslationModeControl();
            return;
        }
        enableEl.checked = enabled;
        enableEl.dispatchEvent(new Event('change', { bubbles: true }));
        syncTranslationModeControl();
    }

    const enableTranslationEl = byId('enable-translation');
    if (enableTranslationEl) {
        enableTranslationEl.addEventListener('change', syncTranslationModeControl);
    }

    document.querySelectorAll('[data-simple-translation-mode]').forEach((button) => {
        button.addEventListener('click', () => {
            setTranslationEnabled(button.dataset.simpleTranslationMode === 'translation');
        });
    });

    document.querySelectorAll('[data-simple-asr-provider]').forEach((button) => {
        button.addEventListener('click', () => {
            setAsrProvider(button.dataset.simpleAsrProvider, { applyBackend: true, save: true });
        });
    });

    const asrBackendEl = byId('asr-backend');
    if (asrBackendEl) {
        asrBackendEl.addEventListener('change', () => {
            const next = detectAsrProviderFromForm();
            if (next !== asrProvider) {
                setAsrProvider(next, { applyBackend: false, save: true });
            }
        });
    }

    try {
        const storedProvider = localStorage.getItem(ASR_PROVIDER_KEY);
        if (storedProvider === 'openai' || storedProvider === 'bailian') {
            asrProvider = storedProvider;
        } else {
            asrProvider = detectAsrProviderFromForm();
        }
    } catch (e) {
        asrProvider = detectAsrProviderFromForm();
    }

    // 同步 UI，不强制改后端（由已保存配置决定）
    setAsrProvider(asrProvider, { applyBackend: false, save: false });
    syncTranslationModeControl();

    // 兼容旧调用点
    window.GanlanSimpleMode = {
        getMode: () => 'advanced',
        setMode: () => {},
        getAsrProvider: () => asrProvider,
        setAsrProvider,
        applySimpleDefaults: () => {},
        ensureSimpleLlmTemplateEditable: () => {},
        DEFAULT_QWEN_AUDIO3_MODEL,
        SIMPLE_LLM_TEMPLATE: 'custom1',
    };
})();


/**
 * Settings area page switching (core / self / others / screen / advanced)
 */
(function () {
    'use strict';

    const PAGE_KEY = 'ganlan-ui-page';
    const PAGES = ['core', 'self', 'others', 'screen', 'advanced', 'words', 'about'];

    function normalizePage(value) {
        return PAGES.includes(value) ? value : 'core';
    }

    // 标签栏的白色滑块：移到当前标签下面（切页、窗口变宽窄、换界面语言后都要重算）
    function moveTabIndicator() {
        const bar = document.querySelector('.page-tabs');
        const indicator = bar && bar.querySelector('.page-tab-indicator');
        const active = bar && bar.querySelector('.app-page-btn.active');
        if (!indicator || !active) return;
        indicator.style.width = `${active.offsetWidth}px`;
        indicator.style.transform = `translateX(${active.offsetLeft}px)`;
        if (!bar.classList.contains('has-indicator')) {
            bar.classList.add('has-indicator');
            // 第一次定位不要滑过去
            requestAnimationFrame(() => bar.classList.add('indicator-ready'));
        }
        // 窄窗口里标签栏横向滚动：只滚标签栏本身，不要带着整页跳
        const left = active.offsetLeft;
        const right = left + active.offsetWidth;
        if (left < bar.scrollLeft) {
            bar.scrollLeft = left - 8;
        } else if (right > bar.scrollLeft + bar.clientWidth) {
            bar.scrollLeft = right - bar.clientWidth + 8;
        }
    }

    function setAppPage(page, options = {}) {
        const { save = true } = options;
        const next = normalizePage(page);

        document.querySelectorAll('.app-page').forEach((el) => {
            const match = el.dataset.appPage === next;
            el.hidden = !match;
            el.classList.toggle('is-active', match);
        });

        document.querySelectorAll('.app-page-btn').forEach((btn) => {
            const active = btn.dataset.appPage === next;
            btn.classList.toggle('active', active);
            btn.setAttribute('aria-pressed', active ? 'true' : 'false');
        });

        if (save) {
            try {
                localStorage.setItem(PAGE_KEY, next);
            } catch (e) { /* ignore */ }
        }
        moveTabIndicator();
        // pages.js 据此在打开「词语设置」「详情」时才去拉数据
        document.dispatchEvent(new CustomEvent('ganlan:page', { detail: { page: next } }));
    }

    document.querySelectorAll('.app-page-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
            setAppPage(btn.dataset.appPage, { save: true });
        });
    });

    let initial = 'core';
    try {
        initial = normalizePage(localStorage.getItem(PAGE_KEY) || 'core');
    } catch (e) {
        initial = 'core';
    }
    setAppPage(initial, { save: false });
    window.addEventListener('resize', moveTabIndicator);
    // 标签文字由 i18n 在 DOMContentLoaded 里填进去，宽度要等那之后再量
    document.addEventListener('DOMContentLoaded', () => setTimeout(moveTabIndicator, 0));
    window.addEventListener('load', moveTabIndicator);
    document.addEventListener('i18n:languageChanged', moveTabIndicator);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(moveTabIndicator);

    window.GanlanUiPages = {
        setAppPage,
        getPage: () => {
            try {
                return normalizePage(localStorage.getItem(PAGE_KEY) || 'core');
            } catch (e) {
                return 'core';
            }
        },
    };
})();
