/**
 * 插件：「插件」页（导入、启用 / 禁用、设置、删除）和「软件信息」页的插件列表。
 * - 插件包是 .zip：拖进窗口任何地方都能导入，也可以点「导入插件」选文件
 * - 每个插件的设置项由 plugin.json 的 settings 决定，这里按类型画表单
 * 后端接口在 ui/app.py 的「插件」一节，实际逻辑在 plugin_manager.py。
 */
(function () {
    'use strict';

    const API = '/api/plugins';
    const byId = (id) => document.getElementById(id);
    const t = (key, params) => (window.i18n ? window.i18n.t(key, params || {}) : key);
    const toast = (text, type) => {
        if (typeof showMessage === 'function') showMessage(text, type);
    };
    const INPUT_CLASS = 'box-border w-full h-[36px] px-[11px] bg-[#FFFFFF] [outline:1px_solid_#CBD9C7] [outline-offset:-0.5px] rounded-[4px] text-[13px] leading-normal text-[#12261A] font-[Geist,system-ui,sans-serif] placeholder:text-[#9AAFA1] focus:[outline:1.5px_solid_#1F7A4D]';
    const ICONS = {
        puzzle: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15.39 4.39a1 1 0 0 0 1.68-.474 2.5 2.5 0 1 1 3.014 3.015 1 1 0 0 0-.474 1.68l1.683 1.682a2.414 2.414 0 0 1 0 3.414L19.61 15.39a1 1 0 0 1-1.68-.474 2.5 2.5 0 1 0-3.014 3.015 1 1 0 0 1 .474 1.68l-1.683 1.682a2.414 2.414 0 0 1-3.414 0L8.61 19.61a1 1 0 0 0-1.68.474 2.5 2.5 0 1 1-3.014-3.015 1 1 0 0 0 .474-1.68l-1.683-1.682a2.414 2.414 0 0 1 0-3.414L4.39 8.61a1 1 0 0 1 1.68.474 2.5 2.5 0 1 0 3.014-3.015 1 1 0 0 1-.474-1.68l1.683-1.682a2.414 2.414 0 0 1 3.414 0z"/></svg>',
        gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>',
        trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>',
        upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/></svg>',
    };

    const state = {
        data: null,          // GET /api/plugins 的返回
        open: new Set(),     // 展开了设置的插件 id
        drafts: {},          // id -> 还没保存的设置
        busy: false,
    };

    // ---------- 工具 ----------

    async function requestJson(url, options) {
        const response = await fetch(url, options);
        let data = {};
        try {
            data = await response.json();
        } catch (e) { /* ignore */ }
        if (!response.ok || data.success === false) {
            const error = new Error(data.message || `HTTP ${response.status}`);
            error.status = response.status;
            error.data = data;
            throw error;
        }
        return data;
    }

    function postJson(url, body) {
        return requestJson(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
    }

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }

    function iconNode(name, className) {
        const span = el('span', className || 'plugin-btn-icon');
        span.innerHTML = ICONS[name];
        return span;
    }

    /** plugin.json 里的文字可以是字符串，也可以是 {语言: 文字}。 */
    function local(value) {
        if (!value) return '';
        if (typeof value === 'string') return value;
        const lang = window.i18n && window.i18n.getCurrentLanguage ? window.i18n.getCurrentLanguage() : 'zh-CN';
        const keys = [lang, lang.split('-')[0], 'zh-CN', 'zh', 'en'];
        for (const key of keys) {
            if (value[key]) return value[key];
        }
        const first = Object.values(value).find(Boolean);
        return first || '';
    }

    function pluginName(plugin) {
        return local(plugin.name) || plugin.id;
    }

    function findPlugin(id) {
        return ((state.data && state.data.plugins) || []).find((p) => p.id === id) || null;
    }

    function replacePlugin(info) {
        if (!state.data) return;
        const list = state.data.plugins || [];
        const index = list.findIndex((p) => p.id === info.id);
        if (index >= 0) list[index] = info;
        else list.push(info);
    }

    // ---------- 读取 ----------

    async function loadPlugins(options = {}) {
        try {
            state.data = options.refresh
                ? await postJson(`${API}/refresh`, {})
                : await requestJson(API);
            render();
            if (options.refresh) toast(t('msg.pluginsRefreshed', { count: state.data.plugins.length }), 'success');
        } catch (error) {
            toast(t('msg.pluginLoadFailed') + error.message, 'error');
        }
    }

    // ---------- 「插件」页 ----------

    function statusOf(plugin) {
        return { running: 'running', enabled: 'enabled', disabled: 'disabled', error: 'error' }[plugin.status] || 'disabled';
    }

    function renderSwitch(plugin) {
        const label = el('label', 'gl-switch');
        label.title = t(plugin.enabled ? 'plugins.disableHint' : 'plugins.enableHint');
        const input = el('input');
        input.type = 'checkbox';
        input.checked = !!plugin.enabled;
        input.setAttribute('aria-label', t('plugins.toggle', { name: pluginName(plugin) }));
        input.addEventListener('change', () => setEnabled(plugin.id, input.checked, input));
        label.append(input, el('span', 'gl-switch-track'));
        return label;
    }

    function renderItem(plugin) {
        const item = el('article', 'plugin-item');
        item.dataset.pluginId = plugin.id;
        item.dataset.status = statusOf(plugin);

        const main = el('div', 'plugin-main');
        const icon = el('div', 'plugin-icon');
        if (plugin.has_icon) {
            const img = el('img');
            img.alt = '';
            img.src = `${API}/${encodeURIComponent(plugin.id)}/icon?v=${encodeURIComponent(plugin.version)}`;
            img.addEventListener('error', () => { icon.replaceChildren(iconNode('puzzle', '')); });
            icon.appendChild(img);
        } else {
            icon.appendChild(iconNode('puzzle', ''));
        }

        const body = el('div', 'plugin-body');
        const titleRow = el('div', 'plugin-title-row');
        titleRow.append(
            el('span', 'plugin-name', pluginName(plugin)),
            el('span', 'plugin-version', `v${plugin.version}`),
            el('span', `plugin-badge is-${statusOf(plugin)}`, t(`plugins.status.${statusOf(plugin)}`)),
        );
        body.appendChild(titleRow);

        const meta = [];
        if (local(plugin.author)) meta.push(t('plugins.by', { author: local(plugin.author) }));
        meta.push(plugin.format === 'folder' ? t('plugins.folderFormat', { file: plugin.file }) : plugin.file);
        if (!plugin.has_code) meta.push(t('plugins.noCode'));
        const metaRow = el('div', 'plugin-meta');
        metaRow.textContent = meta.join(' · ');
        if (plugin.homepage) {
            const link = el('a', 'plugin-link', t('plugins.homepage'));
            link.href = plugin.homepage;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            metaRow.append(' · ', link);
        }
        body.appendChild(metaRow);
        if (local(plugin.description)) body.appendChild(el('p', 'plugin-desc', local(plugin.description)));
        if (plugin.error) body.appendChild(el('p', 'plugin-error', plugin.error));

        const actions = el('div', 'plugin-actions');
        const hasSettings = (plugin.settings_schema || []).length > 0;
        const gear = el('button', 'plugin-icon-btn');
        gear.type = 'button';
        gear.appendChild(iconNode('gear'));
        gear.title = hasSettings ? t('btn.pluginSettings') : t('plugins.noSettings');
        gear.setAttribute('aria-label', t('btn.pluginSettings'));
        gear.setAttribute('aria-expanded', state.open.has(plugin.id) ? 'true' : 'false');
        gear.disabled = !hasSettings;
        gear.classList.toggle('active', state.open.has(plugin.id));
        gear.addEventListener('click', () => toggleSettings(plugin.id));
        const remove = el('button', 'plugin-icon-btn is-danger');
        remove.type = 'button';
        remove.appendChild(iconNode('trash'));
        remove.title = t('btn.deletePlugin');
        remove.setAttribute('aria-label', t('btn.deletePlugin'));
        remove.addEventListener('click', () => deletePlugin(plugin.id));
        actions.append(renderSwitch(plugin), gear, remove);

        main.append(icon, body, actions);
        item.appendChild(main);
        if (hasSettings && state.open.has(plugin.id)) item.appendChild(renderSettings(plugin));
        return item;
    }

    function render() {
        const list = byId('plugin-list');
        const dropzone = byId('plugin-dropzone');
        if (!list || !state.data) return;
        const plugins = state.data.plugins || [];
        list.replaceChildren(...plugins.map(renderItem));
        if (dropzone) dropzone.classList.toggle('is-compact', plugins.length > 0);

        const broken = state.data.broken || [];
        const brokenBox = byId('plugin-broken');
        const brokenList = byId('plugin-broken-list');
        if (brokenBox && brokenList) {
            brokenBox.hidden = broken.length === 0;
            brokenList.replaceChildren(...broken.map((b) => {
                const li = el('li');
                li.append(el('span', 'plugin-broken-file', b.file), el('span', 'plugin-broken-error', b.error));
                return li;
            }));
        }
        renderAbout();
    }

    // ---------- 设置表单 ----------

    function draftOf(plugin) {
        if (!state.drafts[plugin.id]) state.drafts[plugin.id] = { ...(plugin.settings || {}) };
        return state.drafts[plugin.id];
    }

    function fieldControl(plugin, field, draft) {
        const id = `plugin-${plugin.id}-${field.key}`.replace(/[^A-Za-z0-9_-]/g, '_');
        const value = draft[field.key];
        let control;
        if (field.type === 'toggle') {
            const label = el('label', 'gl-switch');
            const input = el('input');
            input.type = 'checkbox';
            input.id = id;
            input.checked = !!value;
            input.addEventListener('change', () => { draft[field.key] = input.checked; markDirty(plugin.id); });
            label.append(input, el('span', 'gl-switch-track'));
            return { control: label, id };
        }
        if (field.type === 'select') {
            control = el('select', `${INPUT_CLASS} appearance-none pr-[30px]`);
            (field.options || []).forEach((opt) => {
                const option = el('option', '', local(opt.label) || opt.value);
                option.value = opt.value;
                control.appendChild(option);
            });
            control.value = value;
            control.addEventListener('change', () => { draft[field.key] = control.value; markDirty(plugin.id); });
        } else if (field.type === 'textarea') {
            control = el('textarea', 'word-textarea plugin-textarea');
            control.value = value == null ? '' : String(value);
            control.maxLength = field.max_length || 20000;
            control.addEventListener('input', () => { draft[field.key] = control.value; markDirty(plugin.id); });
        } else {
            control = el('input', INPUT_CLASS);
            control.autocomplete = 'off';
            if (field.type === 'number') {
                control.type = 'number';
                ['min', 'max', 'step'].forEach((name) => {
                    if (typeof field[name] === 'number') control[name] = String(field[name]);
                });
                if (typeof field.step !== 'number') control.step = 'any';
            } else {
                control.type = field.type === 'password' ? 'password' : 'text';
                control.maxLength = field.max_length || 2000;
                control.spellcheck = false;
            }
            control.value = value == null ? '' : String(value);
            control.addEventListener('input', () => {
                draft[field.key] = field.type === 'number'
                    ? (control.value === '' ? null : Number(control.value))
                    : control.value;
                markDirty(plugin.id);
            });
        }
        control.id = id;
        return { control, id };
    }

    function renderSettings(plugin) {
        const box = el('div', 'plugin-settings');
        const draft = draftOf(plugin);
        const grid = el('div', 'plugin-fields');
        (plugin.settings_schema || []).forEach((field) => {
            const row = el('div', `plugin-field is-${field.type}`);
            const { control, id } = fieldControl(plugin, field, draft);
            const label = el('label', 'plugin-field-label', local(field.label) || field.key);
            label.htmlFor = id;
            if (field.type === 'toggle') {
                const line = el('div', 'plugin-toggle-line');
                line.append(control, label);
                row.appendChild(line);
            } else {
                row.append(label, control);
            }
            if (local(field.description)) row.appendChild(el('div', 'plugin-field-hint', local(field.description)));
            grid.appendChild(row);
        });
        box.appendChild(grid);

        const actions = el('div', 'plugin-settings-actions');
        const dirty = el('span', 'word-dirty', t('words.unsaved'));
        dirty.hidden = !isDirty(plugin);
        dirty.dataset.role = 'dirty';
        const reset = el('button', 'plugin-text-btn', t('plugins.resetDefaults'));
        reset.type = 'button';
        reset.addEventListener('click', () => resetDefaults(plugin.id));
        const save = el('button', 'plugin-save-btn', t('plugins.saveSettings'));
        save.type = 'button';
        save.addEventListener('click', () => saveSettings(plugin.id, save));
        actions.append(dirty, reset, save);
        box.appendChild(actions);

        if (window.glSelectMenu) box.querySelectorAll('select').forEach((s) => window.glSelectMenu.enhance(s));
        return box;
    }

    function isDirty(plugin) {
        const draft = state.drafts[plugin.id];
        if (!draft) return false;
        return Object.keys(plugin.settings || {}).some((key) => draft[key] !== plugin.settings[key]);
    }

    function markDirty(id) {
        const plugin = findPlugin(id);
        const item = document.querySelector(`.plugin-item[data-plugin-id="${CSS.escape(id)}"]`);
        const badge = item && item.querySelector('[data-role="dirty"]');
        if (plugin && badge) badge.hidden = !isDirty(plugin);
    }

    function toggleSettings(id) {
        if (state.open.has(id)) {
            state.open.delete(id);
        } else {
            state.open.add(id);
        }
        render();
    }

    function resetDefaults(id) {
        const plugin = findPlugin(id);
        if (!plugin) return;
        const draft = {};
        (plugin.settings_schema || []).forEach((field) => { draft[field.key] = field.default; });
        state.drafts[id] = draft;
        render();
        markDirty(id);
    }

    async function saveSettings(id, button) {
        const plugin = findPlugin(id);
        if (!plugin) return;
        if (button) button.disabled = true;
        try {
            const result = await postJson(`${API}/${encodeURIComponent(id)}/settings`, { settings: draftOf(plugin) });
            replacePlugin(result.plugin);
            delete state.drafts[id];
            render();
            toast(t('msg.pluginSettingsSaved', { name: pluginName(result.plugin) }), 'success');
        } catch (error) {
            toast(t('msg.pluginActionFailed') + error.message, 'error');
            if (button) button.disabled = false;
        }
    }

    // ---------- 启用 / 删除 ----------

    async function setEnabled(id, enabled, input) {
        if (input) input.disabled = true;
        try {
            const result = await postJson(`${API}/${encodeURIComponent(id)}/enabled`, { enabled });
            replacePlugin(result.plugin);
            render();
            const name = pluginName(result.plugin);
            if (result.plugin.status === 'error') {
                toast(t('msg.pluginEnableFailed', { name, error: result.plugin.error }), 'error');
            } else {
                toast(t(enabled ? 'msg.pluginEnabled' : 'msg.pluginDisabled', { name }), 'success');
            }
        } catch (error) {
            toast(t('msg.pluginActionFailed') + error.message, 'error');
            loadPlugins();
        }
    }

    async function deletePlugin(id) {
        const plugin = findPlugin(id);
        if (!plugin) return;
        const name = pluginName(plugin);
        if (!confirm(t('msg.pluginDeleteConfirm', { name }))) return;
        try {
            await requestJson(`${API}/${encodeURIComponent(id)}`, { method: 'DELETE' });
            state.open.delete(id);
            delete state.drafts[id];
            if (state.data) state.data.plugins = state.data.plugins.filter((p) => p.id !== id);
            render();
            toast(t('msg.pluginDeleted', { name }), 'success');
        } catch (error) {
            toast(t('msg.pluginActionFailed') + error.message, 'error');
            loadPlugins();
        }
    }

    // ---------- 导入 ----------

    async function uploadOne(file, overwrite) {
        const form = new FormData();
        form.append('file', file, file.name);
        if (overwrite) form.append('overwrite', '1');
        return requestJson(`${API}/import`, { method: 'POST', body: form });
    }

    async function importFiles(fileList) {
        const files = Array.from(fileList || []);
        if (!files.length || state.busy) return;
        state.busy = true;
        setBusy(true);
        let imported = 0;
        try {
            for (const file of files) {
                if (!/\.zip$/i.test(file.name)) {
                    toast(t('msg.pluginNotZip', { file: file.name }), 'error');
                    continue;
                }
                let result;
                try {
                    result = await uploadOne(file, false);
                } catch (error) {
                    if (error.status !== 409 || !error.data || !error.data.exists) {
                        toast(t('msg.pluginImportFailed', { file: file.name }) + error.message, 'error');
                        continue;
                    }
                    const { existing, incoming } = error.data;
                    const same = existing.version === incoming.version;
                    const ok = confirm(t(same ? 'msg.pluginReinstallConfirm' : 'msg.pluginReplaceConfirm', {
                        name: local(existing.name) || existing.id,
                        old: existing.version,
                        new: incoming.version,
                    }));
                    if (!ok) continue;
                    try {
                        result = await uploadOne(file, true);
                    } catch (error2) {
                        toast(t('msg.pluginImportFailed', { file: file.name }) + error2.message, 'error');
                        continue;
                    }
                }
                const info = result.plugin;
                const replaced = !!findPlugin(info.id);
                replacePlugin(info);
                delete state.drafts[info.id];
                imported += 1;
                const name = pluginName(info);
                if (info.status === 'error') {
                    toast(t('msg.pluginImportedWithError', { name, error: info.error }), 'error');
                } else {
                    toast(t(replaced ? 'msg.pluginUpdated' : 'msg.pluginImported', { name }), 'success');
                }
            }
        } finally {
            state.busy = false;
            setBusy(false);
        }
        if (imported) await loadPlugins();
    }

    function setBusy(busy) {
        const zone = byId('plugin-dropzone');
        if (zone) zone.classList.toggle('is-busy', busy);
    }

    function choosePluginFile() {
        const input = byId('plugin-file-input');
        if (input) input.click();
    }

    // 拖进窗口任何地方都能导入；松手前盖一层提示
    function hasFiles(event) {
        const types = event.dataTransfer && event.dataTransfer.types;
        return !!types && Array.from(types).includes('Files');
    }

    function ensureOverlay() {
        let overlay = byId('plugin-drop-overlay');
        if (overlay) return overlay;
        overlay = el('div', 'plugin-drop-overlay');
        overlay.id = 'plugin-drop-overlay';
        overlay.setAttribute('aria-hidden', 'true');
        const panel = el('div', 'plugin-drop-panel');
        panel.append(iconNode('upload', 'plugin-drop-icon'), el('div', 'plugin-drop-title', t('plugins.overlayTitle')),
            el('div', 'plugin-drop-hint', t('plugins.overlayHint')));
        overlay.appendChild(panel);
        document.body.appendChild(overlay);
        return overlay;
    }

    let dragDepth = 0;

    function showOverlay(show) {
        const overlay = ensureOverlay();
        if (show) {
            overlay.querySelector('.plugin-drop-title').textContent = t('plugins.overlayTitle');
            overlay.querySelector('.plugin-drop-hint').textContent = t('plugins.overlayHint');
        }
        overlay.classList.toggle('show', show);
    }

    function initDragAndDrop() {
        window.addEventListener('dragenter', (event) => {
            if (!hasFiles(event)) return;
            event.preventDefault();
            dragDepth += 1;
            showOverlay(true);
        });
        window.addEventListener('dragover', (event) => {
            if (!hasFiles(event)) return;
            // 不拦的话，文件掉在页面上浏览器会直接打开或下载它
            event.preventDefault();
            event.dataTransfer.dropEffect = 'copy';
        });
        window.addEventListener('dragleave', (event) => {
            if (!hasFiles(event)) return;
            dragDepth = Math.max(0, dragDepth - 1);
            if (dragDepth === 0) showOverlay(false);
        });
        window.addEventListener('drop', (event) => {
            if (!hasFiles(event)) return;
            event.preventDefault();
            dragDepth = 0;
            showOverlay(false);
            const files = event.dataTransfer.files;
            if (!files || !files.length) return;
            if (window.GanlanUiPages) window.GanlanUiPages.setAppPage('plugins', { save: true });
            importFiles(files);
        });
    }

    // ---------- 「软件信息」页 ----------

    function renderAbout() {
        const list = byId('about-plugins');
        if (!list || !state.data) return;
        const plugins = state.data.plugins || [];
        if (!plugins.length) {
            list.replaceChildren(el('li', 'about-plugins-empty', t('about.pluginsEmpty')));
        } else {
            list.replaceChildren(...plugins.map((plugin) => {
                const li = el('li', 'about-plugin');
                const dot = el('span', `about-plugin-dot is-${statusOf(plugin)}`);
                li.append(
                    dot,
                    el('span', 'about-plugin-name', pluginName(plugin)),
                    el('span', 'about-plugin-version', `v${plugin.version}`),
                    el('span', `plugin-badge is-${statusOf(plugin)}`, t(`plugins.status.${statusOf(plugin)}`)),
                );
                return li;
            }));
        }
        const enabled = plugins.filter((p) => p.enabled).length;
        const count = byId('about-plugins-count');
        if (count) count.textContent = t('about.pluginsCountValue', { total: plugins.length, enabled });
        const dir = byId('about-plugins-dir');
        if (dir) {
            dir.textContent = state.data.dir || '-';
            dir.title = state.data.dir || '';
        }
    }

    function showPluginsPage() {
        if (window.GanlanUiPages) window.GanlanUiPages.setAppPage('plugins', { save: true });
    }

    // ---------- 事件 ----------

    function onPage(page) {
        if (page === 'plugins' || page === 'about') loadPlugins();
    }

    function init() {
        const input = byId('plugin-file-input');
        if (input) {
            input.addEventListener('change', () => {
                const files = Array.from(input.files || []);
                input.value = '';
                importFiles(files);
            });
        }
        const zone = byId('plugin-dropzone');
        if (zone) {
            zone.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    choosePluginFile();
                }
            });
        }
        initDragAndDrop();
        document.addEventListener('ganlan:page', (event) => onPage(event.detail && event.detail.page));
        document.addEventListener('i18n:languageChanged', render);
        const current = window.GanlanUiPages && window.GanlanUiPages.getPage ? window.GanlanUiPages.getPage() : '';
        onPage(current);
    }

    window.choosePluginFile = choosePluginFile;
    window.refreshPlugins = () => loadPlugins({ refresh: true });
    window.showPluginsPage = showPluginsPage;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
