/**
 * 配色（1.1.2，岚：可以自定义配色，可以输入 16 进制码或者使用调色盘）。
 *
 * 在 <head> 里同步加载：页面画出来之前就把颜色变量设好，打开时不会先闪一下默认的绿色。
 * 默认配色就是 style.css 第 1 节的令牌；换了主题色时，把整套颜色（浅绿底、灰绿文字、描边……）
 * 按新主题色的色相、饱和度在 OKLCH 里转过去，明暗不变，所以文字和底色的对比和原来一样。
 * 背景、卡片、文字单独改过时，相关的几档灰按它们重新调和。
 *
 *   GanlanTheme.apply(theme)   theme = { accent, background, surface, text, hero_accent, hero_shade }，空串 = 跟默认
 *   GanlanTheme.compute(theme) 只算不设，返回 { '--green-600': '#1F7A4D', ... }
 */
(function () {
    'use strict';

    // 默认配色（和 style.css :root 一致）。名字就是 CSS 变量名。
    const BASE = {
        '--bg': '#FBFCFA',
        '--surface': '#FFFFFF',
        '--surface-soft': '#F5F8F4',
        '--surface-tint': '#EEF5F0',
        '--surface-hover': '#F4F7F3',
        '--green-50': '#E4F2E8',
        '--green-75': '#E7F2EA',
        '--green-100': '#CFE6D7',
        '--green-400': '#5FC08D',
        '--green-500': '#2E8F5E',
        '--green-600': '#1F7A4D',
        '--green-700': '#17643E',
        '--green-900': '#0D3A23',
        '--ink': '#12261A',
        '--ink-2': '#3F5B49',
        '--ink-mid': '#5E7A6A',
        '--ink-3': '#7C9285',
        '--ink-soft': '#8FA79B',
        '--ink-4': '#9AAFA1',
        '--line': '#E3EAE1',
        '--line-strong': '#CBD9C7',
        '--hero-accent': '#8FDCB4',
        '--hero-accent-2': '#7FD3A6',
        '--hero-shade': '#0A2115',
        '--console-bg': '#0D2016',
        '--console-ink': '#C7DFD0',
        '--console-thumb': '#24402F',
    };
    const DEFAULT_ACCENT = BASE['--green-600'];
    // 跟着主题色一起变亮变暗的（其余只转色相、明暗不动）
    const ACCENT_FAMILY = new Set(['--green-400', '--green-500', '--green-700']);
    // 这些变量还要一份 "r g b" 形式，给 rgb(var(--x-rgb) / 0.2) 这种半透明用
    const RGB_VARS = ['--green-600', '--green-400', '--ink', '--hero-shade', '--console-bg', '--surface', '--bg'];

    const PRESETS = [
        { id: 'default', accent: '#1F7A4D' },
        { id: 'teal', accent: '#0F8079' },
        { id: 'lake', accent: '#1F6FA8' },
        { id: 'violet', accent: '#6A4BB0' },
        { id: 'sakura', accent: '#C2457A' },
        { id: 'amber', accent: '#B4660F' },
        { id: 'graphite', accent: '#3D4A52' },
    ];

    // ---------- 颜色换算 ----------
    function clamp(x, lo, hi) { return Math.min(hi, Math.max(lo, x)); }

    function parseHex(hex) {
        const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || '').trim());
        if (!m) return null;
        const n = parseInt(m[1], 16);
        return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    }

    function toHex(rgb) {
        return '#' + rgb.map((v) => clamp(Math.round(v), 0, 255).toString(16).padStart(2, '0')).join('').toUpperCase();
    }

    function normalizeHex(value) {
        const rgb = parseHex(value);
        return rgb ? toHex(rgb) : '';
    }

    const toLinear = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    const fromLinear = (c) => 255 * (c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055);

    function rgbToOklch(rgb) {
        const [r, g, b] = rgb.map(toLinear);
        const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
        const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
        const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
        const L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s;
        const A = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s;
        const B = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s;
        return [L, Math.hypot(A, B), (Math.atan2(B, A) * 180 / Math.PI + 360) % 360];
    }

    function oklchToLinear(L, C, h) {
        const rad = h * Math.PI / 180;
        const A = C * Math.cos(rad);
        const B = C * Math.sin(rad);
        const l = Math.pow(L + 0.3963377774 * A + 0.2158037573 * B, 3);
        const m = Math.pow(L - 0.1055613458 * A - 0.0638541728 * B, 3);
        const s = Math.pow(L - 0.0894841775 * A - 1.2914855480 * B, 3);
        return [
            4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
        ];
    }

    // 超出 sRGB 时降低彩度直到放得下
    function oklchToRgb(L, C, h) {
        L = clamp(L, 0, 1);
        let c = Math.max(0, C);
        for (let i = 0; i < 24; i += 1) {
            const lin = oklchToLinear(L, c, h);
            if (lin.every((v) => v >= -0.0005 && v <= 1.0005)) return lin.map((v) => fromLinear(clamp(v, 0, 1)));
            c *= 0.88;
        }
        return oklchToLinear(L, 0, h).map((v) => fromLinear(clamp(v, 0, 1)));
    }

    function mix(hexA, hexB, t) {
        // t = 0 → A，t = 1 → B（在线性光里混，深浅更自然）
        const a = parseHex(hexA).map(toLinear);
        const b = parseHex(hexB).map(toLinear);
        return toHex(a.map((v, i) => fromLinear(v + (b[i] - v) * t)));
    }

    function luminance(hex) {
        const [r, g, b] = parseHex(hex).map(toLinear);
        return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    }

    function contrast(hexA, hexB) {
        const a = luminance(hexA);
        const b = luminance(hexB);
        return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    }

    function isDark(hex) { return luminance(hex) < 0.22; }

    // ---------- 算一整套 ----------
    function retint(accent) {
        const out = { ...BASE };
        const target = rgbToOklch(parseHex(accent));
        const origin = rgbToOklch(parseHex(DEFAULT_ACCENT));
        const dh = target[2] - origin[2];
        const ratio = clamp(target[1] / origin[1], 0, 2.2);
        const dl = target[0] - origin[0];
        Object.entries(BASE).forEach(([name, hex]) => {
            if (name === '--surface') return;
            const [L, C, h] = rgbToOklch(parseHex(hex));
            const newL = ACCENT_FAMILY.has(name) ? clamp(L + dl, 0.18, 0.93) : L;
            out[name] = toHex(oklchToRgb(newL, C * ratio, h + dh));
        });
        out['--green-600'] = normalizeHex(accent);
        return out;
    }

    function compute(theme) {
        const t = theme || {};
        const accent = normalizeHex(t.accent) || DEFAULT_ACCENT;
        const vars = accent === DEFAULT_ACCENT ? { ...BASE } : retint(accent);

        const background = normalizeHex(t.background);
        const surface = normalizeHex(t.surface);
        const text = normalizeHex(t.text);
        if (surface || text || background) {
            const S = surface || vars['--surface'];
            const T = text || vars['--ink'];
            const A = vars['--green-600'];
            vars['--surface'] = S;
            vars['--ink'] = T;
            if (surface || text) {
                vars['--ink-2'] = mix(T, S, 0.22);
                vars['--ink-mid'] = mix(T, S, 0.36);
                vars['--ink-3'] = mix(T, S, 0.48);
                vars['--ink-soft'] = mix(T, S, 0.56);
                vars['--ink-4'] = mix(T, S, 0.62);
                vars['--line'] = mix(T, S, 0.885);
                vars['--line-strong'] = mix(T, S, 0.79);
                vars['--surface-soft'] = mix(T, S, 0.965);
                vars['--surface-hover'] = mix(T, S, 0.955);
                vars['--surface-tint'] = mix(A, S, 0.92);
                vars['--green-50'] = mix(A, S, 0.86);
                vars['--green-75'] = mix(A, S, 0.88);
                vars['--green-100'] = mix(A, S, 0.74);
                vars['--green-900'] = isDark(S) ? mix(A, '#FFFFFF', 0.62) : vars['--green-900'];
            }
            vars['--bg'] = background || (surface ? mix(T, S, 0.985) : vars['--bg']);
        }

        const heroAccent = normalizeHex(t.hero_accent);
        if (heroAccent) {
            vars['--hero-accent'] = heroAccent;
            vars['--hero-accent-2'] = mix(heroAccent, '#000000', 0.08);
        }
        const heroShade = normalizeHex(t.hero_shade);
        if (heroShade) vars['--hero-shade'] = heroShade;

        // 按钮上的字：主题色太浅时换成深色字
        vars['--on-accent'] = contrast('#FFFFFF', vars['--green-600']) >= 3 ? '#FFFFFF' : mix(vars['--green-600'], '#000000', 0.82);
        RGB_VARS.forEach((name) => {
            vars[name + '-rgb'] = parseHex(vars[name]).join(' ');
        });
        return vars;
    }

    function isDefault(theme) {
        const t = theme || {};
        return !['accent', 'background', 'surface', 'text', 'hero_accent', 'hero_shade'].some((key) => {
            const value = normalizeHex(t[key]);
            return value && !(key === 'accent' && value === DEFAULT_ACCENT);
        });
    }

    let lastApplied = [];

    function apply(theme) {
        const root = document.documentElement;
        lastApplied.forEach((name) => root.style.removeProperty(name));
        lastApplied = [];
        const custom = !isDefault(theme);
        root.classList.toggle('gl-custom-theme', custom);
        if (!custom) return compute(null);   // 默认就用 style.css 里写好的
        const vars = compute(theme);
        Object.entries(vars).forEach(([name, value]) => {
            root.style.setProperty(name, value);
            lastApplied.push(name);
        });
        return vars;
    }

    window.GanlanTheme = { BASE, PRESETS, DEFAULT_ACCENT, compute, apply, isDefault, normalizeHex, parseHex, toHex, contrast, mix, rgbToOklch };

    // 服务端塞进页面的个性化设置（index.html 的 <head>）
    const initial = window.GL_PERSONALIZATION && window.GL_PERSONALIZATION.theme;
    if (initial) {
        try { apply(initial); } catch (e) { /* 坏了就用默认配色 */ }
    }
})();
