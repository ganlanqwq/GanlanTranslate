/**
 * 「个性化」大类（1.1.2，岚：可以对软件进行个性化设定，页面在上行标签栏的插件后）。
 *
 *   配色：预设 + 主题色 / 背景 / 卡片 / 文字 / 首页强调色 / 首页遮罩色，每个都能输十六进制或用调色盘
 *         （调色盘是页面里画的，安装版窗口里 <input type=color> 会弹 Qt 的灰色对话框）。
 *   首页：换图片（选好先裁剪）、重新裁剪、恢复默认图片、遮罩浓度；四段文字。
 *   导出 / 导入 / 恢复默认：个性化配置单独存（数据目录的 personalization.json + personalization\ 图片），
 *         和预设、浏览器里的设置分开；导出是一个 .json（图片用 base64 带上）。
 *
 * 改了马上生效（theme.js 设颜色变量、i18n 的 GL_TEXT_OVERRIDES 换首页文字），停手半秒后存到后端。
 */
(function () {
    'use strict';

    const byId = (id) => document.getElementById(id);
    const t = (key, params) => (window.i18n ? window.i18n.t(key, params) : key);
    const reducedMotion = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const Theme = window.GanlanTheme;
    const MAX_IMAGE_BYTES = 15 * 1024 * 1024;
    const IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/bmp'];

    const EMPTY = {
        theme: { preset: 'default', accent: '', background: '', surface: '', text: '', hero_accent: '', hero_shade: '' },
        hero: { eyebrow: '', title1: '', title2: '', lede: '', overlay: 100 },
        image: { custom: false, rev: 0, original: '' },
    };

    function clone(value) { return JSON.parse(JSON.stringify(value)); }

    function normalize(raw) {
        const out = clone(EMPTY);
        if (!raw || typeof raw !== 'object') return out;
        Object.assign(out.theme, raw.theme || {});
        Object.assign(out.hero, raw.hero || {});
        Object.assign(out.image, raw.image || {});
        return out;
    }

    let settings = normalize(window.GL_PERSONALIZATION);

    const COLOR_ROWS = [
        { key: 'accent', label: 'theme.accent', hint: 'theme.accentHint', varName: '--green-600' },
        { key: 'background', label: 'theme.background', hint: 'theme.followHint', varName: '--bg' },
        { key: 'surface', label: 'theme.surface', hint: 'theme.followHint', varName: '--surface' },
        { key: 'text', label: 'theme.text', hint: 'theme.followHint', varName: '--ink' },
        { key: 'hero_accent', label: 'theme.heroAccent', hint: 'theme.heroAccentHint', varName: '--hero-accent' },
        { key: 'hero_shade', label: 'theme.heroShade', hint: 'theme.heroShadeHint', varName: '--hero-shade' },
    ];

    const TEXT_FIELDS = [
        { key: 'eyebrow', i18n: 'hero.issue', label: 'heroEdit.eyebrow', max: 60 },
        { key: 'title1', i18n: 'hero.line1', label: 'heroEdit.title1', max: 40 },
        { key: 'title2', i18n: 'hero.line2', label: 'heroEdit.title2', max: 40 },
        { key: 'lede', i18n: 'hero.lede', label: 'heroEdit.lede', max: 240, multiline: true },
    ];

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }

    function toast(message, type = 'success') {
        if (typeof window.showMessage === 'function') window.showMessage(message, type);
    }

    // ---------- 保存 ----------

    let saveTimer = null;
    let saving = Promise.resolve();

    function scheduleSave() {
        clearTimeout(saveTimer);
        saveTimer = setTimeout(save, 500);
    }

    function save() {
        clearTimeout(saveTimer);
        saveTimer = null;
        const body = JSON.stringify({ theme: settings.theme, hero: settings.hero });
        saving = saving.then(async () => {
            try {
                const resp = await fetch('/api/personalization', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body,
                });
                const data = await resp.json();
                if (!data.success) throw new Error(data.message || resp.status);
            } catch (err) {
                toast(t('personalize.saveFailed', { error: err.message || err }), 'error');
            }
        });
        return saving;
    }

    // ---------- 配色 ----------

    function effectiveVars() {
        return Theme.compute(settings.theme);
    }

    // 换配色时颜色慢慢变过去（theme.css 里注册的颜色变量 + gl-theme-animating 上的 transition）
    let themeAnimTimer = null;

    function animateThemeChange() {
        if (reducedMotion()) return;
        const root = document.documentElement;
        root.classList.add('gl-theme-animating');
        clearTimeout(themeAnimTimer);
        themeAnimTimer = setTimeout(() => root.classList.remove('gl-theme-animating'), 650);
    }

    function applyTheme() {
        animateThemeChange();
        Theme.apply(settings.theme);
        renderColorValues();
        renderPresetState();
        renderContrast();
    }

    function presetOf(accent) {
        const hex = Theme.normalizeHex(accent) || Theme.DEFAULT_ACCENT;
        return Theme.PRESETS.find((preset) => preset.accent === hex) || null;
    }

    function renderPresets() {
        const box = byId('theme-presets');
        if (!box) return;
        box.replaceChildren(...Theme.PRESETS.map((preset) => {
            const btn = el('button', 'theme-preset');
            btn.type = 'button';
            btn.dataset.preset = preset.id;
            btn.setAttribute('role', 'radio');
            const vars = Theme.compute({ accent: preset.accent });
            const dot = el('span', 'theme-preset-dot');
            dot.style.background = `linear-gradient(135deg, ${vars['--green-600']} 0 50%, ${vars['--green-50']} 50% 100%)`;
            dot.style.boxShadow = `inset 0 0 0 1px ${vars['--line-strong']}`;
            btn.append(dot, el('span', 'theme-preset-name', t(`theme.preset.${preset.id}`)));
            btn.addEventListener('click', () => {
                settings.theme.accent = preset.id === 'default' ? '' : preset.accent;
                settings.theme.preset = preset.id;
                applyTheme();
                scheduleSave();
            });
            return btn;
        }));
        renderPresetState();
    }

    function renderPresetState() {
        const active = presetOf(settings.theme.accent);
        document.querySelectorAll('#theme-presets .theme-preset').forEach((btn) => {
            const on = Boolean(active) && btn.dataset.preset === active.id;
            btn.classList.toggle('active', on);
            btn.setAttribute('aria-checked', on ? 'true' : 'false');
        });
    }

    function setColor(key, hex) {
        settings.theme[key] = hex ? Theme.normalizeHex(hex) : '';
        if (key === 'accent') {
            const preset = presetOf(settings.theme.accent);
            settings.theme.preset = preset ? preset.id : 'custom';
            if (settings.theme.accent === Theme.DEFAULT_ACCENT) settings.theme.accent = '';
        }
        applyTheme();
        scheduleSave();
    }

    function renderColors() {
        const box = byId('theme-colors');
        if (!box) return;
        box.replaceChildren(...COLOR_ROWS.map((row) => {
            const item = el('div', 'theme-color-row');
            item.dataset.key = row.key;
            const text = el('div', 'theme-color-text');
            text.append(el('div', 'theme-color-label', t(row.label)), el('div', 'theme-color-hint', t(row.hint)));
            const controls = el('div', 'theme-color-controls');
            const swatch = el('button', 'theme-swatch');
            swatch.type = 'button';
            swatch.setAttribute('aria-label', t('theme.openPicker', { name: t(row.label) }));
            swatch.setAttribute('aria-haspopup', 'dialog');
            const input = el('input', 'theme-hex');
            input.type = 'text';
            input.maxLength = 7;
            input.spellcheck = false;
            input.autocomplete = 'off';
            input.setAttribute('aria-label', t(row.label));
            const reset = el('button', 'theme-color-reset');
            reset.type = 'button';
            reset.title = t('theme.resetColor');
            reset.setAttribute('aria-label', t('theme.resetColor'));
            reset.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>';
            controls.append(swatch, input, reset);
            item.append(text, controls);

            swatch.addEventListener('click', () => {
                const start = Theme.normalizeHex(settings.theme[row.key]) || effectiveVars()[row.varName];
                openPicker(swatch, start, (hex) => setColor(row.key, hex));
            });
            input.addEventListener('input', () => {
                const value = input.value.trim();
                const hex = Theme.normalizeHex(value.startsWith('#') ? value : '#' + value);
                input.classList.toggle('is-invalid', Boolean(value) && !hex);
                if (hex && value.replace('#', '').length === 6) setColor(row.key, hex);
            });
            input.addEventListener('blur', () => { input.classList.remove('is-invalid'); renderColorValues(); });
            input.addEventListener('keydown', (event) => { if (event.key === 'Enter') input.blur(); });
            reset.addEventListener('click', () => setColor(row.key, ''));
            return item;
        }));
        renderColorValues();
    }

    function renderColorValues() {
        const vars = effectiveVars();
        COLOR_ROWS.forEach((row) => {
            const item = document.querySelector(`#theme-colors .theme-color-row[data-key="${row.key}"]`);
            if (!item) return;
            const custom = Boolean(Theme.normalizeHex(settings.theme[row.key]));
            const shown = Theme.normalizeHex(settings.theme[row.key]) || vars[row.varName];
            item.querySelector('.theme-swatch').style.setProperty('--swatch', shown);
            const input = item.querySelector('.theme-hex');
            if (document.activeElement !== input) input.value = custom ? shown : '';
            input.placeholder = shown;
            item.classList.toggle('is-custom', custom);
            item.querySelector('.theme-color-reset').disabled = !custom;
        });
    }

    function renderContrast() {
        const box = byId('theme-contrast');
        if (!box) return;
        const vars = effectiveVars();
        const textRatio = Theme.contrast(vars['--ink'], vars['--surface']);
        const buttonRatio = Theme.contrast(vars['--on-accent'], vars['--green-600']);
        const low = textRatio < 4.5 ? textRatio : (buttonRatio < 3 ? buttonRatio : 0);
        box.hidden = !low;
        if (low) box.textContent = t('theme.lowContrast', { ratio: low.toFixed(1) });
    }

    // ---------- 调色盘 ----------

    let picker = null;

    function hsvToHex(h, s, v) {
        const f = (n) => {
            const k = (n + h / 60) % 6;
            return v - v * s * Math.max(0, Math.min(k, 4 - k, 1));
        };
        return Theme.toHex([f(5) * 255, f(3) * 255, f(1) * 255]);
    }

    function hexToHsv(hex) {
        const [r, g, b] = (Theme.parseHex(hex) || [31, 122, 77]).map((v) => v / 255);
        const max = Math.max(r, g, b);
        const d = max - Math.min(r, g, b);
        let h = 0;
        if (d) {
            if (max === r) h = ((g - b) / d) % 6;
            else if (max === g) h = (b - r) / d + 2;
            else h = (r - g) / d + 4;
            h *= 60;
            if (h < 0) h += 360;
        }
        return { h, s: max ? d / max : 0, v: max };
    }

    function closePicker() {
        if (!picker) return;
        const { node, cleanup } = picker;
        picker = null;
        cleanup();
        if (reducedMotion()) {
            node.remove();
        } else {
            node.classList.add('is-leaving');
            setTimeout(() => node.remove(), 160);
        }
    }

    function openPicker(anchor, startHex, onChange) {
        if (picker && picker.anchor === anchor) { closePicker(); return; }
        closePicker();
        let hsv = hexToHsv(startHex);
        const node = el('div', 'gl-color-popover');
        node.setAttribute('role', 'dialog');
        node.setAttribute('aria-label', t('theme.pickerTitle'));
        const sv = el('div', 'gcp-sv');
        sv.tabIndex = 0;
        sv.setAttribute('aria-label', t('theme.pickerSv'));
        const svThumb = el('span', 'gcp-sv-thumb');
        sv.append(svThumb);
        const hue = el('div', 'gcp-hue');
        hue.tabIndex = 0;
        hue.setAttribute('aria-label', t('theme.pickerHue'));
        const hueThumb = el('span', 'gcp-hue-thumb');
        hue.append(hueThumb);
        const row = el('div', 'gcp-row');
        const preview = el('span', 'gcp-preview');
        const hexInput = el('input', 'gcp-hex');
        hexInput.type = 'text';
        hexInput.maxLength = 7;
        hexInput.spellcheck = false;
        hexInput.setAttribute('aria-label', 'HEX');
        row.append(preview, hexInput);
        const swatches = el('div', 'gcp-swatches');
        const palette = Theme.PRESETS.map((p) => p.accent).concat(['#0A2115', '#12261A', '#FBFCFA', '#FFFFFF', '#8FDCB4', '#F2C14E']);
        palette.forEach((hex) => {
            const b = el('button', 'gcp-swatch');
            b.type = 'button';
            b.style.background = hex;
            b.title = hex;
            b.addEventListener('click', () => { hsv = hexToHsv(hex); update(true); });
            swatches.append(b);
        });
        node.append(sv, hue, row, swatches);
        document.body.append(node);

        function update(emit) {
            const hex = hsvToHex(hsv.h, hsv.s, hsv.v);
            sv.style.setProperty('--hue', hsvToHex(hsv.h, 1, 1));
            svThumb.style.left = `${hsv.s * 100}%`;
            svThumb.style.top = `${(1 - hsv.v) * 100}%`;
            svThumb.style.background = hex;
            hueThumb.style.left = `${(hsv.h / 360) * 100}%`;
            hueThumb.style.background = hsvToHex(hsv.h, 1, 1);
            preview.style.background = hex;
            if (document.activeElement !== hexInput) hexInput.value = hex;
            if (emit) onChange(hex);
        }

        function drag(target, apply) {
            target.addEventListener('pointerdown', (event) => {
                event.preventDefault();
                target.setPointerCapture(event.pointerId);
                target.focus({ preventScroll: true });
                const move = (e) => {
                    const rect = target.getBoundingClientRect();
                    apply(Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)),
                        Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height)));
                    update(true);
                };
                move(event);
                const up = () => {
                    target.removeEventListener('pointermove', move);
                    target.removeEventListener('pointerup', up);
                    target.removeEventListener('pointercancel', up);
                };
                target.addEventListener('pointermove', move);
                target.addEventListener('pointerup', up);
                target.addEventListener('pointercancel', up);
            });
        }
        drag(sv, (x, y) => { hsv.s = x; hsv.v = 1 - y; });
        drag(hue, (x) => { hsv.h = Math.min(359.9, x * 360); });
        sv.addEventListener('keydown', (event) => {
            const step = event.shiftKey ? 0.1 : 0.02;
            const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
            if (!moves[event.key]) return;
            event.preventDefault();
            hsv.s = Math.min(1, Math.max(0, hsv.s + moves[event.key][0]));
            hsv.v = Math.min(1, Math.max(0, hsv.v + moves[event.key][1]));
            update(true);
        });
        hue.addEventListener('keydown', (event) => {
            const step = event.shiftKey ? 15 : 3;
            if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
            event.preventDefault();
            hsv.h = (hsv.h + (event.key === 'ArrowLeft' ? -step : step) + 360) % 360;
            update(true);
        });
        hexInput.addEventListener('input', () => {
            const value = hexInput.value.trim();
            const hex = Theme.normalizeHex(value.startsWith('#') ? value : '#' + value);
            hexInput.classList.toggle('is-invalid', Boolean(value) && !hex);
            if (hex && value.replace('#', '').length === 6) { hsv = hexToHsv(hex); update(true); }
        });
        hexInput.addEventListener('keydown', (event) => { if (event.key === 'Enter') closePicker(); });

        function position() {
            const rect = anchor.getBoundingClientRect();
            const w = node.offsetWidth;
            const h = node.offsetHeight;
            let left = Math.min(window.innerWidth - w - 12, Math.max(12, rect.left + rect.width / 2 - w / 2));
            let top = rect.bottom + 8;
            node.classList.toggle('is-above', top + h > window.innerHeight - 12);
            if (top + h > window.innerHeight - 12) top = Math.max(12, rect.top - h - 8);
            node.style.left = `${left}px`;
            node.style.top = `${top}px`;
        }
        const onDown = (event) => {
            if (!node.contains(event.target) && !anchor.contains(event.target)) closePicker();
        };
        const onKey = (event) => {
            if (event.key === 'Escape') { event.stopPropagation(); closePicker(); anchor.focus(); }
        };
        document.addEventListener('mousedown', onDown, true);
        document.addEventListener('keydown', onKey, true);
        window.addEventListener('resize', position);
        window.addEventListener('scroll', position, true);
        picker = {
            node,
            anchor,
            cleanup: () => {
                document.removeEventListener('mousedown', onDown, true);
                document.removeEventListener('keydown', onKey, true);
                window.removeEventListener('resize', position);
                window.removeEventListener('scroll', position, true);
            },
        };
        update(false);
        position();
        sv.focus({ preventScroll: true });
    }

    // ---------- 首页文字 ----------

    function applyTextOverrides() {
        window.GL_TEXT_OVERRIDES = {
            'hero.issue': settings.hero.eyebrow || '',
            'hero.line1': settings.hero.title1 || '',
            'hero.line2': settings.hero.title2 || '',
            'hero.lede': settings.hero.lede || '',
        };
        if (window.i18n) window.i18n.applyTranslations();
    }

    // 默认文字要直接拿 i18n 的（不经过覆盖），当输入框的 placeholder
    function defaultText(key) {
        return t(key);
    }

    function renderTextFields() {
        const box = byId('hero-text-fields');
        if (!box) return;
        box.replaceChildren(...TEXT_FIELDS.map((field) => {
            const group = el('div', 'hero-text-field');
            const head = el('div', 'hero-text-head');
            const label = el('label', 'hero-text-label', t(field.label));
            label.htmlFor = `hero-text-${field.key}`;
            const count = el('span', 'hero-text-count');
            head.append(label, count);
            const input = el(field.multiline ? 'textarea' : 'input', 'hero-text-input');
            input.id = `hero-text-${field.key}`;
            if (!field.multiline) input.type = 'text';
            else input.rows = 3;
            input.maxLength = field.max;
            input.placeholder = defaultText(field.i18n);
            input.value = settings.hero[field.key] || '';
            const updateCount = () => { count.textContent = `${input.value.length}/${field.max}`; };
            updateCount();
            input.addEventListener('input', () => {
                settings.hero[field.key] = field.multiline ? input.value : input.value.replace(/\n/g, ' ');
                updateCount();
                applyTextOverrides();
                scheduleSave();
            });
            group.append(head, input);
            return group;
        }));
    }

    // ---------- 遮罩、图片 ----------

    function applyOverlay() {
        const value = Number(settings.hero.overlay);
        const ratio = Number.isFinite(value) ? value / 100 : 1;
        document.querySelectorAll('.hero, #hero-preview, #theme-preview').forEach((node) => node.style.setProperty('--hero-overlay', String(ratio)));
        const slider = byId('hero-overlay');
        if (slider && document.activeElement !== slider) slider.value = String(value);
        const label = byId('hero-overlay-value');
        if (label) label.textContent = `${Math.round(value)}%`;
    }

    function heroImageUrl() {
        if (settings.image.custom) return `/api/personalization/hero-image?rev=${settings.image.rev}`;
        return byId('hero-bg')?.dataset.defaultSrc || '/static/img/hero-bg.jpg';
    }

    function applyHeroImage(animate) {
        const url = heroImageUrl();
        const position = settings.image.custom ? 'center center' : 'center 32%';
        const bg = byId('hero-bg');
        const set = (node) => {
            if (!node) return;
            node.style.backgroundImage = `url('${url}')`;
            node.style.backgroundPosition = position;
        };
        if (bg) {
            if (animate && !reducedMotion() && bg.animate) {
                const img = new Image();
                img.onload = () => {
                    set(bg);
                    bg.animate([{ opacity: 0.2, filter: 'blur(6px)' }, { opacity: 1, filter: 'none' }], { duration: 520, easing: 'ease-out' });
                };
                img.src = url;
            } else {
                set(bg);
            }
        }
        set(byId('hero-preview-bg'));
        document.querySelectorAll('.theme-preview-hero-bg').forEach(set);
        const recrop = byId('hero-image-recrop');
        if (recrop) recrop.disabled = !(settings.image.custom && settings.image.original);
        const def = byId('hero-image-default');
        if (def) def.disabled = !settings.image.custom;
    }

    async function uploadHeroImage(blob, original) {
        const form = new FormData();
        form.append('image', blob, 'hero.jpg');
        if (original) form.append('original', original, original.name || 'original');
        const resp = await fetch('/api/personalization/hero-image', { method: 'POST', body: form });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data.success) throw new Error(data.message || `HTTP ${resp.status}`);
        settings.image = normalize(data.settings).image;
        applyHeroImage(true);
    }

    async function handleImageFile(file) {
        if (!file) return;
        if (!IMAGE_TYPES.includes(file.type)) { toast(t('heroEdit.badType'), 'error'); return; }
        if (file.size > MAX_IMAGE_BYTES) { toast(t('heroEdit.tooLarge'), 'error'); return; }
        const url = URL.createObjectURL(file);
        try {
            const blob = await openCropper(url);
            if (!blob) return;
            await uploadHeroImage(blob, file);
            toast(t('heroEdit.saved'));
        } catch (err) {
            toast(t('heroEdit.saveFailed', { error: err.message || err }), 'error');
        } finally {
            URL.revokeObjectURL(url);
        }
    }

    async function recrop() {
        try {
            const blob = await openCropper(`/api/personalization/hero-image?original=1&rev=${settings.image.rev}`);
            if (!blob) return;
            await uploadHeroImage(blob, null);
            toast(t('heroEdit.saved'));
        } catch (err) {
            toast(t('heroEdit.saveFailed', { error: err.message || err }), 'error');
        }
    }

    async function useDefaultImage() {
        const ok = await window.glConfirm({ title: t('heroEdit.useDefault'), message: t('heroEdit.useDefaultConfirm'), confirmText: t('heroEdit.useDefault') });
        if (!ok) return;
        try {
            const resp = await fetch('/api/personalization/hero-image', { method: 'DELETE' });
            const data = await resp.json();
            if (!data.success) throw new Error(data.message);
            settings.image = normalize(data.settings).image;
            applyHeroImage(true);
        } catch (err) {
            toast(t('heroEdit.saveFailed', { error: err.message || err }), 'error');
        }
    }

    // ---------- 裁剪 ----------

    const ASPECTS = [
        { id: 'banner', ratio: 5 / 2, label: 'crop.aspectBanner' },
        { id: 'wide', ratio: 16 / 9, label: 'crop.aspectWide' },
        { id: 'ultra', ratio: 21 / 9, label: 'crop.aspectUltra' },
    ];
    const OUTPUT_MAX_WIDTH = 2560;

    function loadImage(src) {
        return new Promise((resolve, reject) => {
            const img = new Image();
            img.onload = () => resolve(img);
            img.onerror = () => reject(new Error(t('heroEdit.loadFailed')));
            img.src = src;
        });
    }

    async function openCropper(src) {
        const img = await loadImage(src);
        const state = { aspect: ASPECTS[0], zoom: 1, x: 0, y: 0, frame: null, base: 1 };

        const body = el('div', 'cropper');
        const stage = el('div', 'cropper-stage');
        stage.tabIndex = 0;
        stage.setAttribute('aria-label', t('crop.stageLabel'));
        const picture = el('img', 'cropper-image');
        picture.src = img.src;
        picture.alt = '';
        picture.draggable = false;
        const frame = el('div', 'cropper-frame');
        frame.append(el('span', 'cropper-grid'));
        stage.append(picture, frame);

        const controls = el('div', 'cropper-controls');
        const aspects = el('div', 'cropper-aspects');
        aspects.setAttribute('role', 'group');
        ASPECTS.forEach((aspect) => {
            const b = el('button', 'cropper-aspect', t(aspect.label));
            b.type = 'button';
            b.dataset.aspect = aspect.id;
            b.addEventListener('click', () => {
                if (state.aspect === aspect) return;
                state.aspect = aspect;
                stage.classList.add('is-animating');
                layout(true);
                setTimeout(() => stage.classList.remove('is-animating'), 320);
            });
            aspects.append(b);
        });
        const zoomWrap = el('label', 'cropper-zoom');
        zoomWrap.append(el('span', 'cropper-zoom-label', t('crop.zoom')));
        const zoom = el('input', 'gl-range');
        zoom.type = 'range';
        zoom.min = '1';
        zoom.max = '4';
        zoom.step = '0.01';
        zoom.value = '1';
        zoomWrap.append(zoom);
        const fit = el('button', 'cropper-fit', t('crop.fit'));
        fit.type = 'button';
        controls.append(aspects, zoomWrap, fit);
        const hint = el('p', 'cropper-hint', t('crop.hint', { w: img.naturalWidth, h: img.naturalHeight }));
        body.append(stage, controls, hint);

        function scale() { return state.base * state.zoom; }

        function clampPosition() {
            const f = state.frame;
            const w = img.naturalWidth * scale();
            const h = img.naturalHeight * scale();
            state.x = Math.min(f.x, Math.max(f.x + f.w - w, state.x));
            state.y = Math.min(f.y, Math.max(f.y + f.h - h, state.y));
        }

        function paint() {
            clampPosition();
            const s = scale();
            picture.style.width = `${img.naturalWidth * s}px`;
            picture.style.height = `${img.naturalHeight * s}px`;
            picture.style.transform = `translate(${state.x}px, ${state.y}px)`;
            zoom.value = String(state.zoom);
            aspects.querySelectorAll('.cropper-aspect').forEach((b) => b.classList.toggle('active', b.dataset.aspect === state.aspect.id));
        }

        // 换比例 / 第一次：框放在中间，图片铺满框
        function layout(keepCenter) {
            const W = stage.clientWidth;
            const H = stage.clientHeight;
            if (!W || !H) return;
            const pad = 22;
            let fw = W - pad * 2;
            let fh = fw / state.aspect.ratio;
            if (fh > H - pad * 2) { fh = H - pad * 2; fw = fh * state.aspect.ratio; }
            const prev = state.frame;
            let cx = 0.5;
            let cy = 0.5;
            if (keepCenter && prev) {
                const s = scale();
                cx = (prev.x + prev.w / 2 - state.x) / (img.naturalWidth * s);
                cy = (prev.y + prev.h / 2 - state.y) / (img.naturalHeight * s);
            }
            state.frame = { x: (W - fw) / 2, y: (H - fh) / 2, w: fw, h: fh };
            frame.style.left = `${state.frame.x}px`;
            frame.style.top = `${state.frame.y}px`;
            frame.style.width = `${fw}px`;
            frame.style.height = `${fh}px`;
            state.base = Math.max(fw / img.naturalWidth, fh / img.naturalHeight);
            const s = scale();
            state.x = state.frame.x + fw / 2 - cx * img.naturalWidth * s;
            state.y = state.frame.y + fh / 2 - cy * img.naturalHeight * s;
            paint();
        }

        function zoomTo(value, cx, cy) {
            const f = state.frame;
            const px = cx ?? (f.x + f.w / 2);
            const py = cy ?? (f.y + f.h / 2);
            const before = scale();
            state.zoom = Math.min(4, Math.max(1, value));
            const after = scale();
            state.x = px - (px - state.x) * (after / before);
            state.y = py - (py - state.y) * (after / before);
            paint();
        }

        stage.addEventListener('pointerdown', (event) => {
            event.preventDefault();
            stage.setPointerCapture(event.pointerId);
            stage.classList.add('is-dragging');
            const start = { x: event.clientX, y: event.clientY, ix: state.x, iy: state.y };
            const move = (e) => {
                state.x = start.ix + e.clientX - start.x;
                state.y = start.iy + e.clientY - start.y;
                paint();
            };
            const up = () => {
                stage.classList.remove('is-dragging');
                stage.removeEventListener('pointermove', move);
                stage.removeEventListener('pointerup', up);
                stage.removeEventListener('pointercancel', up);
            };
            stage.addEventListener('pointermove', move);
            stage.addEventListener('pointerup', up);
            stage.addEventListener('pointercancel', up);
        });
        stage.addEventListener('wheel', (event) => {
            event.preventDefault();
            const rect = stage.getBoundingClientRect();
            zoomTo(state.zoom * (event.deltaY < 0 ? 1.08 : 1 / 1.08), event.clientX - rect.left, event.clientY - rect.top);
        }, { passive: false });
        stage.addEventListener('keydown', (event) => {
            const step = event.shiftKey ? 40 : 10;
            const moves = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
            if (moves[event.key]) {
                event.preventDefault();
                state.x += moves[event.key][0];
                state.y += moves[event.key][1];
                paint();
            } else if (event.key === '+' || event.key === '=') {
                zoomTo(state.zoom * 1.1);
            } else if (event.key === '-') {
                zoomTo(state.zoom / 1.1);
            }
        });
        zoom.addEventListener('input', () => zoomTo(Number(zoom.value)));
        fit.addEventListener('click', () => { state.zoom = 1; state.frame = null; layout(false); });

        const result = await window.glDialog({
            title: t('crop.title'),
            body,
            size: 'wide',
            className: 'gl-cropper-dialog',
            cancelValue: false,
            buttons: [
                { text: t('dialog.cancel'), value: false, kind: 'secondary' },
                { text: t('crop.confirm'), value: true, kind: 'primary', focus: true },
            ],
            // 对话框已经在页面上了，马上排一次；下一帧（卡片浮起动画开始后）尺寸稳定了再排一次
            onOpen: () => {
                layout(false);
                stage.focus({ preventScroll: true });
                requestAnimationFrame(() => { if (state.zoom === 1) layout(false); });
            },
        });
        if (!result || !state.frame) return null;

        const s = scale();
        const sx = (state.frame.x - state.x) / s;
        const sy = (state.frame.y - state.y) / s;
        const sw = state.frame.w / s;
        const sh = state.frame.h / s;
        const outW = Math.round(Math.min(OUTPUT_MAX_WIDTH, sw));
        const outH = Math.round(outW / state.aspect.ratio);
        const canvas = document.createElement('canvas');
        canvas.width = outW;
        canvas.height = outH;
        const ctx = canvas.getContext('2d');
        ctx.imageSmoothingQuality = 'high';
        ctx.fillStyle = '#0A2115';
        ctx.fillRect(0, 0, outW, outH);
        ctx.drawImage(img, sx, sy, sw, sh, 0, 0, outW, outH);
        return new Promise((resolve) => canvas.toBlob((blob) => resolve(blob), 'image/jpeg', 0.9));
    }

    // ---------- 导出 / 导入 / 恢复默认 ----------

    async function exportConfig() {
        try {
            if (saveTimer) await save();
            await saving;
            const resp = await fetch('/api/personalization/export');
            const data = await resp.json();
            if (!data.success) throw new Error(data.message || resp.status);
            window.downloadTextFile(t('personalize.exportFilename'), JSON.stringify(data.data, null, 2), 'application/json');
            toast(t('personalize.exported'));
        } catch (err) {
            toast(t('personalize.exportFailed', { error: err.message || err }), 'error');
        }
    }

    async function importConfig(file) {
        if (!file) return;
        let parsed;
        try {
            parsed = JSON.parse(await file.text());
        } catch (e) {
            toast(t('personalize.importBadFile'), 'error');
            return;
        }
        const ok = await window.glConfirm({ title: t('personalize.import'), message: t('personalize.importConfirm'), confirmText: t('personalize.import') });
        if (!ok) return;
        try {
            const resp = await fetch('/api/personalization/import', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(parsed),
            });
            const data = await resp.json();
            if (!data.success) throw new Error(data.message || resp.status);
            settings = normalize(data.settings);
            applyAll(true);
            toast(t('personalize.imported'));
        } catch (err) {
            toast(t('personalize.importFailed', { error: err.message || err }), 'error');
        }
    }

    async function resetAll() {
        const ok = await window.glConfirm({ title: t('personalize.resetAll'), message: t('personalize.resetConfirm'), confirmText: t('dialog.reset'), danger: true });
        if (!ok) return;
        try {
            clearTimeout(saveTimer);
            saveTimer = null;
            const resp = await fetch('/api/personalization/reset', { method: 'POST' });
            const data = await resp.json();
            if (!data.success) throw new Error(data.message || resp.status);
            settings = normalize(data.settings);
            applyAll(true);
            toast(t('personalize.resetDone'));
        } catch (err) {
            toast(t('personalize.saveFailed', { error: err.message || err }), 'error');
        }
    }

    function applyAll(animate) {
        applyTheme();
        renderTextFields();
        applyTextOverrides();
        applyOverlay();
        applyHeroImage(animate);
    }

    // ---------- 初始化 ----------

    function bind() {
        const imageInput = byId('hero-image-input');
        byId('hero-image-choose')?.addEventListener('click', () => imageInput?.click());
        imageInput?.addEventListener('change', () => {
            const file = imageInput.files && imageInput.files[0];
            imageInput.value = '';
            handleImageFile(file);
        });
        byId('hero-image-recrop')?.addEventListener('click', recrop);
        byId('hero-image-default')?.addEventListener('click', useDefaultImage);
        const preview = byId('hero-preview');
        preview?.addEventListener('click', () => imageInput?.click());
        preview?.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); imageInput?.click(); }
        });

        // 「首页」页上：拖图片进窗口任何地方都换首页图片（plugins.js 这时候不接）
        const onHeroPage = () => window.GanlanUiPages && window.GanlanUiPages.getPage() === 'hero';
        const hasFiles = (event) => Array.from(event.dataTransfer?.types || []).includes('Files');
        let depth = 0;
        window.addEventListener('dragenter', (event) => {
            if (!onHeroPage() || !hasFiles(event)) return;
            event.preventDefault();
            depth += 1;
            preview?.classList.add('is-dragover');
        });
        window.addEventListener('dragover', (event) => {
            if (!onHeroPage() || !hasFiles(event)) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = 'copy';
        });
        window.addEventListener('dragleave', (event) => {
            if (!onHeroPage() || !hasFiles(event)) return;
            depth = Math.max(0, depth - 1);
            if (!depth) preview?.classList.remove('is-dragover');
        });
        window.addEventListener('drop', (event) => {
            if (!onHeroPage() || !hasFiles(event)) return;
            event.preventDefault();
            depth = 0;
            preview?.classList.remove('is-dragover');
            handleImageFile(event.dataTransfer.files && event.dataTransfer.files[0]);
        });

        const slider = byId('hero-overlay');
        slider?.addEventListener('input', () => {
            settings.hero.overlay = Number(slider.value);
            applyOverlay();
            scheduleSave();
        });
        byId('hero-text-reset')?.addEventListener('click', () => {
            TEXT_FIELDS.forEach((field) => { settings.hero[field.key] = ''; });
            renderTextFields();
            applyTextOverrides();
            scheduleSave();
        });

        const importInput = byId('personalize-import-input');
        importInput?.addEventListener('change', () => {
            const file = importInput.files && importInput.files[0];
            importInput.value = '';
            importConfig(file);
        });
        document.querySelectorAll('[data-personalize-action]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const action = btn.dataset.personalizeAction;
                if (action === 'export') exportConfig();
                else if (action === 'import') importInput?.click();
                else if (action === 'reset') resetAll();
            });
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        bind();
        renderPresets();
        renderColors();
        renderTextFields();
        applyOverlay();
        applyHeroImage(false);
        renderContrast();
        applyTextOverrides();
    });

    document.addEventListener('i18n:languageChanged', () => {
        renderPresets();
        renderColors();
        renderTextFields();
        renderContrast();
    });

    // 关窗口前还没存的赶紧存
    window.addEventListener('beforeunload', () => {
        if (!saveTimer) return;
        clearTimeout(saveTimer);
        try {
            navigator.sendBeacon('/api/personalization', new Blob([JSON.stringify({ theme: settings.theme, hero: settings.hero })], { type: 'application/json' }));
        } catch (e) { /* ignore */ }
    });

    window.GanlanPersonalize = {
        getSettings: () => clone(settings),
        openPicker,
        openCropper,
    };
})();
