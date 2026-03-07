# Tagging Spec: Minimal Implementation

_Last updated: 2026-03-07_

## Goal

Give the user a way to organize articles with tags. Ship the smallest useful version: create tags, apply them to articles, filter by them, manage them. No AI, no auto-tagging rules, no bulk operations — those come later.

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

`GET /api/articles` gains an optional `tag` query parameter:

```
GET /api/articles?tag=abc123&status=unread&limit=20
```

- Filters to articles that have the given tag.
- Implemented via `INNER JOIN article_tags ON ... WHERE tag_id = ?`.
- Single-tag filtering only in this phase. Multi-tag comes later.

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

In the library grid, each article card shows its tags as small clickable chips below the title/domain line. Clicking a chip navigates to the library filtered by that tag.

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
| Multi-tag filtering | Single-tag filtering covers the common case. Multi-tag adds query builder UI complexity. |
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
