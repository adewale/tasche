# CRAP Design Audit: Tasche UI

An analysis of the Tasche read-it-later interface through Robin Williams' four fundamental design principles: **Contrast**, **Repetition**, **Alignment**, and **Proximity**.

---

## Contrast

*"If two items are not exactly the same, then make them very different."*

### Strengths

**Strong typographic contrast between UI and content.** The app makes a clear distinction between UI chrome (system sans-serif stack) and article content (Georgia serif). This is the single most important contrast decision in a reading app and Tasche gets it right. The header logo uses serif at 1.25rem bold with tight letter-spacing (`app.css:226–232`), immediately establishing a literary identity that contrasts with the functional sans-serif used everywhere else.

**Section titles are confidently small.** The `.section-title` treatment (`app.css:1982–1991`) — 0.75rem, uppercase, 0.1em letter-spacing, bold, with a 2px bottom border — is a textbook example of making a heading feel authoritative through weight and structure rather than size. It contrasts sharply with body text and article titles, which are larger but lighter.

**Status badges use a considered color vocabulary.** The original-status variants (`app.css:940–968`) each have their own background, text, and border color tuned to convey meaning: muted green for "available", gold for "paywalled", brown for "gone", red for "domain dead". These are desaturated enough to feel cohesive but different enough to be distinguishable at a glance.

**The header's 2px solid bottom border** (`app.css:209`) is a strong, confident line that separates navigation from content. The audio player mirrors this with a 2px top border (`app.css:1338`). These thick rules act as structural anchors.

### Weaknesses

**The accent color is the same as the text color** (`--accent: #1d1d1f`, `--text: #1d1d1f` at `app.css:7,11`). This means primary buttons, links, favorite icons, and progress bars all share the exact same color as body text. When everything is the same weight of black, interactive elements don't stand out from static text. The primary button (`app.css:308–312`) is a dark rectangle that could be mistaken for a heading. Links (`app.css:155–163`) are distinguished only by an underline — fine for in-article content, but in the Reader actions bar where 7+ buttons compete for attention (`Reader.jsx:684–798`), there is no color-based hierarchy at all.

**The filter tabs lack sufficient contrast for the active state.** The active tab (`app.css:492–498`) is distinguished from inactive tabs only by a 2px bottom border and a color shift from `--text-secondary` to `--text`. On a fast scan, the active tab doesn't jump out. Compare this to the Reader toolbar segments (`app.css:1203–1206`), which invert foreground/background for the active state — a much stronger contrast treatment that the filter tabs should match.

**Card action buttons are muted to the point of invisibility.** Action buttons in the article card footer (`app.css:722–735`) use `--text-muted` (#aeaeb2 in light mode), which at 0.875rem is quite faint. The favourite star, archive icon, headphones, and delete button all look the same until hovered. The delete button (`ArticleCard.jsx:244`) has a `delete-btn` class but no distinct danger styling at rest — only on hover does it presumably look different. Destructive actions should be visually distinct *before* interaction.

**Toast notifications rely entirely on border color for differentiation.** Success, error, and info toasts (`app.css:1783–1799`) all have the same background (`--bg`) and text color (`--text`), differing only in a 1px border color (green, red, or gray). A 1px colored border on a white card is very subtle — especially for error toasts that need to command attention.

### Recommendations

1. **Introduce a distinct accent color** — even a very muted blue or teal — to differentiate interactive elements from body text. It doesn't need to be bright; a single hue shift would suffice.
2. **Give error toasts a tinted background** (e.g., `--status-dead-bg`) to make them impossible to miss.
3. **Use the inverted active treatment** from `.reader-toolbar-seg.active` on the filter tabs for consistency and clarity.
4. **Give the delete button a persistent danger color** at rest, not just on hover.

---

## Repetition

*"Repeat visual elements throughout the design to create unity."*

### Strengths

**Small-caps is used consistently as a metadata voice.** Card metadata (`app.css:681`), reader metadata (`app.css:828`), back links (`app.css:801`), tag chips (`app.css:1449`), sidenotes (`app.css:865`), setup group labels (`app.css:1892`), and toolbar labels (`app.css:1171`) all use `font-variant: small-caps` with `letter-spacing: 0.03em`. This creates a distinct secondary typographic voice — a "whisper register" that consistently signals "this is supplementary information." This is excellent repetition.

**The `--radius: 2px` is near-universal.** Almost everything — cards, buttons, inputs, modals, toasts, code blocks, tag chips — uses 2px or 1px border radius. The app looks like it belongs to one family. There are no rogue rounded corners. The sole exception is the offline badge (50% radius for a circle), which is appropriate.

**The `--border` color and 1px rule** appears on cards, inputs, toolbar segments, tag rows, tags list, setup items, modals, table cells, and code blocks. It is the single most repeated element and it works beautifully — the UI feels stitched together by these fine gray lines.

**Gap values are drawn from a tight set.** The CSS uses 4px, 6px, 8px, 12px, 16px, 24px, and 32px repeatedly across gap, padding, and margin declarations. This creates a subtle but perceivable rhythm. The `input-group` gap (8px at `app.css:404`), filter-bar gap (8px at `app.css:445`), header-actions gap (8px at `app.css:242`), and tag-picker gap (8px at `app.css:1478`) all share the same measure.

**The serif/sans pairing is consistent.** Georgia for: article titles, reader title, tag row names, login heading, modal titles, markdown view titles, header logo. System sans for: all UI text, headings within article content, section titles, metadata, buttons. No third typeface appears anywhere except monospace for code. This discipline is strong.

### Weaknesses

**The `middle-dot separator` pattern in metadata is not consistently applied.** Article card metadata (`app.css:685–689`) uses a CSS `::before` pseudo-element with `\00b7` (middle dot) between spans. But the reader metadata (`Reader.jsx:634–655`) relies on separate `reader-meta-item` spans with gap spacing and no dots. The search results cards (`Search.jsx:120–124`) reuse the article-card-meta class and get dots. This inconsistency between the library cards and the reader header creates a subtle visual mismatch.

**Button styling has fragmentation.** There are at least 5 button styles: `.btn-primary`, `.btn-secondary`, `.btn-danger`, `.btn-icon`, `.btn-save-audio` — plus the audio player's `.play-btn` and the filter `.filter-tab`. The `.btn-save-audio` (`app.css:1625–1648`) defines its own padding, border, and color rather than extending `.btn-secondary`. This means it doesn't inherit the hover state consistently. When every button reinvents itself slightly, the repetition of the button *pattern* weakens.

**The `--shadow-float` is used but `--shadow` and `--shadow-lg` are set to `none`.** This creates a curious inconsistency in the design tokens: three shadow variables exist but only one has a value (`app.css:20–22`). If the design philosophy is "no shadows except floating elements," the unused tokens should be removed or the philosophy should be documented, as the declared-but-unused variables suggest an incomplete system.

### Recommendations

1. **Unify metadata separators** — either use middle dots everywhere (cards + reader header) or remove them everywhere. Pick one voice.
2. **Consolidate `.btn-save-audio` into `.btn-secondary`** with a modifier, so hover/active states repeat the same pattern.
3. **Remove unused shadow tokens** (`--shadow`, `--shadow-lg`, `--shadow-up`) to keep the design system honest.

---

## Alignment

*"Nothing should be placed on the page arbitrarily. Every element should have a visual connection with another element on the page."*

### Strengths

**The 960px max-width rail is rigorously enforced.** The main content (`app.css:285`), header inner (`app.css:214`), audio player inner (`app.css:1348`), and reader main are all constrained to the same maximum width and centered. This creates a strong invisible vertical line down each edge of the content area. Nothing escapes this column.

**The article card's internal layout is well-aligned.** The card body (`app.css:534–537`) uses flexbox with a 12px gap. The thumbnail (72px) sits on the left edge, and the content area (title, meta, excerpt) flows to its right, all top-aligned. The footer (tags + actions) spans the full width below. This creates a clean Z-pattern: image → title → meta → excerpt → tags → actions.

**The reader's grid layout with sidenotes** (`app.css:891–913`) is a particularly sophisticated alignment decision. On wide screens, a 160px left column holds metadata annotations while the main content flows in a separate column capped at 680px. This Tuftean layout creates a clear left edge for the content while giving supplementary information its own aligned track.

**Form input groups** (`app.css:402–409`) consistently place the text input on the left (flex: 1) with action buttons to its right. The save form, search bar, and tag creation form all follow this exact pattern. A user only needs to learn the layout once.

### Weaknesses

**The reader actions bar has no alignment structure.** `Reader.jsx:684–798` renders 7–10 buttons in a flex-wrap container with 8px gap. On narrower screens, these wrap unpredictably. There is no grouping — the favourite button sits next to the reading status dropdown, which sits next to the offline save button, which sits next to the listen button, which sits next to the original link, which sits next to retry, which sits next to delete. The buttons form a visual run-on sentence. They need alignment into logical groups (status actions | audio actions | meta actions | destructive actions) with either separators or additional gap between groups.

**The Settings page lacks vertical rhythm.** Settings sections are separated by `mt-8` (margin-top: 2rem) utility classes applied directly in JSX (`Settings.jsx:94, 157, 204, 245`). However, the first section has `mt-4` (1rem) while the rest use `mt-8`. The inconsistent top margin means the first section sits closer to the page title than subsequent sections sit to each other. Additionally, these margin utilities aren't defined in the main CSS file — they appear to be inline conventions, breaking the "alignment comes from the system" principle.

**The filter bar's sort dropdown and select button lack a left-edge anchor.** The filter bar (`Library.jsx:440–478`) places tabs on the left and the sort dropdown + select button on the right, but the right-side elements have no clear relationship to anything above or below them. The save form's buttons align with the right edge of the input, while the filter bar's buttons float freely. These two rows should share a right-edge alignment.

**The Stats page mixes alignment patterns.** The stat cards grid (`Stats.jsx:70–90`) uses a CSS grid (`.stats-grid`). The activity section uses `.stats-activity-grid`. The domain bars are in a list. The monthly trend is a table. Each section invents its own layout, and while each works internally, the transitions between sections feel arbitrary rather than rhythmic.

### Recommendations

1. **Group the reader action buttons** into 2–3 logical clusters, separated by a visible divider or increased gap, so the eye can parse them as distinct units.
2. **Standardize section spacing** in Settings (and Stats) to a consistent value — define a `.section` class with fixed top margin rather than ad-hoc utilities.
3. **Align the filter bar's right-side controls** with the save button's right edge above it, creating a vertical line on the right side of the form area.

---

## Proximity

*"Items relating to each other should be grouped close together."*

### Strengths

**Article cards group related information well.** The card body (`ArticleCard.jsx:153–184`) places title, metadata, and excerpt in tight proximity (4px and 8px gaps), while the footer (tags + actions) is separated by 10px margin-top. This creates two clear zones: "what is this article?" (top) and "what can I do with it?" (bottom). The reading progress bar sits at the absolute bottom of the card, visually distinct from both zones.

**The reader header creates a clear information cascade.** `Reader.jsx:629–799` flows: back link → title → metadata → tags → original status → action buttons, each separated by consistent margins (16px, 12px, 8px, 16px). The information moves from "where am I?" to "what am I reading?" to "what's its status?" to "what can I do?" This is a well-structured proximity hierarchy.

**The tag row** (`app.css:1492–1501`) groups the tag name and article count tightly (8px gap at baseline) on the left, and action buttons on the right, with `justify-content: space-between`. The name and count are related (they describe the tag), so their proximity is correct. The actions are related to each other (they modify the tag), so their proximity is also correct.

**The audio player groups controls by function.** The player bar (`AudioPlayer.jsx`) places info (title + time) on the left, transport controls (back, play, forward, speed) in the center, and the close button on the right. The play button is visually larger (42px vs 36px) and has a filled background, making it the gravitational center of its group.

### Weaknesses

**The reader's original status badge sits between tags and actions, creating a sandwich.** In `Reader.jsx:656–683`, the original status indicator appears *after* the TagPicker and *before* the action buttons. This places a status display (informational) between two interactive zones (tag assignment and action buttons). The status badge is more closely related to the article metadata (domain, reading time) above. Moving it up — or placing it in the sidenote column on desktop — would improve the proximity logic.

**The Library page's save form and filter bar have no visual grouping.** The save form (`Library.jsx:405–439`) and filter bar (`Library.jsx:440–478`) are adjacent siblings with `margin-bottom: 24px` on the save form. They serve different purposes (adding content vs. filtering content) but appear as one continuous block. A horizontal rule, increased spacing, or distinct background would clarify that these are separate functional zones.

**The bulk action bar** (`Library.jsx:483–518`) appears between the filter bar and the article list when in select mode. Its count label, action buttons, and close button are all in a single flat row. The "2 selected" count and the "Select All / Clear" buttons are selection-management controls, while "Archive" and "Delete" are destructive actions. These two groups should be separated — either with a visual divider or by placing destructive actions on the right edge away from the selection controls.

**The Settings page's "Log out" button** (`Settings.jsx:245–254`) sits at the bottom of the page immediately after the Export section, separated only by `mt-8`. The logged-in-as text and logout button are account-related, but they share proximity with the export controls above. This final section needs either a stronger separator or a distinct visual treatment to signal "this is about your account, not your data."

### Recommendations

1. **Move the original status badge** in the Reader to sit immediately below the metadata row, *before* the TagPicker, so all informational elements are grouped together and all interactive elements follow.
2. **Add a subtle separator** (1px border or 32px gap) between the save form and the filter bar in the Library view.
3. **Split the bulk action bar** into a left group (selection count, select all, clear) and a right group (archive, delete, close) with visible separation.
4. **Give the Settings account section** a top border or a distinct section heading to separate it from export controls.

---

## Summary Scorecard

| Principle  | Score | Notes |
|------------|-------|-------|
| **Contrast**  | 7/10  | Typography contrast is excellent. Color contrast is undermined by the monochrome accent. Interactive elements need to stand apart from static text. |
| **Repetition** | 9/10  | The strongest principle in the design. Small-caps metadata voice, 2px radius, 1px borders, consistent font pairing, and tight spacing scale create a cohesive system. |
| **Alignment**  | 7/10  | The 960px rail and card layouts are strong. The reader actions bar and settings page lack structural alignment. |
| **Proximity**  | 8/10  | Card-level grouping is thoughtful. Page-level grouping (save form vs. filters, reader status badge placement) needs refinement. |

**Overall: 31/40** — The design is well above average, with Repetition as its standout strength. The primary opportunity is introducing a single accent hue to boost Contrast, and tightening the grouping/alignment of action-heavy areas (reader actions bar, bulk action bar, settings page).
