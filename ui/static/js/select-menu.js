/* 下拉框的弹出菜单。
 *
 * 系统自带的下拉列表在安装版窗口（QtWebEngine）里是 Windows 样式，没有动画，箭头也不会动。
 * 这里把弹出列表换成自己画的菜单：原生 <select> 留在原地当按钮和数据源，
 * 值、选项、禁用、onchange 都照旧；菜单每次打开时按当前的选项现画，所以 i18n 换了文字、
 * main.js 重新填了选项都不用同步。选中后改 selectedIndex 并派发 input / change 事件。
 */
(function () {
    'use strict';

    const MENU_GAP = 4;
    const VIEWPORT_MARGIN = 8;
    const MENU_MAX_HEIGHT = 300;
    const CLOSE_MS = 140;
    const CHEVRON_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>';
    const CHECK_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';

    // 当前打开的菜单：{ select, host, menu, items, index }
    let active = null;
    let typeahead = '';
    let typeaheadTimer = 0;

    function reducedMotion() {
        return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    }

    function enhanceable(select) {
        if (select.multiple || select.size > 1) return false;
        // 页面里有几个只当数据用的隐藏 select
        return !select.classList.contains('hidden') && select.getAttribute('aria-hidden') !== 'true';
    }

    function enhance(select) {
        if (select.dataset.glSelect === 'true' || !enhanceable(select)) return;
        select.dataset.glSelect = 'true';

        // 顶栏的界面语言按钮自己有外框和箭头图标，直接用它
        if (!select.closest('.language-selector-wrapper')) {
            const host = document.createElement('span');
            host.className = 'gl-select' + (select.classList.contains('w-full') ? ' gl-select-block' : '');
            select.parentNode.insertBefore(host, select);
            host.appendChild(select);
            const chevron = document.createElement('span');
            chevron.className = 'gl-select-chevron';
            chevron.setAttribute('aria-hidden', 'true');
            chevron.innerHTML = CHEVRON_SVG;
            host.appendChild(chevron);
        }
        select.setAttribute('aria-haspopup', 'listbox');
        select.setAttribute('aria-expanded', 'false');
        select.addEventListener('mousedown', onSelectMouseDown);
        select.addEventListener('keydown', onSelectKeyDown);
    }

    function hostOf(select) {
        return select.closest('.gl-select, .language-selector-wrapper') || select;
    }

    function onSelectMouseDown(event) {
        if (event.button !== 0) return;
        const select = event.currentTarget;
        // 拦下系统自带的下拉列表
        event.preventDefault();
        if (select.disabled) return;
        select.focus({ preventScroll: true });
        if (active && active.select === select) {
            close();
        } else {
            open(select);
        }
    }

    function onSelectKeyDown(event) {
        const select = event.currentTarget;
        if (active && active.select === select) {
            onMenuKeyDown(event);
            return;
        }
        const opens = event.key === 'Enter' || event.key === ' ' || event.key === 'F4'
            || (event.altKey && (event.key === 'ArrowDown' || event.key === 'ArrowUp'));
        if (opens && !select.disabled) {
            event.preventDefault();
            open(select);
        }
        // 关着时的上下方向键照旧由浏览器直接换选项
    }

    function buildMenu(select) {
        const menu = document.createElement('div');
        menu.className = 'gl-select-menu';
        menu.setAttribute('role', 'listbox');
        const items = [];
        let currentGroup = null;
        Array.from(select.options).forEach((option, optionIndex) => {
            if (option.hidden) return;
            const group = option.parentElement && option.parentElement.tagName === 'OPTGROUP' ? option.parentElement : null;
            if (group && group !== currentGroup) {
                const groupLabel = document.createElement('div');
                groupLabel.className = 'gl-select-group';
                groupLabel.textContent = group.label;
                menu.appendChild(groupLabel);
            }
            currentGroup = group;

            const item = document.createElement('div');
            item.className = 'gl-select-option';
            item.setAttribute('role', 'option');
            item.dataset.optionIndex = String(optionIndex);
            if (option.disabled || (group && group.disabled)) {
                item.classList.add('disabled');
                item.setAttribute('aria-disabled', 'true');
            }
            const selected = optionIndex === select.selectedIndex;
            item.classList.toggle('selected', selected);
            item.setAttribute('aria-selected', selected ? 'true' : 'false');

            const label = document.createElement('span');
            label.className = 'gl-select-option-label';
            label.textContent = option.textContent;
            const check = document.createElement('span');
            check.className = 'gl-select-check';
            check.setAttribute('aria-hidden', 'true');
            check.innerHTML = CHECK_SVG;
            item.appendChild(label);
            item.appendChild(check);
            menu.appendChild(item);
            items.push(item);
        });

        // 点菜单时焦点留在 select 上，键盘还能接着用
        menu.addEventListener('mousedown', (event) => event.preventDefault());
        menu.addEventListener('click', (event) => {
            const item = event.target.closest('.gl-select-option');
            if (item && !item.classList.contains('disabled')) {
                choose(Number(item.dataset.optionIndex));
            }
        });
        menu.addEventListener('mousemove', (event) => {
            const item = event.target.closest('.gl-select-option');
            if (item && active && !item.classList.contains('disabled')) {
                highlight(active.items.indexOf(item), false);
            }
        });
        return { menu, items };
    }

    function open(select) {
        close(true);
        if (!select.options.length) return;
        const host = hostOf(select);
        const { menu, items } = buildMenu(select);
        document.body.appendChild(menu);
        active = { select, host, menu, items, index: -1 };
        position();
        if (!active) return;

        host.classList.add('open');
        select.setAttribute('aria-expanded', 'true');
        const selectedItem = items.findIndex((item) => item.classList.contains('selected'));
        highlight(selectedItem >= 0 ? selectedItem : firstEnabled(1, -1), true);

        if (reducedMotion()) {
            menu.classList.add('show');
        } else {
            // 先让初始样式生效，再加 show 才有过渡
            menu.getBoundingClientRect();
            menu.classList.add('show');
        }
    }

    function close(immediate) {
        if (!active) return;
        const { select, host, menu } = active;
        active = null;
        host.classList.remove('open');
        select.setAttribute('aria-expanded', 'false');
        if (immediate || reducedMotion()) {
            menu.remove();
            return;
        }
        menu.classList.remove('show');
        menu.classList.add('closing');
        window.setTimeout(() => menu.remove(), CLOSE_MS);
    }

    function choose(optionIndex) {
        if (!active) return;
        const select = active.select;
        close();
        if (optionIndex < 0 || optionIndex === select.selectedIndex) return;
        select.selectedIndex = optionIndex;
        select.dispatchEvent(new Event('input', { bubbles: true }));
        select.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function position() {
        if (!active) return;
        const { host, menu } = active;
        const rect = host.getBoundingClientRect();
        const viewportWidth = document.documentElement.clientWidth;
        const viewportHeight = document.documentElement.clientHeight;
        // 按钮滚出窗口就收起
        if (rect.bottom < 0 || rect.top > viewportHeight || (!rect.width && !rect.height)) {
            close(true);
            return;
        }

        const width = Math.min(Math.max(rect.width, 150), viewportWidth - VIEWPORT_MARGIN * 2);
        menu.style.width = `${width}px`;
        let left = host.classList.contains('language-selector-wrapper') ? rect.right - width : rect.left;
        left = Math.max(VIEWPORT_MARGIN, Math.min(left, viewportWidth - VIEWPORT_MARGIN - width));
        menu.style.left = `${left}px`;

        menu.style.maxHeight = 'none';
        menu.style.overflowY = 'hidden';
        const full = menu.getBoundingClientRect().height;
        const natural = Math.min(full, MENU_MAX_HEIGHT);
        const below = viewportHeight - rect.bottom - MENU_GAP - VIEWPORT_MARGIN;
        const above = rect.top - MENU_GAP - VIEWPORT_MARGIN;
        const dropUp = natural > below && above > below;
        const limit = Math.max(0, Math.min(MENU_MAX_HEIGHT, dropUp ? above : below));
        // 放得下就不限高也不出滚动条（按取整后的高度限高会差零点几像素，多出一根滚动条）
        const height = Math.min(full, limit);
        if (full > limit) {
            menu.style.maxHeight = `${limit}px`;
            menu.style.overflowY = 'auto';
        }
        menu.style.top = `${dropUp ? rect.top - MENU_GAP - height : rect.bottom + MENU_GAP}px`;
        menu.classList.toggle('drop-up', dropUp);
    }

    function firstEnabled(step, from) {
        if (!active) return -1;
        const { items } = active;
        for (let i = from + step; i >= 0 && i < items.length; i += step) {
            if (!items[i].classList.contains('disabled')) return i;
        }
        return -1;
    }

    function highlight(index, scroll) {
        if (!active || index < 0 || index >= active.items.length) return;
        active.items.forEach((item, i) => item.classList.toggle('active', i === index));
        active.index = index;
        if (scroll) {
            const { menu } = active;
            const item = active.items[index];
            if (item.offsetTop < menu.scrollTop) {
                menu.scrollTop = item.offsetTop - 4;
            } else if (item.offsetTop + item.offsetHeight > menu.scrollTop + menu.clientHeight) {
                menu.scrollTop = item.offsetTop + item.offsetHeight - menu.clientHeight + 4;
            }
        }
    }

    function onMenuKeyDown(event) {
        if (!active) return;
        const { items, index } = active;
        switch (event.key) {
            case 'ArrowDown':
            case 'ArrowUp': {
                event.preventDefault();
                const next = firstEnabled(event.key === 'ArrowDown' ? 1 : -1, index);
                if (next >= 0) highlight(next, true);
                return;
            }
            case 'Home':
            case 'PageUp':
                event.preventDefault();
                highlight(firstEnabled(1, -1), true);
                return;
            case 'End':
            case 'PageDown':
                event.preventDefault();
                highlight(firstEnabled(-1, items.length), true);
                return;
            case 'Enter':
            case ' ':
                event.preventDefault();
                if (index >= 0) choose(Number(items[index].dataset.optionIndex));
                return;
            case 'Escape':
            case 'F4':
                event.preventDefault();
                close();
                return;
            case 'Tab':
                close(true);
                return;
            default:
                break;
        }
        // 打字跳到开头相同的那一项
        if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
            event.preventDefault();
            window.clearTimeout(typeaheadTimer);
            typeahead += event.key.toLowerCase();
            typeaheadTimer = window.setTimeout(() => { typeahead = ''; }, 700);
            const match = items.findIndex((item) => !item.classList.contains('disabled')
                && item.textContent.trim().toLowerCase().startsWith(typeahead));
            if (match >= 0) highlight(match, true);
        }
    }

    function init() {
        document.querySelectorAll('select').forEach(enhance);

        document.addEventListener('mousedown', (event) => {
            if (!active) return;
            if (active.menu.contains(event.target) || active.select === event.target) return;
            close();
        }, true);
        document.addEventListener('scroll', (event) => {
            if (active && event.target !== active.menu) position();
        }, true);
        window.addEventListener('resize', () => position());
        window.addEventListener('blur', () => close(true));
        // 切设置页时页面会隐藏，菜单跟着收起
        document.addEventListener('ganlan:page', () => close(true));
    }

    window.glSelectMenu = { enhance, close: () => close(true) };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
