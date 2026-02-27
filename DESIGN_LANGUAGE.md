# Tasche Design Language

Monochrome pen-and-ink aesthetic. Enforced by `scripts/lint-design.mjs`.

## Brand

German for "pocket." A preservation tool for knowledge workers.
Sharp, monochrome, typographic. Content-first with no decorative noise.

## The Four Ideas

Everything flows from four ideas, reused everywhere:

### 1. Stroke Weight Hierarchy

The pen-and-ink core. Varying line thickness communicates state.

| Weight | Meaning | Examples |
|--------|---------|----------|
| 1px | Default, inactive, at rest | Card outlines, separators, input borders |
| 2px | Structural / active / focused | Header bottom, audio player top, section headings, focus rings, selected cards, active filter underline, spinner |
| 3px | Primary emphasis | Reading indicator, blockquote accent, toast type stripe, reading progress bar |

### 2. Typography Does the Work of Colour

| Treatment | Role | Examples |
|-----------|------|----------|
| Serif (Georgia) | Titles, prominence | Card titles, logo, login hero, modal titles, stat numbers, tag names |
| Small-caps | Metadata, labels | Article meta, reader meta, stat labels, back links, bulk counts, tag chips |
| Italic | Secondary text | Excerpts, descriptions |
| Uppercase + letter-spacing | Section labels | Section titles, stats section titles |

### 3. Fill Inversion

Black fill = active. White fill = inactive. No coloured fills.

| State | Style |
|-------|-------|
| Primary button | Black bg, white text |
| Active toggle/segment | Black bg, white text |
| Inactive button | White bg, 1px black border |
| Danger button | Red border only; fills red on hover |

### 4. Minimal Decoration

Near-sharp corners. Subtle shadow only on floating elements. No coloured backgrounds.

| Property | Rule |
|----------|------|
| `box-shadow` | Only `var(--shadow-float)` on floating elements (modals, toasts, dropdowns, popovers). No shadows on cards, inputs, or inline elements. |
| `border-radius` | 2px (near-sharp) or 0 |
| Overlay | 15% black (not 50%) |
| Backgrounds | `var(--bg)` or `transparent` only |

Floating elements = things that sit above the page: modals, toasts, help menu, shortcuts panel, highlight toolbar/popover.

Exceptions: spinner (functional circle), highlight colour swatches, stats legend dots.

## Colour Palette

Monochrome. Colour reserved for semantic meaning only.

### Light mode

| Variable | Value | Purpose |
|----------|-------|---------|
| `--text` | `#1d1d1f` | Primary text, primary buttons, active fills |
| `--text-secondary` | `#6e6e73` | Secondary text, hover states |
| `--text-muted` | `#aeaeb2` | Disabled, placeholders, emphasis borders |
| `--bg` | `#ffffff` | Page background |
| `--bg-secondary` | `#f5f5f7` | Secondary surfaces |
| `--border` | `#d2d2d7` | Default borders |
| `--accent` | `#1d1d1f` | Interactive colour (= text) |
| `--link` | `#1d1d1f` | Links (underlined, not coloured) |

### Semantic colours (desaturated, minimal use)

Semantic colours are desaturated to ~25-30% saturation so they register with
the same visual weight as the monochrome greys. They read as tinted greys, not
vivid markers.

| Variable | Light | Dark | Purpose |
|----------|-------|------|---------|
| `--danger` | `#915550` | `#b89490` | Error toasts, delete actions |
| `--success` | `#527a5c` | `#85a88e` | Success toasts |
| `--warning` | `#907040` | `#b09870` | Warning toasts, search marks |

- Highlight colours (yellow, green, blue, pink) — reader annotations only
- Status badge colours (`--status-*`) — desaturated functional badges

## Type Scale

| Step | rem | ~px | Usage |
|------|-----|-----|-------|
| 3xs | 0.6875 | 11 | Toolbar labels, audio time |
| 2xs | 0.75 | 12 | Tag chips, kbd, section titles |
| xs | 0.8125 | 13 | Meta, descriptions, stat labels |
| sm | 0.875 | 14 | Buttons, excerpts, toasts |
| base | 0.9375 | 15 | Inputs, body text |
| md | 1.0625 | 17 | Card titles |
| lg | 1.25 | 20 | Logo |
| xl | 1.75 | 28 | Reader title |
| 2xl | 2.5 | 40 | Login hero, stat numbers |

### Font families

- **Titles**: `--font-serif` (Georgia) — card titles, logo, headings, stat numbers
- **UI chrome**: `--font-sans` (system stack) — buttons, labels, navigation
- **Code**: `--font-mono` (SF Mono) — code blocks, shortcuts keys

## Spacing (4px grid)

| Token | px | Usage |
|-------|-----|-------|
| sp-1 | 4 | Tight gaps, chip padding |
| sp-2 | 8 | Standard gap, button padding |
| sp-3 | 12 | Card gaps, section spacing |
| sp-4 | 16 | Content padding, standard margins |
| sp-5 | 20 | Desktop card padding |
| sp-6 | 24 | Section margins, reader spacing |
| sp-8 | 32 | Large section spacing |
| sp-12 | 48 | Empty state padding |

## Border Radius

| Token | Value | Usage |
|-------|-------|-------|
| `--radius-sm` | 1px | Near-invisible rounding |
| `--radius` | 2px | Default for all components |
| `--radius-lg` | 2px | Same as default (no large radius) |
| 50% | — | Spinner only (functional) |

## Z-Index Layers

| Layer | Value | Element |
|-------|-------|---------|
| card | 10 | Processing overlay |
| sticky | 50 | Bulk action bar |
| header | 100 | Sticky header |
| popover | 150 | Popovers |
| fixed | 200 | Audio player |
| modal | 250 | Modals, overlays |
| toast | 300 | Notifications |

## Motion

| Token | Value | Usage |
|-------|-------|-------|
| `--duration-fast` | 0.15s | Micro-interactions |
| `--transition` | 0.2s ease | Default transitions |
| `--duration-slow` | 0.3s | Progress bars, fades |

## Breakpoints

| Query | Purpose |
|-------|---------|
| `(pointer: coarse)` | Touch device adjustments (44px targets) |
| `(max-width: 639px)` | Mobile layout |
| `(min-width: 640px)` | Tablet+ |
| `(min-width: 768px)` | Desktop |
| `(prefers-color-scheme: dark)` | Dark mode |
