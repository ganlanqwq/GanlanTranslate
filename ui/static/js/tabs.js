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
 * 设置区的两层菜单：上面一条大类（设置 / 功能 / 插件 / 信息），下面一条当前大类的子页面。
 * 2026-10-17 岚要求从一条七个标签改成两层。
 */
(function () {
    'use strict';

    const PAGE_KEY = 'ganlan-ui-page';
    const GROUP_PAGES_KEY = 'ganlan-ui-group-pages';
    const GROUPS = {
        settings: ['core', 'advanced', 'words'],
        features: ['self', 'others', 'screen'],
        plugins: ['plugins'],
        info: ['about', 'credits'],
    };
    const GROUP_ORDER = Object.keys(GROUPS);
    const PAGES = GROUP_ORDER.flatMap((group) => GROUPS[group]);
    const SUBNAV_SHIFT = 22;
    const EASE_OUT = 'cubic-bezier(0.2, 0.7, 0.2, 1)';
    const reducedMotion = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;

    let currentGroup = null;
    // 每个大类上次看的子页面，点大类时回到那一页
    let lastPages = {};
    try {
        lastPages = JSON.parse(localStorage.getItem(GROUP_PAGES_KEY) || '{}') || {};
    } catch (e) {
        lastPages = {};
    }

    function normalizePage(value) {
        return PAGES.includes(value) ? value : 'core';
    }

    function groupOf(page) {
        return GROUP_ORDER.find((group) => GROUPS[group].includes(page)) || 'settings';
    }

    function motionAllowed() {
        return !(reducedMotion && reducedMotion.matches);
    }

    function subtabsOf(group) {
        return document.querySelector(`.page-subtabs[data-page-group="${group}"]`);
    }

    // 滑块（大类的白底、子页面的绿色下划线）移到当前按钮下面；animate=false 时直接跳过去
    function placeIndicator(bar, animate = true) {
        const indicator = bar && bar.querySelector(':scope > .page-tab-indicator, :scope > .page-subtab-indicator');
        const active = bar && bar.querySelector(':scope > .active');
        if (!indicator || !active || bar.hidden) return;
        if (!animate) bar.classList.remove('indicator-ready');
        indicator.style.width = `${active.offsetWidth}px`;
        indicator.style.transform = `translateX(${active.offsetLeft}px)`;
        bar.classList.add('has-indicator');
        if (!bar.classList.contains('indicator-ready')) {
            void indicator.offsetWidth;
            // 第一次定位不要滑过去
            requestAnimationFrame(() => bar.classList.add('indicator-ready'));
        }
        // 窄窗口里横向滚动：只滚这一条本身，不要带着整页跳
        const left = active.offsetLeft;
        const right = left + active.offsetWidth;
        if (left < bar.scrollLeft) {
            bar.scrollLeft = left - 8;
        } else if (right > bar.scrollLeft + bar.clientWidth) {
            bar.scrollLeft = right - bar.clientWidth + 8;
        }
    }

    function placeIndicators() {
        placeIndicator(document.querySelector('.page-groups'));
        placeIndicator(subtabsOf(currentGroup));
    }

    // 换大类：旧的子页面条往反方向滑出、淡出，新的从另一边滑进来（两条叠在同一格里）
    function showSubtabs(group, previousGroup) {
        const animate = motionAllowed() && Boolean(previousGroup) && previousGroup !== group;
        const direction = GROUP_ORDER.indexOf(group) > GROUP_ORDER.indexOf(previousGroup) ? 1 : -1;
        document.querySelectorAll('.page-subtabs').forEach((el) => {
            if (el.getAnimations) el.getAnimations().forEach((animation) => animation.cancel());
            el.classList.remove('leaving');
            const name = el.dataset.pageGroup;
            if (name === group) {
                el.hidden = false;
                placeIndicator(el, false);
                if (animate && el.animate) {
                    el.animate([
                        { opacity: 0, transform: `translateX(${direction * SUBNAV_SHIFT}px)` },
                        { opacity: 1, transform: 'none' },
                    ], { duration: 300, delay: 120, easing: EASE_OUT, fill: 'backwards' });
                }
            } else if (animate && name === previousGroup && el.animate) {
                el.classList.add('leaving');
                const out = el.animate([
                    { opacity: 1, transform: 'none' },
                    { opacity: 0, transform: `translateX(${-direction * SUBNAV_SHIFT}px)` },
                ], { duration: 140, easing: 'ease-in', fill: 'forwards' });
                out.onfinish = () => {
                    el.hidden = true;
                    el.classList.remove('leaving');
                    out.cancel();
                };
            } else {
                el.hidden = true;
            }
        });
    }

    function setAppPage(page, options = {}) {
        const { save = true } = options;
        const next = normalizePage(page);
        const group = groupOf(next);
        const previousGroup = currentGroup;
        currentGroup = group;
        lastPages[group] = next;

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

        document.querySelectorAll('.page-group-btn').forEach((btn) => {
            const active = btn.dataset.pageGroup === group;
            btn.classList.toggle('active', active);
            btn.setAttribute('aria-pressed', active ? 'true' : 'false');
        });

        if (save) {
            try {
                localStorage.setItem(PAGE_KEY, next);
                localStorage.setItem(GROUP_PAGES_KEY, JSON.stringify(lastPages));
            } catch (e) { /* ignore */ }
        }
        placeIndicator(document.querySelector('.page-groups'));
        if (previousGroup === group) {
            placeIndicator(subtabsOf(group));
        } else {
            showSubtabs(group, previousGroup);
        }
        // pages.js 据此在打开「词语设置」「软件信息」时才去拉数据
        document.dispatchEvent(new CustomEvent('ganlan:page', { detail: { page: next } }));
    }

    function setPageGroup(group) {
        const pages = GROUPS[group];
        if (!pages) return;
        setAppPage(pages.includes(lastPages[group]) ? lastPages[group] : pages[0], { save: true });
    }

    document.querySelectorAll('.page-group-btn').forEach((btn) => {
        btn.addEventListener('click', () => setPageGroup(btn.dataset.pageGroup));
    });

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
    window.addEventListener('resize', placeIndicators);
    // 标签文字由 i18n 在 DOMContentLoaded 里填进去，宽度要等那之后再量
    document.addEventListener('DOMContentLoaded', () => setTimeout(placeIndicators, 0));
    window.addEventListener('load', placeIndicators);
    document.addEventListener('i18n:languageChanged', placeIndicators);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(placeIndicators);

    window.GanlanUiPages = {
        setAppPage,
        setPageGroup,
        getPage: () => {
            try {
                return normalizePage(localStorage.getItem(PAGE_KEY) || 'core');
            } catch (e) {
                return 'core';
            }
        },
    };
})();
