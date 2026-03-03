# Bookmarklet Tagging Spec

> Upgrade the bookmarklet popup from a status-only flash to a lightweight form that lets users tag articles at save time — when context is freshest.

**Status:** Draft
**Date:** 2026-03-03

---

## Problem

The current bookmarklet popup (`frontend/public/bookmarklet.html`) is fire-and-forget: it saves the URL, shows "Saved!", and auto-closes. There is no opportunity to add tags. Users must open the app, find the article, and tag it after the fact — a workflow almost nobody does. The result: most articles end up untagged, and the tag system goes underused.

Pinboard solved this in 2009 by focusing the bookmarklet cursor on the tags field. Huffduffer solved it by having the server pre-fill everything it can infer. Both services prove that tagging at point of capture is the critical moment.

---

## Design Principles

1. **Tag-first interaction.** URL and title are pre-filled; the cursor starts in the tags input. Copying Pinboard's insight: the thing the user needs to _decide_ is what tags to apply, not what URL they're on.

2. **Zero tags is fine.** Hitting Save (or Enter) with no tags works exactly like the current bookmarklet. No regression for speed-oriented users.

3. **Suggest, don't require.** Show the user's existing tags, highlight ones that match via tag rules, but never force tagging.

4. **Stay small and self-contained.** The popup is still a single HTML file with inline CSS/JS. No framework, no build step, no external dependencies. Target: under 8KB.

5. **Keyboard-completable.** The entire flow — open popup, type tag prefix, accept suggestion, save — must work without the mouse. Target: under 5 seconds for a user who knows their tags.

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
│  ┌──────────────────────────────────┐│
│  │            Save                  ││
│  └──────────────────────────────────┘│
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
| **Save button** | Submits the form. Disabled while saving. Shows spinner during request. |
| **Status area** | Below the button. Shows "Saved!" / "Already saved." / error messages. On success, auto-closes after 1.5s (same as current behavior). |

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
  ├─ collect: url, title, tag_ids[]
  │
  ├─ POST /api/articles ─────────────────► creates article (status: pending)
  │     { url, title, tag_ids }             returns { id, status }
  │
  ├─ show "Saved!" status
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

Accept an optional `tag_ids` field in the request body:

```json
{
  "url": "https://example.com/article",
  "title": "Example Article",
  "tag_ids": ["abc123", "def456"]
}
```

**Behavior:**

- `tag_ids` is optional. If omitted or empty, behaves exactly as today.
- Each tag ID is validated: must belong to the authenticated user. Invalid IDs are silently skipped (not an error — the tag may have been deleted between the popup loading and the user clicking Save).
- Tag associations are inserted via `INSERT OR IGNORE INTO article_tags` (same as `apply_auto_tags()`), so duplicates with auto-tagging are harmless.
- Tag association happens immediately at article creation, before enqueueing the processing job. When the queue consumer later runs `apply_auto_tags()`, the `INSERT OR IGNORE` ensures no duplicates.

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

---

## Dark Mode

The popup respects `prefers-color-scheme: dark` (same as current implementation). All new elements — input fields, chips, autocomplete dropdown, suggested tags — have dark mode variants defined in the inline `<style>` block.

---

## Acceptance Test

1. User has 3 existing tags: "python", "web", "tutorials"
2. User has a tag rule: `domain = "realpython.com"` → "python"
3. User visits `https://realpython.com/python-lists/` and clicks the bookmarklet
4. Popup opens. Title shows "Python Lists and List Manipulation – Real Python". URL shows `realpython.com/python-lists/`
5. Tags input is focused. Suggested tags section shows: `★ python` (rule-matched), `web` (frequent), `tutorials` (frequent)
6. User clicks `★ python` chip → chip appears in the input
7. User types `tu` → autocomplete shows "tutorials (5)"
8. User presses Enter → "tutorials" chip added to input
9. User presses Enter again (empty input) → form submits
10. Status shows "Saved!" → popup auto-closes after 1.5s
11. In the library, the article appears with "python" and "tutorials" tags already applied
