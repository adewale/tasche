# Bookmarklet Popup Window: Lessons from Huffduffer & Pinboard

Research into what established services put in their bookmarklet popup windows, with a focus on tagging support. The goal is to inform what Tasche's bookmarklet popup could look like if upgraded from the current "fire-and-forget" save-only design to one that supports tagging at save time.

## Current State: Tasche's Bookmarklet

Tasche's current popup (`frontend/public/bookmarklet.html`) is a **pure status display** — no user interaction at all:

1. Opens a 420×180 popup
2. Immediately fires `POST /api/articles` with the URL and title
3. Shows "Saving to Tasche…" → "Saved!" → auto-closes after 1.5s
4. No fields, no tagging, no editing — just a confirmation flash

This is fast and frictionless, but it means users must open the app later to tag the article.

---

## Model 1: Pinboard — "Speed Through Focus"

Pinboard (Maciej Cegłowski, 2009–present) is the gold standard for bookmarklet-with-tagging UX. Its design philosophy: **minimal, fast, keyboard-first**.

### Bookmarklet Variants Offered

Pinboard offers multiple bookmarklets on its [howto page](https://pinboard.in/howto/), letting users choose their own speed/control tradeoff:

| Variant | Behavior | Window |
|---------|----------|--------|
| **Popup** | Opens form with pre-filled URL + title. Cursor focused on tags field. | ~610×350 |
| **Popup with tags** | Same form but includes a clickable tag cloud below | Larger (~700×550) |
| **Read later** | Saves immediately, no popup, no tagging | No window |
| **Same page** | Loads form in the current tab, redirects back after save | No popup |

### What's in the Popup

**Fields shown:**

| Field | Pre-populated? | Editable? | Notes |
|-------|---------------|-----------|-------|
| URL | Yes (from bookmarklet) | Yes | Current page URL, with UTM/tracking params stripped |
| Title | Yes (from `document.title`) | Yes | Called "description" in the API for Delicious compat |
| Description | Yes (selected text or meta description) | Yes | Called "extended" in the API |
| Tags | No (but cursor starts here) | Yes | Autocomplete from user's existing tags |
| Private | Default from user settings | Yes | Checkbox |
| Read Later | No | Yes | Checkbox |

**Tag interaction design:**

- **Cursor auto-focuses on tags** — URL and title are pre-filled, so the first thing you type goes into tags
- **Autocomplete** from the user's own tag vocabulary as you type
- **Two kinds of suggestions** (via the `posts/suggest` API endpoint):
  - **Recommended tags**: drawn from the user's own tagging history
  - **Popular tags**: tags used site-wide for that URL
- **Clickable tag cloud** (in the "popup with tags" variant): your most-used tags displayed as clickable chips
- **Tag separator**: spaces (not commas)
- **Tag constraints**: no whitespace, no commas, max 255 chars, tags starting with `.` are private

### Key UX Insight

The popup is optimized for the common case: **you just want to tag and save**. Title and URL are already correct 90% of the time, so the cursor skips straight to tags. A keyboard-fluent user can: click bookmarklet → type a few tag letters → Enter (accept suggestion) → Enter (save) → popup closes. Under 3 seconds.

The "Particular Pinboard" enhanced bookmarklet ([joelcarranza/particular-pinboard](https://github.com/joelcarranza/particular-pinboard)) goes further:
- Cleans up page titles (strips SEO junk)
- Pre-fills description with selected text or meta description
- Auto-suggests tags based on keyword rules the user defines

---

## Model 2: Huffduffer — "Server Does the Work"

Huffduffer (Jeremy Keith, 2008–present) is an audio bookmarking service. Its bookmarklet philosophy, [articulated by Keith himself](https://adactio.com/journal/tags/bookmarklets):

> "Have the bookmarklet pop open a new browser window at your service, passing in the URL of the current page. Then do all the heavy lifting on your server."

### Bookmarklet Design

The bookmarklet is deliberately **dumb** — it's just a URL redirect:

```
https://huffduffer.com/add?page={currentURL}
```

The `/add` page on Huffduffer's server does all the extraction work (via server-side CURL):
- Finds audio files on the page (MP3, M4A via `<a>`, `<audio>`, `<link rel="enclosure">`, `og:audio`)
- Extracts title, description from page metadata
- Discovers RSS feeds and parses enclosures
- Pre-fills the form with everything it found

### What's in the Popup

**Window size:** 360×480 (taller than Pinboard — needs room for audio selection)

**Fields shown:**

| Field | Pre-populated? | Editable? | Notes |
|-------|---------------|-----------|-------|
| Audio URL | Yes (auto-detected) | Yes | If multiple audio files found, shows a selectable list |
| Title | Yes (from page) | Yes | |
| Description | Yes (from page metadata) | Yes | |
| Tags | Sometimes (from page metadata) | Yes | Space or comma separated |

**Tag interaction:**
- Tags are free-text input (no autocomplete or suggestions)
- Each tag generates its own podcast feed — so tags have functional meaning beyond organization
- Users can subscribe to any tag, any user's feed, or any user+tag combination

### Key UX Insight

Keith's philosophy is about **resilience and maintainability**:

1. **Server-side extraction means the bookmarklet never needs updating.** Improve the server logic, and every user's bookmarklet gets smarter automatically.
2. **The bookmarklet is CSP-resistant.** Since it just opens a URL (no JS injection into the page), it works even on sites with strict `script-src` policies. Keith explicitly contrasts this with Instapaper's approach.
3. **Show everything, let users correct.** The form is pre-filled but fully editable. The server's best guess is shown, and the user can override any field.

---

## Synthesis: What This Means for Tasche

### Design Options (Spectrum of Complexity)

**Option A: Keep current design, tag later**
- Current behavior: fire-and-forget save, tag in the app
- Pro: Fastest possible save. Simplest code.
- Con: Tagging friction — most articles end up untagged

**Option B: Pinboard "Read Later" + current approach (two bookmarklets)**
- Offer the current instant-save bookmarklet AND a second "save with tags" variant
- Let users choose their speed/control tradeoff
- Pro: No regression for speed-oriented users
- Con: Two bookmarklets to explain

**Option C: Pinboard-style popup form (recommended)**
- Replace the status-only popup with a small form
- Pre-fill URL and title (already passed by bookmarklet)
- Focus cursor on a tags input field
- Show the user's existing tags as suggestions (fetch from `/api/tags`)
- "Save" button submits and closes
- Pro: Tagging at point of capture, when context is freshest
- Con: Larger popup, slightly slower save, more code

**Option D: Huffduffer-style server-rendered form**
- Popup opens a server-rendered page at `/bookmarklet?url=...`
- Server fetches the page, extracts metadata, suggests tags (via tag rules)
- Pro: Smart suggestions, CSP-resistant, upgradable without changing bookmarklet
- Con: Slower (server must fetch the page before showing form), more backend work

### Recommended Design: Option C — Popup with Tag Input

Based on the Pinboard model, the popup should:

1. **Pre-fill and show** (read-only or editable):
   - Page title (editable, in case `document.title` is messy)
   - URL (shown but not usually edited)

2. **Focus on tags** (primary interaction):
   - Text input with autocomplete from user's existing tags
   - Fetch tag list from `/api/tags` when popup opens
   - Clickable chips showing the user's most-used tags (top 10-15)
   - Matching Tasche's existing tag data model

3. **One-click save**:
   - "Save" button (or Enter key) submits
   - Auto-closes on success (keep the 1.5s delay)
   - Keep the "Already saved" / error states

4. **Window dimensions**:
   - Increase from 420×180 to roughly **420×360** to fit the tag input and suggestions
   - Pinboard uses 610×350; Huffduffer uses 360×480
   - Tasche can stay narrower since there's no description field

5. **Keyboard flow** (Pinboard's killer UX):
   - Popup opens → cursor in tags input
   - Type tag prefix → autocomplete dropdown appears
   - Enter → accept suggestion (add tag chip)
   - Tab or Enter (when tags input is empty) → submit
   - Entire flow possible without touching the mouse

### Tag Suggestion Sources

Drawing from both Pinboard's and Huffduffer's approaches:

| Source | How | Priority |
|--------|-----|----------|
| **User's existing tags** | Fetch from `/api/tags` on popup load | Primary — autocomplete |
| **Tag rules** | If Tasche's auto-tagging rules match this URL/domain, pre-fill those tags | Secondary — pre-fill |
| **Recently used tags** | Show last ~5 tags used as quick-click chips | Convenience |
| **Most used tags** | Show top ~10 tags as a clickable cloud | Discovery |

### Implementation Notes

- The bookmarklet code itself (`getBookmarkletCode()` in `utils.js`) doesn't need to change — it already passes URL and title
- The popup page (`bookmarklet.html`) grows from a status display to a small form
- Need to fetch `/api/tags` from the popup (same-origin, session cookie works)
- Tag rules matching could happen client-side (fetch rules + match URL) or server-side (new endpoint)
- Keep the instant-save path: if the user hits Save immediately without adding tags, it works exactly like today

---

## Sources

- [Jeremy Keith — Bookmarklets (Medium)](https://adactio.medium.com/bookmarklets-11ef72df4b4d)
- [Jeremy Keith — Journal entries tagged "bookmarklets"](https://adactio.com/journal/tags/bookmarklets)
- [Huffduffer — About](https://huffduffer.com/about)
- [Huffduffer iOS Bookmarklet Gist](https://gist.github.com/jancbeck/6c21f3fce51122c8fb26)
- [Pinboard — Howto / Bookmarklet](https://pinboard.in/howto/)
- [Pinboard API v1 Documentation](https://pinboard.in/api/)
- [Particular Pinboard (enhanced bookmarklet)](https://github.com/joelcarranza/particular-pinboard)
- [Pinboard Bookmarklet — enhanced with description and referrer](https://gist.github.com/jsit/85bc9a1759b62bec14fb5d05f20dffe9)
- [How I Use Pinboard — MacStories](https://www.macstories.net/stories/how-i-use-pinboard/)
- [A Beginner's Guide to Pinboard — The Sweet Setup](https://thesweetsetup.com/articles/a-beginners-guide-to-pinboard/)
- [Six Colors — Exploring Huffduffer](https://sixcolors.com/member/2019/01/the-hackett-file-exploring-huffduffer/)
- [The History of Pinboard — dewey.](https://getdewey.co/blog/the-history-of-pinboardin/)
