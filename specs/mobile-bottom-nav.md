# Mobile Bottom Navigation

_Last updated: 2026-03-12_

## Problem

Tasche uses a hamburger menu for navigating between top-level views (Library, Tags, Stats, Settings). On mobile, this creates two problems:

1. **Discoverability.** Hamburger menus hide destinations behind a tap. Users don't build a mental map of the app's structure. Industry research shows 40% slower task completion compared to visible bottom tabs, and apps that switched from hamburger to bottom tabs saw 65% increases in daily active usage (Redbooth case study).

2. **Ergonomics.** The hamburger icon sits in the top-right corner — the hardest spot to reach one-handed on modern phones (6"+ screens). Every navigation action requires a stretch-and-tap-twice sequence: reach up to open the menu, then tap the destination.

On desktop, the hamburger is fine — cursor travel is cheap and screen real estate is abundant. This spec only affects viewports below 640px.

---

## Research: What Similar Apps Do

### Pocket

Bottom tab bar with a creative twist: tapping the active Home tab reveals a secondary menu that lets users switch scope (e.g., Home → Archive). The tab icon updates to reflect the current scope. This packs multiple views into fewer tabs without visual clutter.

**Tabs:** Home (with scope switcher), Search, Save (FAB-style), Discover, Profile

### Readwise Reader

Bottom nav bar with **Views**, **Search**, and **Account** tabs. In "long-form reading view," the bottom bar hides entirely to maximize reading space — a pattern directly relevant to Tasche's reader mode.

### Instapaper

Minimal bottom tab bar: **Home**, **Liked**, **Archive**, **Search**, **Settings**. Five tabs, all flat. No clever tricks — just straightforward section switching.

### Wallabag (self-hosted)

Web UI has no bottom nav. The Android app uses a standard hamburger/drawer pattern. UX is generally described as "rough around the edges." Wallabag demonstrates the cost of not investing in mobile navigation — it works, but doesn't feel native.

### Common Patterns

| Pattern | Pocket | Readwise | Instapaper |
|---------|--------|----------|------------|
| Bottom nav? | Yes | Yes | Yes |
| Tab count | 5 | 3–4 | 5 |
| Hides in reader? | Yes | Yes | Yes |
| Search as tab? | Yes | Yes | Yes |

**Universal:** Every modern read-it-later app with a native-feel mobile experience uses bottom navigation. Every one hides it during immersive reading.

---

## Does Bottom Nav Make Sense for Tasche?

**Yes, with constraints.** Here's the reasoning:

### Arguments For

- **Tasche is a PWA.** There's no native tab bar provided by the OS. A bottom nav is the only way to provide persistent, thumb-reachable navigation on mobile.
- **Clear top-level destinations.** Library, Tags, Stats, Settings — exactly four, which falls in the Material Design sweet spot of 3–5 tabs.
- **Single-user simplicity.** No Profile/Account/Discover tabs needed. Four tabs is clean and uncrowded.
- **Platform guidelines agree.** Both Material Design 3 ("Navigation bar") and Apple HIG (tab bars) recommend bottom navigation for 3–5 top-level destinations on mobile. Material explicitly says: "use it for destinations requiring direct access from anywhere."
- **Proven UX uplift.** The research is unambiguous — visible navigation outperforms hidden navigation on mobile.

### Arguments Against (and mitigations)

| Concern | Mitigation |
|---------|------------|
| Screen real estate is precious for reading | Hide bottom nav in reader view (same as Pocket, Readwise, Instapaper) |
| Conflicts with AudioPlayer fixed at bottom | AudioPlayer renders above the nav bar; both are fixed-position |
| Adds visual weight to minimal aesthetic | Use the existing data-ink design language: no gradients, 1px top border, muted icons, small text labels |
| Desktop doesn't need it | Only render below 640px breakpoint; desktop keeps the hamburger |

### Verdict

The cost of *not* doing this is hidden navigation and bad ergonomics on the primary device people use for reading. The cost of doing it is ~50px of vertical space on non-reader views. The trade-off clearly favors bottom nav.

---

## Design

### Tab Definitions

Four tabs, left to right:

| Tab | Icon | Label | Route | Notes |
|-----|------|-------|-------|-------|
| Library | `IconLogo` (existing) | Library | `#/` | Default/home destination |
| Tags | `IconTag` (existing) | Tags | `#/tags` | Tag management |
| Stats | `IconBarChart` (existing) | Stats | `#/stats` | Reading statistics |
| Settings | `IconSettings` (existing) | Settings | `#/settings` | App settings |

**Why not Search as a tab?** Search in Tasche is a filter on the Library view, not a separate destination. Promoting it to a tab would conflict with this intentional design (see spec §1.8: "Search is a filter on the article list, not a separate view"). The search icon remains in the header where it contextually belongs.

### Visual Treatment

```
┌─────────────────────────────────────────┐
│                                         │
│            (page content)               │
│                                         │
├─────────────────────────────────────────┤  ← 1px border, var(--color-border)
│  ◉          ⊛          ⊛          ⊛    │  ← 56px height (Material spec)
│ Library    Tags       Stats    Settings │  ← 11px labels
└─────────────────────────────────────────┘
```

- **Height:** 56px (matches Material Design 3 spec for navigation bars). On devices with a home indicator/safe area, add `env(safe-area-inset-bottom)` padding.
- **Background:** `var(--color-bg)` with 1px top border in `var(--color-border)`. No shadow, no blur — matches the header's treatment.
- **Icons:** 20px, using existing icon components. Active tab icon uses `var(--color-text)`; inactive uses `var(--color-text-secondary)`.
- **Labels:** Always visible (Material Design recommends labels on all tabs for 4 or fewer items). 11px, same color logic as icons.
- **Active indicator:** A subtle 3px bottom border or background pill on the active tab (following Material Design 3's "active indicator" pattern), using `var(--color-text)`.
- **Transitions:** `color 0.2s ease` on icon and label. No layout shifts.

### Visibility Rules

| Context | Bottom nav visible? | Reason |
|---------|-------------------|--------|
| Library view | Yes | Primary navigation |
| Tags view | Yes | Primary navigation |
| Stats view | Yes | Primary navigation |
| Settings view | Yes | Primary navigation |
| Reader view (`#/article/:id`) | **No** | Immersive reading — matches every competitor |
| Markdown view (`#/article/:id/markdown`) | **No** | Immersive reading variant |
| Login view | **No** | Pre-auth, no navigation needed |
| Viewport ≥ 640px | **No** | Desktop/tablet uses hamburger menu |

### Interaction with AudioPlayer

The AudioPlayer is currently `position: fixed; bottom: 0`. When the bottom nav is visible:

- AudioPlayer shifts up by the nav bar height (`bottom: calc(56px + env(safe-area-inset-bottom))`)
- When bottom nav is hidden (reader view), AudioPlayer returns to `bottom: 0`
- This is a CSS-only change, gated on the same conditions that show/hide the nav bar

### Keyboard Shortcuts

No changes. Keyboard shortcuts are a desktop feature (`@media (pointer: fine)`). The bottom nav doesn't participate in keyboard navigation.

---

## Implementation

### New Component: `BottomNav.jsx`

A new component in `frontend/src/components/`. Accepts the current route hash as input, renders four tab links, highlights the active one.

```
Props: none (reads window.location.hash via hashchange listener)
State: currentHash (string)
Render: <nav class="bottom-nav"> with four <a> elements
```

The component self-manages its active state via `hashchange` events, same pattern as the existing Header component.

### Changes to `app.jsx`

- Import and render `<BottomNav />` after `<main>` and before `<AudioPlayer />`
- The component handles its own show/hide logic internally (checks route and viewport)

### Changes to `app.css`

1. **`.bottom-nav` block:** Fixed position, bottom: 0, full width, 56px height, flex row, z-index between main content and AudioPlayer.
2. **`.bottom-nav-tab` items:** Flex column (icon + label), centered, with active state styling.
3. **Safe area:** `padding-bottom: env(safe-area-inset-bottom)` on the nav container.
4. **`main` padding:** Add `padding-bottom: calc(56px + env(safe-area-inset-bottom))` on mobile to prevent content from being hidden behind the nav.
5. **AudioPlayer offset:** When bottom nav is present, AudioPlayer's `bottom` shifts up.
6. **Media query:** All bottom-nav styles wrapped in `@media (max-width: 639px)`. The component renders nothing on wider viewports.
7. **Reader/login hiding:** `.bottom-nav` gets `display: none` via a `data-route` attribute or class on the app container.

### Changes to Header

- On mobile viewports (< 640px), **remove Tags, Stats, and Settings from the hamburger menu** since they're now in the bottom nav. Keep theme toggle, keyboard shortcuts, and design language link in the hamburger (these are actions, not destinations).
- On desktop viewports (>= 640px), hamburger menu remains unchanged.

### Migration Path

No data changes. No API changes. Pure frontend. Fully reversible — removing the component and CSS restores the current behavior.

---

## What This Spec Does NOT Cover

- **Swipe gestures** between tabs (could add later, adds complexity)
- **Badge counts** on tabs (e.g., unread count on Library — premature, adds polling overhead)
- **Animated transitions** between views (current hash routing does hard swaps; this is fine)
- **Tablet layout** (navigation rail / sidebar for 640px–1080px — separate spec if needed)
- **Reordering or customizing tabs** (single-user app, four tabs, no need)
