/**
 * 「词语设置」「软件信息」两页
 * - 识别热词（hot_words）、翻译术语（terminology）：内置的只读展示，网页上改的是自定义那份
 * - 详情：开发者信息、特别鸣谢、运行信息（版本、目录，/api/app-info）
 * 打开对应页面时才拉数据（tabs.js 的 ganlan:page 事件）。
 */
(function () {
    'use strict';

    const API = '/api';
    const byId = (id) => document.getElementById(id);
    const t = (key, params) => (window.i18n ? window.i18n.t(key, params || {}) : key);
    const toast = (text, type) => {
        if (typeof showMessage === 'function') showMessage(text, type);
    };

    async function requestJson(url, options) {
        const response = await fetch(url, options);
        let data = {};
        try {
            data = await response.json();
        } catch (e) { /* ignore */ }
        if (!response.ok || data.success === false) {
            throw new Error(data.message || `HTTP ${response.status}`);
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

    // ---------- 识别热词 ----------

    const hot = {
        data: null,       // /api/words/hot-words 的返回
        lang: 'zh-cn',
        saved: {},        // 语言 -> 服务器上的自定义热词（换行拼接）
        drafts: {},       // 语言 -> 文本框里的内容
    };

    function parseWords(text) {
        const out = [];
        String(text || '').split(/\r?\n/).forEach((line) => {
            const word = line.replace(/\s+/g, ' ').trim();
            if (word && !out.includes(word)) out.push(word);
        });
        return out;
    }

    function hotDraft(lang) {
        return lang in hot.drafts ? hot.drafts[lang] : (hot.saved[lang] || '');
    }

    function hotDirtyLangs() {
        return Object.keys(hot.drafts).filter(
            (lang) => parseWords(hot.drafts[lang]).join('\n') !== (hot.saved[lang] || ''),
        );
    }

    function updateHotMeta() {
        if (!hot.data) return;
        const langs = hot.data.languages || {};
        let total = 0;
        Object.keys(langs).forEach((lang) => {
            total += (langs[lang].builtin || []).length + parseWords(hotDraft(lang)).length;
        });
        const current = langs[hot.lang] || { builtin: [] };
        const countEl = byId('hot-words-count');
        if (countEl) {
            countEl.textContent = t('words.hotCount', {
                custom: parseWords(hotDraft(hot.lang)).length,
                builtin: (current.builtin || []).length,
                total,
                limit: hot.data.limit || 500,
            });
            countEl.classList.toggle('word-over', total > (hot.data.limit || 500));
        }
        const dirty = byId('hot-words-dirty');
        if (dirty) dirty.hidden = hotDirtyLangs().length === 0;
    }

    function renderHot() {
        document.querySelectorAll('#hot-words-tabs [data-hot-lang]').forEach((btn) => {
            const active = btn.dataset.hotLang === hot.lang;
            btn.classList.toggle('active', active);
            btn.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
        const input = byId('hot-words-input');
        if (input) input.value = hotDraft(hot.lang);
        const builtinBox = byId('hot-words-builtin');
        if (builtinBox && hot.data) {
            builtinBox.replaceChildren();
            const builtin = (hot.data.languages[hot.lang] || {}).builtin || [];
            if (!builtin.length) {
                builtinBox.appendChild(el('span', 'word-empty', t('words.builtinEmpty')));
            }
            builtin.forEach((word) => builtinBox.appendChild(el('span', 'word-chip', word)));
        }
        updateHotMeta();
    }

    function applyHotData(data) {
        hot.data = data;
        Object.entries(data.languages || {}).forEach(([lang, item]) => {
            const before = hot.saved[lang];
            hot.saved[lang] = (item.custom || []).join('\n');
            // 没改过的语言跟着服务器走；改过没保存的保留草稿
            if (lang in hot.drafts && parseWords(hot.drafts[lang]).join('\n') === (before || '')) {
                delete hot.drafts[lang];
            }
        });
        renderHot();
    }

    async function loadHotWords() {
        try {
            applyHotData(await requestJson(`${API}/words/hot-words`));
        } catch (error) {
            toast(t('msg.wordsLoadFailed') + error.message, 'error');
        }
    }

    async function saveHotWords() {
        const input = byId('hot-words-input');
        if (input) hot.drafts[hot.lang] = input.value;
        const langs = hotDirtyLangs();
        if (!langs.length) {
            toast(t('msg.wordsNoChanges'), 'success');
            return;
        }
        const button = byId('hot-words-save');
        if (button) button.disabled = true;
        try {
            let last = null;
            for (const lang of langs) {
                last = await postJson(`${API}/words/hot-words`, { lang, words: parseWords(hot.drafts[lang]) });
                hot.saved[lang] = (last.words || []).join('\n');
                delete hot.drafts[lang];
            }
            if (last) applyHotData(last);
            toast(t('msg.hotWordsSaved'), 'success');
        } catch (error) {
            toast(t('msg.wordsSaveFailed') + error.message, 'error');
        } finally {
            if (button) button.disabled = false;
        }
    }

    // ---------- 翻译术语 ----------

    const terms = {
        data: null,
        lang: null,
        saved: {},    // 语言 -> [{keywords: '模型; avatar', instruction}]
        drafts: {},
    };

    function toRows(entries) {
        return (entries || []).map((entry) => ({
            keywords: (entry.keywords || []).join('; '),
            instruction: entry.instruction || '',
        }));
    }

    function normalizeRows(rows) {
        return (rows || [])
            .map((row) => ({
                keywords: String(row.keywords || '').split(/[;；]/).map((k) => k.trim()).filter(Boolean).join('; '),
                instruction: String(row.instruction || '').replace(/\s+/g, ' ').trim(),
            }))
            .filter((row) => row.keywords && row.instruction);
    }

    function termRows(lang) {
        if (!(lang in terms.drafts)) {
            terms.drafts[lang] = (terms.saved[lang] || []).map((row) => ({ ...row }));
        }
        return terms.drafts[lang];
    }

    function termsDirtyLangs() {
        return Object.keys(terms.drafts).filter(
            (lang) => JSON.stringify(normalizeRows(terms.drafts[lang])) !== JSON.stringify(terms.saved[lang] || []),
        );
    }

    function updateTermsDirty() {
        const dirty = byId('terms-dirty');
        if (dirty) dirty.hidden = termsDirtyLangs().length === 0;
    }

    function buildTermRow(rows, row) {
        const line = el('div', 'term-row');
        const keywords = el('input', 'term-input');
        keywords.type = 'text';
        keywords.value = row.keywords;
        keywords.placeholder = t('words.termKeywordsPh');
        keywords.setAttribute('aria-label', t('words.termKeywords'));
        const instruction = el('input', 'term-input');
        instruction.type = 'text';
        instruction.value = row.instruction;
        instruction.placeholder = t('words.termInstructionPh');
        instruction.setAttribute('aria-label', t('words.termInstruction'));
        const remove = el('button', 'term-remove', '×');
        remove.type = 'button';
        remove.title = t('words.termRemove');
        remove.setAttribute('aria-label', t('words.termRemove'));

        keywords.addEventListener('input', () => {
            row.keywords = keywords.value;
            line.classList.remove('is-invalid');
            updateTermsDirty();
        });
        instruction.addEventListener('input', () => {
            row.instruction = instruction.value;
            line.classList.remove('is-invalid');
            updateTermsDirty();
        });
        remove.addEventListener('click', () => {
            const index = rows.indexOf(row);
            if (index >= 0) rows.splice(index, 1);
            renderTerms();
            updateTermsDirty();
        });
        line.append(keywords, instruction, remove);
        return line;
    }

    function renderTerms() {
        if (!terms.data || !terms.lang) return;
        const select = byId('terms-lang');
        if (select && select.value !== terms.lang) select.value = terms.lang;

        const rows = termRows(terms.lang);
        if (!rows.length) rows.push({ keywords: '', instruction: '' });
        const list = byId('terms-list');
        if (list) {
            list.replaceChildren(...rows.map((row) => buildTermRow(rows, row)));
        }

        const builtinBox = byId('terms-builtin');
        if (builtinBox) {
            builtinBox.replaceChildren();
            const builtin = (terms.data.languages[terms.lang] || {}).builtin || [];
            if (!builtin.length) {
                builtinBox.appendChild(el('span', 'word-empty', t('words.builtinEmpty')));
            }
            builtin.forEach((entry) => {
                const item = el('div', 'term-builtin-row');
                item.append(
                    el('span', 'term-builtin-kw', (entry.keywords || []).join(' · ')),
                    el('span', 'term-builtin-inst', entry.instruction || ''),
                );
                builtinBox.appendChild(item);
            });
        }
        updateTermsDirty();
    }

    function defaultTermsLang() {
        // 默认显示自我的译文语言（在列表里的话）
        const select = byId('terms-lang');
        const options = select ? Array.from(select.options).map((o) => o.value) : [];
        const target = (byId('target-language')?.value || '').trim();
        const match = options.find((code) => code.toLowerCase() === target.toLowerCase());
        return match || 'ja';
    }

    function applyTermsData(data) {
        terms.data = data;
        Object.entries(data.languages || {}).forEach(([lang, item]) => {
            const before = JSON.stringify(terms.saved[lang] || []);
            terms.saved[lang] = toRows(item.custom);
            if (lang in terms.drafts && JSON.stringify(normalizeRows(terms.drafts[lang])) === before) {
                delete terms.drafts[lang];
            }
        });
        if (!terms.lang) terms.lang = defaultTermsLang();
        renderTerms();
    }

    async function loadTerms() {
        try {
            applyTermsData(await requestJson(`${API}/words/terms`));
        } catch (error) {
            toast(t('msg.wordsLoadFailed') + error.message, 'error');
        }
    }

    function addTermRow() {
        if (!terms.lang) return;
        const rows = termRows(terms.lang);
        rows.push({ keywords: '', instruction: '' });
        renderTerms();
        const inputs = document.querySelectorAll('#terms-list .term-row:last-child .term-input');
        if (inputs[0]) inputs[0].focus();
    }

    async function saveTerms() {
        const langs = termsDirtyLangs();
        // 只填了一半的行：标红，不保存
        let incomplete = false;
        document.querySelectorAll('#terms-list .term-row').forEach((line, index) => {
            const row = termRows(terms.lang)[index];
            const hasKw = row && String(row.keywords || '').trim();
            const hasInst = row && String(row.instruction || '').trim();
            const bad = Boolean(hasKw) !== Boolean(hasInst);
            line.classList.toggle('is-invalid', bad);
            incomplete = incomplete || bad;
        });
        if (incomplete) {
            toast(t('msg.termIncomplete'), 'warning');
            return;
        }
        if (!langs.length) {
            toast(t('msg.wordsNoChanges'), 'success');
            return;
        }
        const button = byId('terms-save');
        if (button) button.disabled = true;
        try {
            for (const lang of langs) {
                const result = await postJson(`${API}/words/terms`, {
                    lang,
                    entries: normalizeRows(terms.drafts[lang]),
                });
                terms.saved[lang] = toRows(result.entries);
                if (terms.data && terms.data.languages[lang]) {
                    terms.data.languages[lang].custom = result.entries || [];
                }
                delete terms.drafts[lang];
            }
            renderTerms();
            toast(t('msg.termsSaved'), 'success');
        } catch (error) {
            toast(t('msg.wordsSaveFailed') + error.message, 'error');
        } finally {
            if (button) button.disabled = false;
        }
    }

    // ---------- 详情 ----------

    let appInfo = null;

    function renderAbout() {
        if (!appInfo) return;
        const set = (id, value) => {
            const node = byId(id);
            if (node) {
                node.textContent = value || '-';
                node.title = value || '';
            }
        };
        set('about-version', `v${appInfo.version}`);
        set('about-build', t(appInfo.build === 'installed' ? 'about.build.installed' : 'about.build.dev'));
        set('about-program-dir', appInfo.program_dir);
        set('about-data-dir', appInfo.data_dir);
        set('about-os', appInfo.os);
        set('about-python', appInfo.python);
        // 开发版（或者 Uninstall.exe 不在）没有卸载程序，按钮变灰并换说明
        const button = byId('uninstall-btn');
        const hint = byId('uninstall-hint');
        if (button) button.disabled = !appInfo.uninstaller;
        if (hint) {
            hint.dataset.i18n = appInfo.uninstaller ? 'about.uninstallHint' : 'about.uninstallMissing';
            hint.textContent = t(hint.dataset.i18n);
        }
    }

    async function loadAbout() {
        try {
            appInfo = await requestJson(`${API}/app-info`);
            renderAbout();
        } catch (error) {
            toast(t('msg.wordsLoadFailed') + error.message, 'error');
        }
    }

    async function copyDevEmail() {
        const node = byId('dev-email');
        const text = node ? node.textContent.trim() : '';
        let ok = false;
        try {
            ok = Boolean(text) && typeof writeTextToClipboard === 'function' && await writeTextToClipboard(text);
        } catch (e) {
            ok = false;
        }
        toast(t(ok ? 'msg.emailCopied' : 'msg.copyFailed'), ok ? 'success' : 'error');
    }

    async function openAppFolder(key) {
        try {
            await postJson(`${API}/open-folder`, { key });
        } catch (error) {
            toast(t('msg.openFolderFailed') + error.message, 'error');
        }
    }

    async function openUninstaller() {
        // 卸载程序自己会问要不要保留数据、确认后关掉本程序，这里只负责打开它
        const button = byId('uninstall-btn');
        if (button) button.disabled = true;
        try {
            await postJson(`${API}/uninstall`, {});
            toast(t('msg.uninstallerOpened'), 'success');
        } catch (error) {
            toast(t('msg.uninstallerFailed') + error.message, 'error');
        } finally {
            // 在 UAC 里点了「否」或者在卸载程序里取消了，回来还能再点
            setTimeout(() => { if (button) button.disabled = !(appInfo && appInfo.uninstaller); }, 3000);
        }
    }

    // ---------- 事件 ----------

    function onPage(page) {
        if (page === 'words') {
            loadHotWords();
            loadTerms();
        } else if (page === 'about') {
            loadAbout();
        }
    }

    function init() {
        document.querySelectorAll('#hot-words-tabs [data-hot-lang]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const input = byId('hot-words-input');
                if (input) hot.drafts[hot.lang] = input.value;
                hot.lang = btn.dataset.hotLang;
                renderHot();
            });
        });
        const input = byId('hot-words-input');
        if (input) {
            input.addEventListener('input', () => {
                hot.drafts[hot.lang] = input.value;
                updateHotMeta();
            });
        }
        const select = byId('terms-lang');
        if (select) {
            select.addEventListener('change', () => {
                terms.lang = select.value;
                renderTerms();
            });
        }
        document.addEventListener('ganlan:page', (event) => onPage(event.detail && event.detail.page));
        document.addEventListener('i18n:languageChanged', () => {
            renderHot();
            renderTerms();
            renderAbout();
        });
        // tabs.js 先加载，打开网页时的那次切页事件已经发过了
        const current = window.GanlanUiPages && window.GanlanUiPages.getPage ? window.GanlanUiPages.getPage() : '';
        onPage(current);
    }

    window.saveHotWords = saveHotWords;
    window.saveTerms = saveTerms;
    window.addTermRow = addTermRow;
    window.openAppFolder = openAppFolder;
    window.openUninstaller = openUninstaller;
    window.copyDevEmail = copyDevEmail;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
