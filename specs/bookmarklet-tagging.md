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

4. **Stay small and self-contained.** The popup is still a single HTML file with inline CSS/JS. No framework, no build step, no external dependencies. Target: under 8KB.

5. **Keyboard-completable.** The entire flow — open popup, type tag prefix, accept suggestion, save — must work without the mouse. Target: under 5 seconds for a user who knows their tags.

6. **Capture intent, not just URL.** The moment of saving is when the user knows _why_ they're saving. "I want to read this on the train" (save) vs. "I want to listen to this on my walk" (save + listen later) are different intents. The popup should let users express both.

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
│  ┌──────────────────────────────────┐│
│  │ javascript, web ×               ││
│  │ ┌─ autocomplete ──────────────┐ ││
│  │ │  javascript  (12 articles)  │ ││
│  │ │  java        (3 articles)   │ ││
│  │ └────────────────────────────-┘ ││
│  └──────────────────────────────────┘│
│                                      │
│  Suggested                           │
│  ┌────────┐ ┌─────┐ ┌───────────┐  │
│  │ ★ web  │ │ dev │ │ tutorials │  │
│  └────────┘ └─────┘ └───────────┘  │
│                                      │
│  ┌─────────────────┐ ┌────────────┐ │
│  │      Save       │ │  🎧 Listen │ │
│  └─────────────────┘ └────────────┘ │
│                                      │
└──────────────────────────────────────┘
```

### Elements

| Element | Behavior |
|---------|----------|
| **Title** | Pre-filled from `document.title` (passed via query param). Editable single-line text input. Sent as `title` in the POST body. |
| **URL display** | Shown as static text (not editable). Truncated with ellipsis if longer than ~50 chars. Shows domain prominently. Provides visual confirmation the user is saving the right page. |
| **Tags input** | Text input with inline chips for already-added tags. Cursor starts here on page load. Typing filters the autocomplete dropdown. |
| **Autocomplete dropdown** | Appears below the tags input when typing. Shows matching tags from the user's tag list, sorted by relevance (see §Autocomplete). Each entry shows the tag name and article count. |
| **Suggested tags** | Clickable chips shown below the input. Two sources: tag rules that match this URL/domain/title (marked with ★) and the user's most-used tags. Click to add. |
| **Save button** | Submits the form with `listen_later: false`. Disabled while saving. Shows spinner during request. |
| **Listen button** | Labeled "🎧 Listen". Submits the form with `listen_later: true`, which saves the article _and_ queues TTS generation. Same disabled/spinner behavior as Save. Visually secondary to the Save button (outlined style, not filled). |
| **Status area** | Below the buttons. Shows "Saved!" / "Saved! Audio queued." / "Already saved." / error messages. On success, auto-closes after 1.5s (same as current behavior). |

### Keyboard Shortcuts

| Key | Context | Action |
|-----|---------|--------|
| Any character | Tags input | Filters autocomplete dropdown |
| `↓` / `↑` | Autocomplete open | Navigate suggestions |
| `Enter` | Autocomplete open, suggestion highlighted | Accept suggestion (add tag chip) |
| `Enter` | Autocomplete closed or empty input | Submit form (save) |
| `Backspace` | Tags input, cursor at start | Remove last tag chip |
| `Tab` | Tags input | Accept top autocomplete suggestion if one is shown |
| `,` or `Space` | Tags input, text present | Commit current text as a tag (if it matches an existing tag) |
| `Escape` | Autocomplete open | Close dropdown |
| `Escape` | Autocomplete closed | Close popup window |
| `Ctrl+Enter` / `⌘+Enter` | Anywhere in form | Save + Listen Later (equivalent to clicking the Listen button) |

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
  ├─ GET /api/tags ──────────────────────► returns all user tags
  │     │                                   with article_count
  │     └─ store in memory as tagList
  │
  ├─ GET /api/bookmarklet/suggestions ───► returns { matched_rules, recent_tags }
  │     ?url=...&title=...                  for this URL
  │     │
  │     ├─ render ★ chips for matched rule tags
  │     └─ render chips for recent/frequent tags
  │
  └─ ready for user input
```

Both fetches happen in parallel. The tags input is functional immediately (the autocomplete works as soon as `/api/tags` returns). Suggested tags appear as soon as `/api/bookmarklet/suggestions` returns.

If either fetch fails, the popup degrades gracefully — the tags input still works for manual entry, and Save still works (tags are sent as names, resolved server-side).

### On Save

```
user clicks Save (or presses Enter)
  │
  ├─ collect: url, title, tag_ids[], listen_later
  │            (listen_later = false for Save, true for Listen)
  │
  ├─ POST /api/articles ─────────────────► creates article (status: pending)
  │     { url, title, tag_ids,              if listen_later: also sets
  │       listen_later }                    audio_status = 'pending'
  │                                         returns { id, status }
  │
  ├─ show status message:
  │     listen_later=false → "Saved!"
  │     listen_later=true  → "Saved! Audio queued."
  ├─ auto-close after 1.5s
  └─ done
```

---

## New API Endpoint

### `GET /api/bookmarklet/suggestions`

Returns tag suggestions for a given URL. Used exclusively by the bookmarklet popup.

**Query parameters:**

| Param | Required | Description |
|-------|----------|-------------|
| `url` | Yes | The page URL being saved |
| `title` | No | The page title (for `title_contains` rule matching) |

**Response:**

```json
{
  "matched_rules": [
    { "tag_id": "abc123", "tag_name": "python", "match_type": "domain", "pattern": "docs.python.org" },
    { "tag_id": "def456", "tag_name": "tutorial", "match_type": "title_contains", "pattern": "tutorial" }
  ],
  "recent_tags": [
    { "id": "ghi789", "name": "web", "article_count": 34 },
    { "id": "jkl012", "name": "javascript", "article_count": 28 }
  ]
}
```

**`matched_rules`** — Tags whose rules match the given URL/title/domain. The server runs the same matching logic as `apply_auto_tags()` in `src/articles/processing.py` (domain exact/glob match, `title_contains`, `url_contains`) but returns the matches instead of applying them. These tags are shown with a ★ indicator in the popup to signal "Tasche thinks this tag applies."

**`recent_tags`** — The user's most-used tags (by `article_count`), excluding any already present in `matched_rules`. Capped at 8 tags. These give quick access to the user's common tags.

**Implementation:** New route in `src/tags/routes.py` (or a new `src/bookmarklet/routes.py` if preferred). Reuses the tag rule evaluation logic from `processing.py` — extract it into a shared helper.

**Auth:** Requires session cookie (same as all `/api/` endpoints). Returns 401 if not authenticated.

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

The autocomplete operates entirely client-side against the tag list fetched on popup load. No server round-trips per keystroke.

### Matching

Given user input `q` and a tag name `name`:

1. **Prefix match** (highest priority): `name.toLowerCase().startsWith(q.toLowerCase())`
2. **Substring match** (secondary): `name.toLowerCase().includes(q.toLowerCase())`

Results are sorted: prefix matches first (by `article_count` descending), then substring matches (by `article_count` descending). Maximum 6 suggestions shown.

### Display

Each autocomplete entry shows:

```
  tag-name                    (12)
```

Tag name on the left, article count in parentheses on the right (dimmed). The article count helps the user distinguish between similarly-named tags and signals which tags are well-established.

### Tag Creation

If the user types a tag name that doesn't match any existing tag, the autocomplete shows:

```
  Create "newtag"              +
```

Selecting this option creates the tag via `POST /api/tags` inline, adds it to the local tag list, and inserts it as a chip in the input. This matches the behavior of the existing tag creation flow in the app's Tags view.

---

## Suggested Tags Section

Below the tags input, a row of clickable chips. Two categories, visually distinguished:

### Rule-matched tags (★)

Tags whose rules match this URL. Rendered with a ★ prefix and a subtle highlight background (e.g., a faint accent color border). These are the strongest suggestions — the user explicitly configured these rules.

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
| **Listen** (secondary) | "🎧 Listen" | `listen_later: true` | "Saved! Audio queued." |

The Save button is primary (filled, prominent). The Listen button is secondary (outlined, slightly smaller). This keeps the default path fast — Enter key triggers Save, not Listen — while making audio a single extra click away.

### Keyboard: `Ctrl+Enter` / `⌘+Enter` for Listen

Enter submits via Save (the common case). `Ctrl+Enter` / `⌘+Enter` submits via Listen. This matches the "modifier key = enhanced action" convention used by chat apps (send vs. send + something) and avoids adding a new shortcut to memorize.

### No Backend Changes

The `listen_later` field already exists on `POST /api/articles`. The bookmarklet popup simply needs to send `listen_later: true` when the Listen button is clicked. No new endpoint, no schema change, no queue changes.

### Status Message

When `listen_later: true`:
- Success: **"Saved! Audio queued."** — confirms both the save and the audio intent in a single message. The 🎧 icon mirrors the library's headphone filter tab, reinforcing the visual language.
- The popup still auto-closes after 1.5s. Audio generation happens asynchronously in the queue; the popup doesn't wait for it.

---

## Changes to Existing Files

### `frontend/public/bookmarklet.html`

Complete rewrite of this file. Grows from ~90 lines / <2KB to ~300-400 lines / ~6-8KB. Still fully self-contained: inline CSS, inline JS, no external dependencies, no build step.

### `frontend/src/utils.js`

One-line change: update popup window height from `180` to `480`.

### `src/tags/routes.py` (or new `src/bookmarklet/routes.py`)

New `GET /api/bookmarklet/suggestions` endpoint (~40-60 lines). Add a router and mount it in `entry.py`.

### `src/articles/processing.py`

Extract the rule-matching loop from `apply_auto_tags()` into a reusable function (e.g., `match_tag_rules(rules, domain, title, url) -> set[str]`) so both the processing pipeline and the suggestions endpoint can use it.

### `src/articles/routes.py`

Extend `create_article()` to accept and process the optional `tag_ids` list (~15-20 lines of additional code).

### `src/entry.py`

Mount the new bookmarklet suggestions router.

---

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| User has no tags | Tags input is empty, no autocomplete, no suggestions. Save works normally. Popup shows "Create tags in Settings to organize your articles." hint. |
| User has tags but no rules | No ★ suggestions. Recent/frequent tags still shown. |
| URL is a duplicate (409) | Show "Already saved." and auto-close. No tag form needed — the article is already in the library and can be tagged there. |
| Not logged in (401) | Redirect to `/?url=...` (same as current behavior). No tag form shown. |
| `/api/tags` fetch fails | Tags input still renders. User can type tag names; they'll be resolved on save. No autocomplete or suggestions. |
| `/api/bookmarklet/suggestions` fetch fails | No suggested tags section. Tags input with autocomplete still works. |
| User types a tag name that exists but they don't click the autocomplete suggestion | On save, resolve the raw text against the user's tag list by name (case-insensitive). If it matches, use that tag's ID. If not, silently ignore (don't auto-create from raw text — only from the explicit "Create" autocomplete entry). |
| Tag deleted between popup load and save | `INSERT OR IGNORE` handles this; the invalid tag_id is silently skipped. |
| Very long title from `document.title` | Title input is editable and has `maxlength="500"` (matching the existing API constraint). Titles longer than 500 characters are truncated on pre-fill. |
| Popup opened from CSP-restricted page | Bookmarklet code is minimal (`open()` + `encodeURIComponent()`). The popup loads on Tasche's own origin, so CSP of the source page doesn't affect the popup's fetches. No change needed. |
| User clicks Listen on a duplicate URL (409 re-process) | The existing `create_article()` re-process path already handles `listen_later` — if the article exists and `listen_later: true`, it sets `audio_status = 'pending'` alongside resetting article status. Popup shows "Re-saved! Audio queued." |
| User clicks Listen but TTS is unavailable (no AI binding) | Article saves normally. Audio generation fails asynchronously in the queue (sets `audio_status = 'failed'`). The popup doesn't know — it shows "Saved! Audio queued." and closes. The user sees the failure in the reader view later. This is the existing behavior for all TTS failures. |

---

## Dark Mode

The popup respects `prefers-color-scheme: dark` (same as current implementation). All new elements — input fields, chips, autocomplete dropdown, suggested tags — have dark mode variants defined in the inline `<style>` block.

---

## Acceptance Tests

### Test 1: Save with Tags

1. User has 3 existing tags: "python", "web", "tutorials"
2. User has a tag rule: `domain = "realpython.com"` → "python"
3. User visits `https://realpython.com/python-lists/` and clicks the bookmarklet
4. Popup opens. Title shows "Python Lists and List Manipulation – Real Python". URL shows `realpython.com/python-lists/`
5. Tags input is focused. Suggested tags section shows: `★ python` (rule-matched), `web` (frequent), `tutorials` (frequent)
6. User clicks `★ python` chip → chip appears in the input
7. User types `tu` → autocomplete shows "tutorials (5)"
8. User presses Enter → "tutorials" chip added to input
9. User presses Enter again (empty input) → form submits via Save
10. Status shows "Saved!" → popup auto-closes after 1.5s
11. In the library, the article appears with "python" and "tutorials" tags already applied

### Test 2: Save with Tags + Listen Later

1. User visits a long-form article and clicks the bookmarklet
2. Popup opens. User adds a tag via autocomplete
3. User clicks "🎧 Listen" button (instead of Save)
4. Status shows "Saved! Audio queued." → popup auto-closes after 1.5s
5. In the library, the article appears with the tag applied and a headphone icon indicating audio is being generated
6. After processing completes, the article appears in the 🎧 filter tab with playable audio

### Test 3: Listen Later via Keyboard

1. User clicks the bookmarklet on any page
2. Popup opens with cursor in tags input
3. User presses `Ctrl+Enter` (or `⌘+Enter` on macOS) without adding any tags
4. Status shows "Saved! Audio queued." → popup auto-closes after 1.5s
5. Article is saved with `audio_status = 'pending'`, no tags
