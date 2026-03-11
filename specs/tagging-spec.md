# Tagging Spec: Minimal Implementation

_Last updated: 2026-03-08_

## Goal

Give the user a way to organize articles with tags. Ship the smallest useful version: create tags, apply them to articles, filter by them (including multi-tag intersection), manage them. Autocomplete prevents tag sprawl. No AI, no auto-tagging rules, no bulk operations — those come later.

---

## Data Model

### D1 Schema

```sql
CREATE TABLE tags (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, name)
);

CREATE TABLE article_tags (
    article_id TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    tag_id     TEXT NOT NULL REFERENCES tags(id)     ON DELETE CASCADE,
    PRIMARY KEY (article_id, tag_id)
);

CREATE INDEX idx_article_tags_tag ON article_tags(tag_id);
```

**Design decisions:**

- **Flat tags, no hierarchy.** Research confirms this is the right starting point — every successful read-it-later app starts flat. Saved filters can substitute for hierarchy later.
- **IDs are `secrets.token_urlsafe(16)`**, matching Tasche's existing convention.
- **Tag names: max 100 characters.** Validated at the API boundary.
- **Case-sensitive storage, case-insensitive uniqueness.** The `UNIQUE(user_id, name)` constraint uses D1's default `NOCASE` collation. The original casing the user typed is preserved for display.
- **`ON DELETE CASCADE`** on both foreign keys — deleting a tag removes all associations; deleting an article removes its tag links.
- **No color, no description, no metadata on tags.** Resist the urge. Tags are just names.

---

## API

All endpoints require authentication (`get_current_user` dependency). All handlers are `async def`.

### Tag CRUD — `/api/tags`

#### `POST /api/tags`
Create a tag.

```
Request:  { "name": "python" }
Response: 201 { "id": "abc123", "name": "python", "created_at": "..." }
```

- Trim whitespace from name.
- Reject empty or >100 chars → 422.
- Reject duplicate name (same user, case-insensitive) → 409.

#### `GET /api/tags`
List all tags for the user, with article counts.

```
Response: 200 [
  { "id": "abc123", "name": "python", "article_count": 12 },
  { "id": "def456", "name": "rust", "article_count": 3 }
]
```

- Ordered alphabetically by name.
- `article_count` via `LEFT JOIN article_tags ... GROUP BY`.

#### `PATCH /api/tags/{tag_id}`
Rename a tag.

```
Request:  { "name": "Python" }
Response: 200 { "id": "abc123", "name": "Python" }
```

- Same validation as create (trim, empty, length, duplicate).
- 404 if tag doesn't exist or belongs to another user.

#### `DELETE /api/tags/{tag_id}`
Delete a tag and all its associations.

```
Response: 204
```

- 404 if not found or wrong user.
- Cascade handles association cleanup.

### Article–Tag Associations — `/api/articles/{article_id}/tags`

#### `POST /api/articles/{article_id}/tags`
Apply a tag to an article.

```
Request:  { "tag_id": "abc123" }
Response: 201 { "article_id": "...", "tag_id": "abc123" }
```

- 404 if article or tag not found / wrong user.
- 409 if association already exists.

#### `DELETE /api/articles/{article_id}/tags/{tag_id}`
Remove a tag from an article.

```
Response: 204
```

- 404 if association doesn't exist.

#### `GET /api/articles/{article_id}/tags`
List tags on an article.

```
Response: 200 [
  { "id": "abc123", "name": "python" },
  { "id": "def456", "name": "rust" }
]
```

### Article Listing — Tag Filtering

`GET /api/articles` accepts one or more `tag` query parameters:

```
GET /api/articles?tag=abc123&status=unread&limit=20
GET /api/articles?tag=abc123&tag=def456&limit=20
```

- **Single tag:** `INNER JOIN article_tags ON ... WHERE tag_id = ?`.
- **Multiple tags (intersection):** articles must have _all_ specified tags. Implemented via `INNER JOIN article_tags ... WHERE tag_id IN (?, ?) GROUP BY articles.id HAVING COUNT(DISTINCT article_tags.tag_id) = ?` where the final `?` is the number of tags provided.
- **Limit: up to 4 tags.** Pinboard proved this ceiling is sufficient for power users. Reject >4 with 400.
- Combines with existing `status`, `limit`, `offset`, and `search` parameters.

### Tags Inline on Articles

The `GET /api/articles` response includes tags on each article to avoid N+1 queries:

```json
{
  "id": "article_1",
  "title": "...",
  "tags": [{"id": "abc123", "name": "python"}, {"id": "def456", "name": "rust"}]
}
```

Implemented via a correlated subquery using `json_group_array(json_object('id', t.id, 'name', t.name))` in the SELECT. Parse the JSON string in Python before returning.

---

## Frontend

### Tags View (`#/tags`)

A management screen for all tags.

```
┌─────────────────────────────────────────────┐
│  [← Back]  Tags                             │
├─────────────────────────────────────────────┤
│  ┌─────────────────────────────────┬──────┐ │
│  │ + New tag name...               │ Add  │ │
│  └─────────────────────────────────┴──────┘ │
│                                             │
│  python                        12 articles  │
│  rust                           3 articles  │
│  cloudflare                     8 articles  │
│                                             │
│  Tap a tag to filter your library.          │
└─────────────────────────────────────────────┘
```

**Behavior:**
- Text input + "Add" button to create a tag.
- Each row: tag name, article count, rename (inline edit) and delete (with confirmation) controls.
- Clicking the tag name navigates to `#/?tag={tag_id}` (library filtered by that tag).

### Tag Chips on Article Cards

In the library grid, each article card shows its tags as small clickable chips below the title/domain line.

```
┌──────────────────────────────────┐
│  Article Title                   │
│  example.com · 5 min             │
│  [python] [cloudflare]           │
└──────────────────────────────────┘
```

**Behavior:**
- Chips render in alphabetical order, matching the tag list sort.
- Clicking a chip navigates to `#/?tag={tag_id}` (library filtered by that tag).
- If an article has more than 3 tags, show the first 3 and a `+N` overflow indicator. This prevents cards from growing unbounded.
- Chip styling: small, muted background, readable text. No tag colors (consistent with "tags are just names" decision).
- When a tag filter is active, the matching chip is visually highlighted (e.g. bolder weight or filled background) so the user can see why this article is in the filtered view.

### Tag Picker in Reader View

When reading an article, a tag icon opens a picker to add/remove tags.

```
┌─────────────────────────────────────────────┐
│  Tags on this article:                      │
│  [python ×] [rust ×]                        │
│                                             │
│  ┌───────────────────────────────────────┐  │
│  │ Type to filter or create...           │  │
│  ├───────────────────────────────────────┤  │
│  │ cloudflare                            │  │
│  │ design                                │  │
│  │ + Create "new-tag"                    │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

**Behavior:**
- Shows currently applied tags as removable chips (click × to remove).
- Text input with a dropdown showing existing tags, filtered as the user types.
- Already-applied tags are hidden from the suggestion list.
- If the typed text doesn't match any existing tag, show a "Create {name}" option at the bottom that creates the tag and applies it in one step.
- **Autocomplete prioritizes existing tags.** This is the single most important UX decision for preventing tag sprawl.
- On mobile: disable `autocapitalize`, `autocorrect`, and `spellcheck` on the input.

### Tag Autocomplete

Autocomplete is the shared interaction model used wherever a user picks a tag — the Tag Picker in Reader view and the tag input in the Tags management view.

**How it works:**
1. **On focus:** Show all existing tags (minus already-applied ones), sorted alphabetically. No typing required — the full list is visible immediately.
2. **As the user types:** Filter the list to tags whose names contain the typed substring (case-insensitive). Highlight the matching portion in each suggestion.
3. **Keyboard navigation:** Arrow keys move through suggestions, `Enter` selects the highlighted tag, `Escape` closes the dropdown.
4. **Create-on-the-fly:** If the typed text has no exact match, a "Create {name}" option appears at the bottom of the list, visually distinct from existing tags. Selecting it creates the tag via `POST /api/tags` and immediately applies it.
5. **No duplicate creation:** If the typed text matches an existing tag (case-insensitive), the "Create" option does not appear — the existing tag is offered instead.

**Why this matters:** Research shows autocomplete is table-stakes UX. Pinboard, Raindrop, and Readwise all do it. Without it, users create "javascript", "JavaScript", and "JS" as separate tags. Autocomplete that strongly favours existing tags is the primary defence against tag sprawl — more effective than merge or cleanup tools after the fact.

### State Management

A global `tags` signal (via `@preact/signals`) holds the full tag list, refreshed on create/rename/delete. Article objects include their tags inline from the API response.

---

## Code Structure

```
src/tags/
├── __init__.py
├── routes.py          # Tag CRUD router + article-tag association router
```

- **`routes.py`** exports two routers:
  - `router` — mounted at `/api/tags` (CRUD)
  - `article_tags_router` — mounted at `/api/articles` (associations)

- Registered in `entry.py`:
  ```python
  from tags.routes import router as tags_router, article_tags_router
  app.include_router(tags_router, prefix="/api/tags", tags=["tags"])
  app.include_router(article_tags_router, prefix="/api/articles", tags=["tags"])
  ```

```
frontend/src/
├── views/Tags.jsx           # Tag management view
├── components/TagPicker.jsx # Tag picker for reader view
├── api.js                   # Tag API functions
├── state.js                 # tags signal
```

---

## What's Explicitly Out of Scope

These are all good features. They are not in this spec.

| Feature | Why not now |
|---------|-----------|
| Auto-tagging rules (domain/URL/title patterns) | Adds a `tag_rules` table, rule engine in the queue consumer, and a rules management UI. Spec it separately. |
| AI-suggested tags | Requires prompt design, Workers AI integration in the queue consumer, and a suggestion UX. Spec it separately. |
| Tag merge | Rename covers 80% of the need. Merge requires a picker UI for selecting the target tag. |
| Bulk tagging from library | Requires a selection mode in the library grid (checkboxes, action bar). |
| Keyboard shortcuts (`T` to tag) | Nice but not minimal. Add after the core works. |
| Tag colors | Adds a color picker, storage, and rendering complexity for marginal value. |
| Saved filters / smart views | A separate feature that combines tags with other criteria. |

---

## Migration

Single migration file: `NNNN_tags.sql`

```sql
CREATE TABLE IF NOT EXISTS tags (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_user_name ON tags(user_id, name);

CREATE TABLE IF NOT EXISTS article_tags (
    article_id TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    tag_id     TEXT NOT NULL REFERENCES tags(id)     ON DELETE CASCADE,
    PRIMARY KEY (article_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_article_tags_tag ON article_tags(tag_id);
```

---

## Testing

Unit tests in `tests/unit/test_tags.py` covering:

1. **Tag CRUD**: create, list (with counts), rename, delete.
2. **Validation**: empty name, too-long name, duplicate name (case-insensitive).
3. **Associations**: add tag to article, remove tag, list tags on article, duplicate association (409).
4. **Ownership**: cannot access another user's tags or articles (404).
5. **Cascade**: deleting a tag removes associations; deleting an article removes associations.
6. **Article listing**: `?tag=` filter returns correct subset; tags appear inline on articles.
7. **Multi-tag filtering**: single-tag subquery, multi-tag `HAVING COUNT`, parameter binding, 4-tag limit, 5+ rejected with 400, combines with `reading_status`.
8. **Property-based tests** (Hypothesis): tag name trimming invariant, create-then-rename roundtrip, long name rejection, duplicate detection idempotency, tag count limit invariant.
