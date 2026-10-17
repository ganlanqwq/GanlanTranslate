/* 安装版窗口没有系统标题栏（2026-10-17 岚：要无头窗口）。
 *
 * 只在安装版的 Qt 窗口里生效（qt_ui/main_window.py 注入了 QWebChannel）；在浏览器里打开网页什么都不做。
 * - 右上角画最小化 / 最大化 / 关闭三个按钮，点了调 Qt。
 * - 网页最上面 DRAG_HEIGHT 这一条能拖动窗口：把这一条里能点的东西（窗口按钮、滚上来的顶栏按钮、提示条）
 *   的位置报给 Qt，其余地方 Qt 交给系统当标题栏（拖动、贴边吸附、双击最大化、右键窗口菜单）。
 * - 往下滚到页首图片下面时，这一条换成半透明的浅色底，按钮换深色。
 */
(function () {
    'use strict';

    if (!window.qt || !window.qt.webChannelTransport || typeof window.QWebChannel !== 'function') {
        return;
    }

    const DRAG_HEIGHT = 32;
    const INTERACTIVE = [
        'a[href]', 'button', 'input', 'select', 'textarea', 'label', 'summary',
        '[role="button"]', '[onclick]', '[tabindex]:not([tabindex="-1"])',
        '.toast-area .message', '.gl-select-menu', '.language-combo-menu',
    ].join(',');
    const GLYPHS = { minimize: '', maximize: '', restore: '', close: '' };
    const LABELS = {
        'zh-CN': { minimize: '最小化', maximize: '最大化', restore: '向下还原', close: '关闭' },
        en: { minimize: 'Minimize', maximize: 'Maximize', restore: 'Restore', close: 'Close' },
        ja: { minimize: '最小化', maximize: '最大化', restore: '元に戻す', close: '閉じる' },
        ko: { minimize: '최소화', maximize: '최대화', restore: '이전 크기로', close: '닫기' },
    };

    let bridge = null;
    let chrome = null;
    let maximizeButton = null;
    let maximized = false;
    let lastReport = '';
    let frame = 0;

    function labels() {
        const lang = window.i18n && window.i18n.getCurrentLanguage ? window.i18n.getCurrentLanguage() : 'zh-CN';
        return LABELS[lang] || LABELS.en;
    }

    function button(action) {
        const el = document.createElement('button');
        el.type = 'button';
        el.className = 'window-chrome-btn' + (action === 'close' ? ' close' : '');
        el.dataset.action = action;
        el.tabIndex = -1;
        el.textContent = GLYPHS[action];
        el.addEventListener('click', () => {
            if (!bridge) return;
            if (action === 'minimize') bridge.minimize();
            else if (action === 'maximize') bridge.toggleMaximize();
            else bridge.closeWindow();
        });
        return el;
    }

    function refreshLabels() {
        if (!chrome) return;
        const text = labels();
        chrome.querySelectorAll('.window-chrome-btn').forEach((el) => {
            let key = el.dataset.action;
            if (key === 'maximize' && maximized) key = 'restore';
            el.title = text[key];
            el.setAttribute('aria-label', text[key]);
        });
    }

    function setState(state) {
        if (!chrome || !state) return;
        maximized = !!state.maximized;
        chrome.classList.toggle('maximized', maximized);
        chrome.classList.toggle('inactive', state.active === false);
        maximizeButton.textContent = maximized ? GLYPHS.restore : GLYPHS.maximize;
        refreshLabels();
    }

    let heroBottom = -1;

    // 页首图片滚出这一条后换浅色底；滚动条轨道在页首图片那一段跟着变深（style.css 第 15 节）
    function syncSolid() {
        const hero = document.querySelector('.hero');
        const rect = hero ? hero.getBoundingClientRect() : null;
        chrome.classList.toggle('solid', rect ? rect.bottom <= DRAG_HEIGHT : window.scrollY > 0);
        const bottom = rect ? Math.round(rect.bottom + window.scrollY) : 0;
        if (bottom !== heroBottom) {
            heroBottom = bottom;
            document.documentElement.style.setProperty('--app-hero-bottom', `${bottom}px`);
        }
    }

    function report() {
        frame = 0;
        if (!chrome || !bridge) return;
        syncSolid();
        const holes = [];
        document.querySelectorAll(INTERACTIVE).forEach((el) => {
            const rect = el.getBoundingClientRect();
            if (!rect.width || !rect.height || rect.bottom <= 0 || rect.top >= DRAG_HEIGHT) return;
            // 被别的东西（比如浅色底）盖住的不算
            const x = Math.min(Math.max(rect.left + rect.width / 2, 0), window.innerWidth - 1);
            const y = Math.min(Math.max(rect.top + rect.height / 2, 0), DRAG_HEIGHT - 1);
            const top = document.elementFromPoint(x, y);
            if (!top || !(top === el || el.contains(top) || top.contains(el))) return;
            holes.push([rect.left, rect.top, rect.width, rect.height].map((v) => Math.round(v * 10) / 10));
        });
        const payload = JSON.stringify({ height: DRAG_HEIGHT, holes });
        if (payload !== lastReport) {
            lastReport = payload;
            bridge.setDragRegion(payload);
        }
    }

    function scheduleReport() {
        if (!frame) frame = window.requestAnimationFrame(report);
    }

    function build() {
        document.documentElement.classList.add('app-window');
        chrome = document.createElement('div');
        chrome.className = 'window-chrome';
        chrome.setAttribute('aria-hidden', 'false');
        const bg = document.createElement('div');
        bg.className = 'window-chrome-bg';
        const buttons = document.createElement('div');
        buttons.className = 'window-chrome-buttons';
        buttons.appendChild(button('minimize'));
        maximizeButton = button('maximize');
        buttons.appendChild(maximizeButton);
        buttons.appendChild(button('close'));
        chrome.appendChild(bg);
        chrome.appendChild(buttons);
        document.body.appendChild(chrome);
        refreshLabels();

        window.addEventListener('scroll', scheduleReport, { passive: true });
        window.addEventListener('resize', scheduleReport);
        document.addEventListener('ganlan:page', scheduleReport);
        // 换界面语言、提示条出现消失等：每秒补报一次，没变化不发
        window.setInterval(() => {
            refreshLabels();
            scheduleReport();
        }, 1000);
        scheduleReport();
    }

    window.ganlanWindowChrome = { setState, refresh: scheduleReport };

    new window.QWebChannel(window.qt.webChannelTransport, (channel) => {
        bridge = channel.objects.ganlanWindow;
        if (!bridge) return;
        const start = () => {
            build();
            bridge.state((json) => {
                try {
                    setState(JSON.parse(json));
                } catch (_) { /* 拿不到就按没最大化 */ }
            });
        };
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', start);
        } else {
            start();
        }
    });
})();
