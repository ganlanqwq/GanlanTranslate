/** Tailwind 配置：把设计稿用到的 class（含大量任意值）编译成本地 CSS。 */
module.exports = {
  corePlugins: {
    preflight: false,
  },
  content: [
    '../ui/templates/index.html',
    '../ui/templates/panel.html',
    './design-source.html',
    './safelist.txt',
  ],
  theme: {
    extend: {},
  },
  plugins: [],
};
