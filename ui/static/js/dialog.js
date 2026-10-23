/**
 * 确认框（2026-10-24 岚：删除插件时弹窗风格不对）。
 *
 * 以前用 window.confirm()，在安装版窗口里弹的是 Qt 自带的灰色对话框，跟页面不搭。
 * glConfirm 画在页面里：白底卡片、绿色主按钮（删除类是红色），淡入浮起，样子和安装程序的确认框一样
 * （style.css 第 17 节）。Esc、点卡片外面 = 取消。
 *
 *   if (!(await glConfirm({ title, message, confirmText, danger: true }))) return;
 */
(function () {
    'use strict';

    const t = (key) => (window.i18n ? window.i18n.t(key) : key);
    const reducedMotion = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let current = null;   // 同时只开一个；再开新的，旧的当取消

    function button(text, className) {
        const node = document.createElement('button');
        node.type = 'button';
        node.className = 'gl-dialog-btn ' + className;
        node.textContent = text;
        return node;
    }

    /**
     * options：title、message（\n 换行）、confirmText（默认「确定」）、cancelText（默认「取消」）、
     * danger（确认按钮变红，并且默认聚焦在「取消」上）。返回 Promise<boolean>。
     */
    function glConfirm(options) {
        const opts = typeof options === 'string' ? { message: options } : (options || {});
        if (current) current.finish(false);

        return new Promise((resolve) => {
            const previous = document.activeElement;
            const root = document.createElement('div');
            root.className = 'gl-dialog';
            const card = document.createElement('div');
            card.className = 'gl-dialog-card';
            card.setAttribute('role', 'alertdialog');
            card.setAttribute('aria-modal', 'true');

            if (opts.title) {
                const title = document.createElement('div');
                title.className = 'gl-dialog-title';
                title.id = 'gl-dialog-title';
                title.textContent = opts.title;
                card.setAttribute('aria-labelledby', title.id);
                card.appendChild(title);
            }
            const text = document.createElement('p');
            text.className = 'gl-dialog-text';
            text.id = 'gl-dialog-text';
            text.textContent = opts.message || '';
            card.setAttribute('aria-describedby', text.id);
            card.appendChild(text);

            const actions = document.createElement('div');
            actions.className = 'gl-dialog-buttons';
            const cancel = button(opts.cancelText || t('dialog.cancel'), 'is-secondary');
            const confirm = button(opts.confirmText || t('dialog.confirm'), opts.danger ? 'is-danger' : 'is-primary');
            actions.append(cancel, confirm);
            card.appendChild(actions);
            root.appendChild(card);

            function onKey(event) {
                if (event.key === 'Escape') {
                    event.preventDefault();
                    event.stopPropagation();
                    finish(false);
                } else if (event.key === 'Tab') {
                    // 焦点只在两个按钮之间转
                    event.preventDefault();
                    (document.activeElement === cancel ? confirm : cancel).focus();
                }
            }

            function finish(result) {
                if (!current || current.root !== root) return;
                current = null;
                document.removeEventListener('keydown', onKey, true);
                if (reducedMotion()) {
                    root.remove();
                } else {
                    root.classList.add('is-leaving');
                    root.addEventListener('animationend', () => root.remove(), { once: true });
                    setTimeout(() => root.remove(), 300);
                }
                if (previous && typeof previous.focus === 'function') {
                    try { previous.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
                }
                resolve(result);
            }

            cancel.addEventListener('click', () => finish(false));
            confirm.addEventListener('click', () => finish(true));
            root.addEventListener('mousedown', (event) => {
                if (event.target === root) finish(false);
            });
            document.addEventListener('keydown', onKey, true);
            document.body.appendChild(root);
            current = { root, finish };
            (opts.danger ? cancel : confirm).focus({ preventScroll: true });
        });
    }

    window.glConfirm = glConfirm;
})();
