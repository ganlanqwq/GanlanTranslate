/**
 * 页面里画的对话框（2026-10-24 岚：删除插件时弹窗风格不对）。
 *
 * 以前用 window.confirm()，在安装版窗口里弹的是 Qt 自带的灰色对话框，跟页面不搭。
 * 这里画在页面里：白底卡片、绿色主按钮（删除类是红色），淡入浮起，样子和安装程序的确认框一样
 * （style.css 第 17 节）。Esc、点卡片外面 = 取消。
 *
 *   if (!(await glConfirm({ title, message, confirmText, danger: true }))) return;
 *   await glAlert({ title, message });                       // 只有一个「知道了」
 *   const value = await glDialog({ title, body, buttons });  // 自己放内容和按钮（1.1.2）
 */
(function () {
    'use strict';

    const t = (key) => (window.i18n ? window.i18n.t(key) : key);
    const reducedMotion = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    let current = null;   // 同时只开一个；再开新的，旧的当取消
    let seq = 0;

    // 对话框开着时锁住后面的页面，不让它滚（滚轮落在对话框外面也不会带着整页走）
    function lockScroll(lock) {
        const root = document.documentElement;
        if (lock) {
            if (root.classList.contains('gl-scroll-locked')) return;
            const width = window.innerWidth - root.clientWidth;
            root.style.setProperty('--gl-scrollbar-width', `${Math.max(0, width)}px`);
            root.classList.add('gl-scroll-locked');
        } else {
            root.classList.remove('gl-scroll-locked');
            root.style.removeProperty('--gl-scrollbar-width');
        }
    }

    function makeButton(text, kind) {
        const node = document.createElement('button');
        node.type = 'button';
        node.className = 'gl-dialog-btn is-' + (kind || 'secondary');
        node.textContent = text;
        return node;
    }

    /**
     * options：
     *   title、message（\n 换行）、body（自己的 DOM，放在文字下面）、
     *   buttons：[{ text, value, kind: 'secondary' | 'primary' | 'danger', focus: true }]，从左到右，
     *   cancelValue：Esc / 点外面时返回的值（默认 null），
     *   size：'wide' 宽一点（放长内容），className：加在卡片上。
     * 返回 Promise，值是按下的按钮的 value。
     */
    function glDialog(options) {
        const opts = options || {};
        if (current) current.finish(current.cancelValue);
        const id = ++seq;

        return new Promise((resolve) => {
            const previous = document.activeElement;
            const root = document.createElement('div');
            root.className = 'gl-dialog';
            const card = document.createElement('div');
            card.className = 'gl-dialog-card' + (opts.size === 'wide' ? ' is-wide' : '') + (opts.className ? ' ' + opts.className : '');
            card.setAttribute('role', opts.role || 'dialog');
            card.setAttribute('aria-modal', 'true');

            if (opts.title) {
                const title = document.createElement('div');
                title.className = 'gl-dialog-title';
                title.id = `gl-dialog-title-${id}`;
                title.textContent = opts.title;
                card.setAttribute('aria-labelledby', title.id);
                card.appendChild(title);
            }
            if (opts.message) {
                const text = document.createElement('p');
                text.className = 'gl-dialog-text';
                text.id = `gl-dialog-text-${id}`;
                text.textContent = opts.message;
                card.setAttribute('aria-describedby', text.id);
                card.appendChild(text);
            }
            if (opts.body) {
                const body = document.createElement('div');
                body.className = 'gl-dialog-body';
                body.appendChild(opts.body);
                card.appendChild(body);
            }

            const actions = document.createElement('div');
            actions.className = 'gl-dialog-buttons';
            const buttons = (opts.buttons && opts.buttons.length) ? opts.buttons : [{ text: t('dialog.ok'), value: true, kind: 'primary', focus: true }];
            let focusTarget = null;
            buttons.forEach((spec) => {
                const node = makeButton(spec.text, spec.kind);
                node.addEventListener('click', () => finish(spec.value));
                actions.appendChild(node);
                if (spec.focus) focusTarget = node;
            });
            card.appendChild(actions);
            root.appendChild(card);
            const cancelValue = Object.prototype.hasOwnProperty.call(opts, 'cancelValue') ? opts.cancelValue : null;

            function onKey(event) {
                if (event.key === 'Escape') {
                    event.preventDefault();
                    event.stopPropagation();
                    finish(cancelValue);
                } else if (event.key === 'Tab') {
                    // 焦点只在对话框里转
                    const items = Array.from(card.querySelectorAll(FOCUSABLE)).filter((el) => el.offsetParent !== null);
                    if (!items.length) return;
                    const index = items.indexOf(document.activeElement);
                    event.preventDefault();
                    const next = event.shiftKey ? (index <= 0 ? items.length - 1 : index - 1) : (index + 1) % items.length;
                    items[next].focus();
                }
            }

            function finish(result) {
                if (!current || current.root !== root) return;
                current = null;
                document.removeEventListener('keydown', onKey, true);
                // 紧接着又开一个对话框（比如三次警告）时先不解锁，免得页面闪一下
                setTimeout(() => { if (!current) lockScroll(false); }, 0);
                if (reducedMotion()) {
                    root.remove();
                } else {
                    root.classList.add('is-leaving');
                    root.addEventListener('animationend', () => root.remove(), { once: true });
                    setTimeout(() => root.remove(), 300);
                }
                if (previous && typeof previous.focus === 'function' && document.contains(previous)) {
                    try { previous.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
                }
                resolve(result);
            }

            root.addEventListener('mousedown', (event) => {
                if (event.target === root) finish(cancelValue);
            });
            document.addEventListener('keydown', onKey, true);
            lockScroll(true);
            document.body.appendChild(root);
            current = { root, finish, cancelValue };
            if (typeof opts.onOpen === 'function') {
                try { opts.onOpen(card, finish); } catch (e) { console.error(e); }
            }
            const target = focusTarget || actions.lastElementChild;
            if (target) target.focus({ preventScroll: true });
        });
    }

    /**
     * options：title、message（\n 换行）、confirmText（默认「确定」）、cancelText（默认「取消」）、
     * danger（确认按钮变红，并且默认聚焦在「取消」上）、body。返回 Promise<boolean>。
     */
    function glConfirm(options) {
        const opts = typeof options === 'string' ? { message: options } : (options || {});
        return glDialog({
            title: opts.title,
            message: opts.message,
            body: opts.body,
            role: 'alertdialog',
            cancelValue: false,
            buttons: [
                { text: opts.cancelText || t('dialog.cancel'), value: false, kind: 'secondary', focus: Boolean(opts.danger) },
                { text: opts.confirmText || t('dialog.confirm'), value: true, kind: opts.danger ? 'danger' : 'primary', focus: !opts.danger },
            ],
        }).then((value) => value === true);
    }

    /** 只有一个按钮的提示框。返回 Promise<void>。 */
    function glAlert(options) {
        const opts = typeof options === 'string' ? { message: options } : (options || {});
        return glDialog({
            title: opts.title,
            message: opts.message,
            body: opts.body,
            size: opts.size,
            className: opts.className,
            cancelValue: undefined,
            buttons: [{ text: opts.okText || t('dialog.ok'), value: undefined, kind: 'primary', focus: true }],
        }).then(() => undefined);
    }

    window.glDialog = glDialog;
    window.glConfirm = glConfirm;
    window.glAlert = glAlert;
})();
