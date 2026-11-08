# Dashboard design references and font provenance

Retrieved on 2026-11-08. Assets in dashboard/web are local copies from the
official Cal Sans repository. No H Company images, logos, or proprietary fonts
were copied.

## Cal Sans

- Repository: https://github.com/calcom/sans
- Pinned commit: `06c9819281db8f1bdae17e3d3ed359b09e56fbfc` (v2.012).
- Font source: https://raw.githubusercontent.com/calcom/sans/06c9819281db8f1bdae17e3d3ed359b09e56fbfc/fonts/calsans-var-full/CalSansVF.woff2
- License source: https://raw.githubusercontent.com/calcom/sans/06c9819281db8f1bdae17e3d3ed359b09e56fbfc/OFL.txt
- Upstream web CSS: https://github.com/calcom/sans/blob/06c9819281db8f1bdae17e3d3ed359b09e56fbfc/index.css
- License: SIL Open Font License 1.1, retained unmodified as `dashboard/web/OFL.txt`.
- Copyright: 2021 The Cal Sans Project Authors.

`CalSansVF.woff2` is the current full variable build, not the older single-weight
CalSans-SemiBold release. Upstream declares weights 400–700. Keep `OFL.txt` with
redistributed copies. The font file was not subsetted or modified.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| CalSansVF.woff2 | 295472 | `d642aa87afbf9310bc4c6c5333643cb8b26dde21f4579a8ef5a667010aa276c8` |
| OFL.txt | 4381 | `c6bd569e2bb98d55fcdaeb53918c023741772ea882d470844195f858a41c5551` |

The WOFF2 signature and both Git blob hashes match the official repository:
font `699bf72a364279d1b04ba22a59efec6b40987254`, license
`52135553ce36d47c868d0dd93de3accaf60fea76`.

Recommended web declaration (adjust the asset path when copying into the app):

```css
@font-face {
  font-family: "Cal Sans";
  src: url("./CalSansVF.woff2") format("woff2");
  font-style: normal;
  font-weight: 400 700;
  font-display: swap;
}
```

## H Company live site reference

Sources inspected over HTTP:

- https://hcompany.ai/
- https://hcompany.ai/_next/static/immutable/chunks/17_m2-y2gtdy9.css
- https://hcompany.ai/_next/static/immutable/chunks/1qz-vknzok8zo.css

These values are from the live HTML/CSS, not sampled approximations. The current
homepage serves a light theme. Its original sans font is **Plus Jakarta Sans**,
with **Fira Code** for monospace; use the requested Cal Sans in the dashboard.

| Role | CSS token | Value |
| --- | --- | --- |
| Primary background / cards | `--color-bg-primary`, `--color-bg-card` | `#ffffff` |
| Secondary background / selected | `--color-grey-200` | `#f2f2f5` |
| Soft background | `--color-grey-100` | `#f9f9fb` |
| Borders / hover surface | `--color-grey-250` | `#ebebf1` |
| Stronger border | `--color-grey-300` | `#d8d8e2` |
| Primary title / text | `--color-grey-750` | `#1c1c26` |
| Secondary text | `--color-grey-600` | `#4a4a58` |
| Muted text | `--color-grey-500` | `#7a7a8a` |
| Disabled text | `--color-grey-400` | `#adadbc` |
| Primary purple accent | `--color-iris-600` | `#5a04bc` |
| Bright purple | `--color-iris-400` | `#7114eb` |
| Purple hover | `--color-iris-700` | `#2c0264` |
| Soft purple surface | `--color-iris-50` | `#f1ecf9` |
| Accent stroke | `--color-iris-200` | `#c2a0ee` |
| Live / success background | `--color-accent-green` | `#10c56d` |
| Success text | `--color-green-500` | `#38a878` |
| Warning | `--color-orange-600` | `#c05a18` |
| Danger | `--color-red-600` | `#c0404a` |
| Dark feature panel | `--color-grey-900` | `#040405` |

The CSS also defines a dark theme: background/card `#0d0d12`, hover `#111117`,
selected/border `#1c1c26`, text `#f2f2f5`, secondary text `#adadbc`, purple
`#7114eb`. A light dashboard with a dark Minecraft viewport follows the default
homepage more closely than an entirely dark interface.

### Layout and typography observed

- Centered content width: 72rem / 1152px, with 24px horizontal padding.
- Fixed navigation bar: 72px high, 14px semibold links, compact 8px corner radii.
- Homepage hero: centered, max-width 64rem / 1024px, large title with -0.025em
  letter spacing; responsive 28–60px, line-height 1.1.
- Section titles: 36px / 40px, weight 500. Card headings: 18px / 24px,
  weight 500. Compact feature titles: 24px / 28px, weight 500.
- Body scale: 16px / 24px; compact labels 14px / 20px; small labels 12px / 16px.
- Sections have 48px vertical padding, growing to 80px on wider screens.
- Cards use 12px radii, 1px pale borders, ample padding, restrained shadows.
- Buttons use 8px radii, 20px horizontal and 10px vertical padding, 14px labels.
- Accent cards use subtle purple radial blooms on almost-black surfaces;
  example bloom: `radial-gradient(circle, #7114eb61 0%, transparent 68%)`.
- Transitions generally last 150–250ms. Product cards use small visual details,
  not saturated backgrounds across every panel.

### Dashboard adaptation recommendation

Use a compact 72px header, white page, Cal Sans title, and neutral-gray dashboard
cards. Make the live Minecraft viewport the largest element, with a slim status
and objective column beside it. Use purple for the primary control/current step,
green for connected/running, and muted gray for waiting. Keep elapsed time and
latency numerals aligned; monospace fallback is suitable for telemetry. A 12px
radius and pale 1px borders preserve the site's restrained visual character.
These are adaptation recommendations, not additional claims about the source site.
