# Bookmarklet Tagging Spec

> Upgrade the bookmarklet popup from a status-only flash to a lightweight form that lets users tag articles and queue audio at save time — when context is freshest.

**Status:** Draft
**Date:** 2026-03-06

---

## Problem

The current bookmarklet popup (`frontend/public/bookmarklet.html`) is fire-and-forget: it saves the URL, shows "Saved!", and auto-closes. There is no opportunity to add tags or queue audio. Users must open the app, find the article, and tag it after the fact — a workflow almost nobody does. The result: most articles end up untagged, and the tag system goes underused. Similarly, users who want to listen to an article must navigate to the reader view and hit the headphone icon — a second trip that discourages casual use of Listen Later.

Pinboard solved tagging in 2009 by focusing the bookmarklet cursor on the tags field. Huffduffer solved it by having the server pre-fill everything it can infer. Both services prove that the point of capture is the critical moment — for metadata, for categorization, and for intent ("I want to read this" vs. "I want to listen to this").

---

## Design Principles

1. **Tag-first interaction.** URL and title are pre-filled; the cursor starts in the tags input. Copying Pinboard's insight: the thing the user needs to _decide_ is what tags to apply, not what URL they're on.

2. **Zero tags is fine.** Hitting Save (or Enter) with no tags works exactly like the current bookmarklet. No regression for speed-oriented users.

3. **Suggest, don't require.** Show the user's existing tags, highlight ones that match via tag rules, but never force tagging.

4. **Reuse existing patterns.** The popup is a standalone HTML page (no Preact), but it loads the app's built stylesheet for visual consistency and reuses existing API endpoints (`GET /api/tags`, `GET /api/tag-rules`, `POST /api/tags`, `POST /api/articles`) rather than introducing bookmarklet-specific endpoints. Tag chips use the same `.tag-chip` class; Save audio uses the same `.btn-save-audio` class.

5. **Keyboard-completable.** The entire flow — open popup, type tag prefix, accept suggestion, save — must work without the mouse. Target: under 5 seconds for a user who knows their tags.

6. **Stay small.** The popup is a single HTML file with inline JS and minimal inline CSS (layout only — visual styles come from the app's stylesheet). No framework, no build step. Target: under 4KB.

7. **Capture intent, not just URL.** The moment of saving is when the user knows _why_ they're saving. "I want to read this on the train" (save) vs. "I want to listen to this on my walk" (save + listen later) are different intents. The popup should let users express both.

---

## Bookmarklet Code

The bookmarklet JavaScript (`getBookmarkletCode()` in `frontend/src/utils.js`) does not change. It already passes `url` and `title` as query parameters and opens the popup at the correct origin. The only change is the popup window dimensions:

```javascript
// Before
'toolbar=no,width=420,height=180'

// After
'toolbar=no,width=420,height=480'
```

The window name stays `'Tasche'` so repeated clicks reuse the same popup.

---

## Popup Page: `/bookmarklet`

### Layout

```
┌──────────────────────────────────────┐
│  Save to Tasche                      │
├──────────────────────────────────────┤
│                                      │
│  Title (editable)                    │
│  ┌──────────────────────────────────┐│
│  │ How to Build a Bookmarklet      ││
│  └──────────────────────────────────┘│
│                                      │
│  example.com/blog/bookmarklet        │
│                                      │
│  Tags                                │
│  ┌────────────┐ ┌───────┐           │
│  │ javascript ×│ │ web × │           │ ◄ selected tag chips (.tag-chip)
│  └────────────┘ └───────┘           │
│  ┌──────────────────────────────────┐│
│  │ tu|                     [↕ list] ││ ◄ text input + native <datalist>
│  └──────────────────────────────────┘│   browser provides autocomplete
│                                      │
│  Suggested                           │
│  ┌────────┐ ┌─────┐ ┌───────────┐  │
│  │ ★ web  │ │ dev │ │ tutorials │  │
│  └────────┘ └─────┘ └───────────┘  │
│                                      │
│  ┌─────────────────┐ ┌──────────────┐│
│  │      Save       │ │ 🎧 Save audio││
│  └─────────────────┘ └──────────────┘│
│                                      │
└──────────────────────────────────────┘
```

### Elements

| Element | Behavior |
|---------|----------|
| **Title** | Pre-filled from `document.title` (passed via query param). Editable single-line text input. Sent as `title` in the POST body. |
| **URL display** | Shown as static text (not editable). Truncated with ellipsis if longer than ~50 chars. Shows domain prominently. Provides visual confirmation the user is saving the right page. |
| **Selected tags** | Tag chips rendered above the input using the existing `.tag-chip` class (same as `TagPicker.jsx` and `ArticleCard.jsx`). Each chip has a × remove button (`.tag-chip-remove`). Displayed in a `.flex-wrap-gap` container. |
| **Tags input** | `<input>` with a `<datalist>` for browser-native autocomplete. The datalist is populated from `GET /api/tags`. Cursor starts here on page load. Typing triggers the browser's built-in autocomplete dropdown — no custom dropdown JS needed. When the user selects or types a matching tag name and presses Enter/comma/space, a chip is added above and the input clears. |
| **Suggested tags** | Clickable chips shown below the input. Two sources: tag rules that match this URL/domain/title (marked with ★) and the user's most-used tags. Click to add. |
| **Save button** | Submits the form with `listen_later: false`. Disabled while saving. Shows spinner during request. |
| **Save audio button** | Labeled "🎧 Save audio" (matching the library's save form). Submits the form with `listen_later: true`, which saves the article _and_ queues TTS generation. Same disabled/spinner behavior as Save. Visually secondary to the Save button (outlined style, not filled). |
| **Status area** | Below the buttons. Shows "Saved!" / "Saved! Audio will be generated." / "Already saved." / error messages. On success, auto-closes after 1.5s (same as current behavior). |

### Keyboard Shortcuts

| Key | Context | Action |
|-----|---------|--------|
| Any character | Tags input | Browser filters `<datalist>` suggestions |
| `Enter` | Tags input with matching text | Add tag chip, clear input |
| `Enter` | Tags input empty | Submit form (save) |
| `,` or `Space` | Tags input with matching text | Add tag chip, clear input |
| `Backspace` | Tags input, cursor at start | Remove last tag chip |
| `Escape` | Tags input | Close popup window |
| `Ctrl+Enter` / `⌘+Enter` | Anywhere in form | Save audio (equivalent to clicking the Save audio button) |

---

## Data Flow

### On Popup Load

```
popup opens
  │
  ├─ read url, title from query params
  ├─ render title input (pre-filled, editable)
  ├─ render URL display (read-only)
  ├─ focus cursor on tags input
  │
  ├─ GET /api/tags ──────────────────────► returns all user tags with article_count
  │     │                                   (same endpoint used by TagPicker.jsx
  │     │                                    and Tags.jsx)
  │     ├─ populate <datalist> for autocomplete
  │     └─ render top tags by article_count as suggestion chips
  │
  ├─ GET /api/tag-rules ─────────────────► returns all tag rules
  │     │                                   (same endpoint used by Tags.jsx)
  │     └─ match rules against url/title/domain client-side
  │        render ★ chips for matched rule tags
  │
  └─ ready for user input
```

Both fetches happen in parallel. Both endpoints already exist (`tags/routes.py` for `/api/tags`, `tag_rules/routes.py` for `/api/tag-rules`). The tag rule matching logic is simple enough to duplicate in ~15 lines of JS (domain exact match, `title_contains`, `url_contains` — the same three checks as `apply_auto_tags()` in `processing.py`).

If either fetch fails, the popup degrades gracefully — the tags input still works for manual entry, and Save still works.

### On Save

```
user clicks Save (or presses Enter)
  │
  ├─ collect: url, title, tag_ids[], listen_later
  │            (listen_later = false for Save, true for Save audio)
  │
  ├─ POST /api/articles ─────────────────► creates article (status: pending)
  │     { url, title, tag_ids,              if listen_later: also sets
  │       listen_later }                    audio_status = 'pending'
  │                                         returns { id, status }
  │
  ├─ show status message:
  │     listen_later=false → "Saved!"
  │     listen_later=true  → "Saved! Audio will be generated."
  ├─ auto-close after 1.5s
  └─ done
```

---

## API Changes

### No New Endpoints

The bookmarklet reuses two existing endpoints:

| Endpoint | Already used by | What the bookmarklet uses it for |
|----------|----------------|----------------------------------|
| `GET /api/tags` | `TagPicker.jsx`, `Tags.jsx` | Populate `<datalist>` for autocomplete + frequent tag chips |
| `GET /api/tag-rules` | `Tags.jsx` | Client-side rule matching → ★ suggested tag chips |

Tag rule matching runs client-side in the popup. The logic is simple (~15 lines of JS):

```javascript
// Mirrors apply_auto_tags() in processing.py
rules.forEach(function (rule) {
  var p = rule.pattern.toLowerCase();
  if (rule.match_type === 'domain' && domain === p) matched.add(rule.tag_id);
  if (rule.match_type === 'title_contains' && title.toLowerCase().includes(p)) matched.add(rule.tag_id);
  if (rule.match_type === 'url_contains' && url.toLowerCase().includes(p)) matched.add(rule.tag_id);
});
```

This avoids creating a bookmarklet-specific endpoint. The tradeoff: glob matching for domain rules (e.g., `*.example.com`) is not replicated — only exact domain match. This is acceptable because glob domain rules are rare and the queue consumer's `apply_auto_tags()` will still apply them during processing.

### Changes to `POST /api/articles`

Accept an optional `tag_ids` field in the request body. The existing `listen_later` field (already supported) is now also sent by the bookmarklet:

```json
{
  "url": "https://example.com/article",
  "title": "Example Article",
  "tag_ids": ["abc123", "def456"],
  "listen_later": true
}
```

**`tag_ids` behavior:**

- `tag_ids` is optional. If omitted or empty, behaves exactly as today.
- Each tag ID is validated: must belong to the authenticated user. Invalid IDs are silently skipped (not an error — the tag may have been deleted between the popup loading and the user clicking Save).
- Tag associations are inserted via `INSERT OR IGNORE INTO article_tags` (same as `apply_auto_tags()`), so duplicates with auto-tagging are harmless.
- Tag association happens immediately at article creation, before enqueueing the processing job. When the queue consumer later runs `apply_auto_tags()`, the `INSERT OR IGNORE` ensures no duplicates.

**`listen_later` behavior** (no changes — already implemented in `create_article()`):

- When `true`, sets `audio_status = 'pending'` on the new article. The queue consumer picks this up after content processing and generates TTS audio via Workers AI.
- When `false` or omitted, no audio is queued. The user can still trigger Listen Later from the reader view later.

---

## Autocomplete Behavior

Uses the native HTML `<datalist>` element — no custom dropdown code. The browser provides type-to-filter, keyboard navigation, and visual rendering for free.

### Implementation

```html
<input list="tag-options" id="tag-input" placeholder="Add tags..." />
<datalist id="tag-options">
  <!-- populated from GET /api/tags on load -->
  <option value="python">python (12)</option>
  <option value="javascript">javascript (8)</option>
  ...
</datalist>
```

The datalist is populated once from `GET /api/tags` (the same endpoint `TagPicker.jsx` uses via `listTags()`). The `option` labels include the article count for context. The browser handles filtering, keyboard navigation, and display.

### Accepting a Tag

When the user presses Enter, comma, or space with text in the input:
1. Match the text against the tag list (case-insensitive)
2. If matched: add a `.tag-chip` above the input, clear the input, update the datalist to exclude already-selected tags
3. If not matched: ignore (no inline creation — the user should create tags in Settings, consistent with how the app works everywhere else)

### Why `<datalist>` over a Custom Autocomplete

| | `<datalist>` | Custom autocomplete |
|--|-------------|---------------------|
| Lines of JS | ~0 (browser-native) | ~80-120 (dropdown, keyboard nav, positioning) |
| Accessibility | Built-in ARIA, screen reader support | Must implement manually |
| Mobile | Native picker on iOS/Android | Custom dropdown on small screens |
| Consistency | Platform-native look | Must match app styling |
| Article count in suggestions | Via option label text | Full control over display |

The TagPicker component in the app uses a native `<select>` for the same reason — platform controls over custom widgets. The `<datalist>` is the input-with-autocomplete equivalent of that choice.

---

## Suggested Tags Section

Below the tags input, a row of clickable chips in a `.flex-wrap-gap` container. Two categories, visually distinguished:

### Rule-matched tags (★)

Tags whose rules match this URL (matched client-side against rules from `GET /api/tag-rules`). Rendered as `.tag-chip` with a ★ prefix and a subtle highlight background. These are the strongest suggestions — the user explicitly configured these rules.

Example: If the user has a tag rule `domain = "github.com"` → tag "github", and they're saving a GitHub page, the "github" chip appears with ★.

### Recent/frequent tags

The user's most-used tags (by article count), excluding any already shown as rule-matched. Rendered as plain chips. These provide quick one-click access to common tags without typing.

### Interaction

- Clicking a suggested tag chip adds it to the tags input as a selected chip.
- The chip then shows a subtle "added" state (dimmed or check-marked) rather than disappearing, so the layout doesn't shift.
- Clicking an already-added chip removes it (toggle behavior).

---

## Listen Later (Audio at Save Time)

### Why Include Audio in the Bookmarklet?

The moment of saving reveals intent. A user saving an article while commuting has different needs than one curating a research library. Today, queuing audio requires: save article → open app → find article → open reader → click 🎧. That's four extra steps, and the intent ("I want to _hear_ this") was clear at the moment they clicked the bookmarklet.

The in-app `[+Save]` form already supports this — it has separate "Save" and "Save audio" buttons that map to `listen_later: false` and `listen_later: true` on `POST /api/articles`. The bookmarklet popup should mirror this.

### Design: Two Buttons, Not a Checkbox

A checkbox labeled "Also generate audio" adds cognitive overhead to every save. Most saves don't want audio. Instead, two side-by-side buttons make the choice a single decisive action:

| Button | Label | Sends | Status message |
|--------|-------|-------|----------------|
| **Save** (primary) | "Save" | `listen_later: false` | "Saved!" |
| **Save audio** (secondary) | "🎧 Save audio" | `listen_later: true` | "Saved! Audio will be generated." |

The Save button is primary (filled, prominent). The Save audio button is secondary (outlined, slightly smaller) — matching the library's `btn-save-audio` styling. This keeps the default path fast — Enter key triggers Save, not Save audio — while making audio a single extra click away.

### Keyboard: `Ctrl+Enter` / `⌘+Enter` for Save Audio

Enter submits via Save (the common case). `Ctrl+Enter` / `⌘+Enter` submits via Save audio. This matches the "modifier key = enhanced action" convention used by chat apps (send vs. send + something) and avoids adding a new shortcut to memorize.

### No Backend Changes

The `listen_later` field already exists on `POST /api/articles`. The bookmarklet popup simply needs to send `listen_later: true` when the Save audio button is clicked. No new endpoint, no schema change, no queue changes.

### Status Message

When `listen_later: true`:
- Success: **"Saved! Audio will be generated."** — matches the library's toast message (`Library.jsx:256`). Confirms both the save and the audio intent.
- The popup still auto-closes after 1.5s. Audio generation happens asynchronously in the queue; the popup doesn't wait for it.

---

## Reuse Inventory

What the bookmarklet reuses from the existing app:

| Existing asset | Reused how |
|----------------|-----------|
| `GET /api/tags` (`tags/routes.py`) | Fetch tag list for `<datalist>` + frequent chips. Same endpoint `TagPicker.jsx` calls via `listTags()`. |
| `GET /api/tag-rules` (`tag_rules/routes.py`) | Fetch rules for client-side matching → ★ chips. Same endpoint `Tags.jsx` calls via `getTagRules()`. |
| `POST /api/tags` (`tags/routes.py`) | Not used by bookmarklet. Tag creation stays in Settings. |
| `POST /api/articles` (`articles/routes.py`) | Save the article. Extended with `tag_ids` (new). `listen_later` already supported. |
| `.tag-chip`, `.tag-chip-remove` CSS | Selected tag chips in the popup — identical to `TagPicker.jsx` and `ArticleCard.jsx`. |
| `.btn-save-audio` CSS | Save audio button styling — identical to `Library.jsx`. |
| `.btn-primary` CSS | Save button styling. |
| `.flex-wrap-gap` CSS | Tag chip container layout. |
| CSS custom properties | All colors, radii, fonts via `--text`, `--bg-card`, `--border`, etc. Dark mode handled by the same `prefers-color-scheme` media queries. |
| `apply_auto_tags()` matching logic | Replicated as ~15 lines of JS for client-side rule matching (domain exact, title_contains, url_contains). |

### What's new (not reusable from existing code)

| New thing | Why it can't reuse existing code |
|-----------|----------------------------------|
| `tag_ids` on `POST /api/articles` | ~15 lines added to `create_article()`. Can't use `addArticleTag()` because the article doesn't exist yet at save time. |
| Chip management JS in popup | `TagPicker.jsx` is a Preact component — can't import into standalone HTML. The popup reimplements add/remove chip logic in vanilla JS (~30 lines). |
| `<datalist>` population | `TagPicker.jsx` uses `<select>`. The popup uses `<datalist>` (more appropriate for type-to-filter). Different HTML element, same data source. |

## Changes to Existing Files

### `frontend/public/bookmarklet.html`

Rewrite. Grows from ~90 lines / <2KB to ~150-200 lines / ~3-4KB. Loads the app's built CSS via `<link>` (same origin, cached). Inline JS only — no framework, no build step.

### `frontend/src/utils.js`

One-line change: update popup window height from `180` to `480`.

### `src/articles/routes.py`

Extend `create_article()` to accept and process the optional `tag_ids` list (~15 lines of additional code).

---

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| User has no tags | Tags input is empty, no autocomplete, no suggestions. Save works normally. Popup shows "Create tags in Settings to organize your articles." hint. |
| User has tags but no rules | No ★ suggestions. Recent/frequent tags still shown. |
| URL is a duplicate (409) | Show "Already saved." and auto-close. No tag form needed — the article is already in the library and can be tagged there. |
| Not logged in (401) | Redirect to `/?url=...` (same as current behavior). No tag form shown. |
| `/api/tags` fetch fails | Tags input still renders but `<datalist>` is empty (no autocomplete). No frequent tag suggestions. Save still works — tags are sent as IDs of whatever was selected before the failure. |
| `/api/tag-rules` fetch fails | No ★ rule-matched suggestions. Frequent tag chips and autocomplete still work (they come from `/api/tags`). |
| User types a tag name that doesn't match any existing tag | Input is ignored on Enter — no chip is created. Tags must exist first (created in Settings). This matches the app's existing convention: `TagPicker.jsx` only allows selecting existing tags, not creating new ones inline. |
| Tag deleted between popup load and save | `INSERT OR IGNORE` handles this; the invalid tag_id is silently skipped. |
| Very long title from `document.title` | Title input is editable and has `maxlength="500"` (matching the existing API constraint). Titles longer than 500 characters are truncated on pre-fill. |
| Popup opened from CSP-restricted page | Bookmarklet code is minimal (`open()` + `encodeURIComponent()`). The popup loads on Tasche's own origin, so CSP of the source page doesn't affect the popup's fetches. No change needed. |
| User clicks Save audio on a duplicate URL (409 re-process) | The existing `create_article()` re-process path already handles `listen_later` — if the article exists and `listen_later: true`, it sets `audio_status = 'pending'` alongside resetting article status. Popup shows "Re-saved! Audio will be generated." |
| User clicks Save audio but TTS is unavailable (no AI binding) | Article saves normally. Audio generation fails asynchronously in the queue (sets `audio_status = 'failed'`). The popup doesn't know — it shows "Saved! Audio will be generated." and closes. The user sees the failure in the reader view later. This is the existing behavior for all TTS failures. |

---

## Dark Mode

Handled automatically. The popup loads the app's stylesheet, which already defines dark mode variants for all reused classes (`.tag-chip`, `.btn-save-audio`, `.btn-primary`, CSS custom properties) via `@media (prefers-color-scheme: dark)`. The popup's minimal inline `<style>` only handles layout — no color definitions to duplicate.

---

## Acceptance Tests

### Test 1: Save with Tags

1. User has 3 existing tags: "python", "web", "tutorials"
2. User has a tag rule: `domain = "realpython.com"` → "python"
3. User visits `https://realpython.com/python-lists/` and clicks the bookmarklet
4. Popup opens. Title shows "Python Lists and List Manipulation – Real Python". URL shows `realpython.com/python-lists/`
5. Tags input is focused. Suggested tags section shows: `★ python` (rule-matched), `web` (frequent), `tutorials` (frequent)
6. User clicks `★ python` chip → chip appears in the input
7. User types `tu` → browser datalist suggests "tutorials (5)"
8. User selects suggestion and presses Enter → "tutorials" chip added
9. User presses Enter again (empty input) → form submits via Save
10. Status shows "Saved!" → popup auto-closes after 1.5s
11. In the library, the article appears with "python" and "tutorials" tags already applied

### Test 2: Save with Tags + Audio

1. User visits a long-form article and clicks the bookmarklet
2. Popup opens. User adds a tag via autocomplete
3. User clicks "🎧 Save audio" button (instead of Save)
4. Status shows "Saved! Audio will be generated." → popup auto-closes after 1.5s
5. In the library, the article appears with the tag applied and a headphone icon indicating audio is being generated
6. After processing completes, the article appears in the 🎧 filter tab with playable audio

### Test 3: Save Audio via Keyboard

1. User clicks the bookmarklet on any page
2. Popup opens with cursor in tags input
3. User presses `Ctrl+Enter` (or `⌘+Enter` on macOS) without adding any tags
4. Status shows "Saved! Audio will be generated." → popup auto-closes after 1.5s
5. Article is saved with `audio_status = 'pending'`, no tags
