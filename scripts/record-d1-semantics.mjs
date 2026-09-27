#!/usr/bin/env node
// Record how real (local) D1 answers `stmt.bind(...).run()` for the write
// patterns Tasche relies on, and write them to tests/fixtures/d1_run_semantics.json.
//
// tests/unit/test_mock_fidelity.py compares the Python test doubles (MockD1,
// SqliteD1) against this recording, so their expected values come from D1
// itself rather than from the mock author's belief.
//
// Local only: `wrangler d1 migrations apply --local` into a temporary
// directory, then Miniflare (the D1 implementation `wrangler dev` uses) opens
// that database through the binding API. Nothing remote is touched.
//
// Usage: npm ci && node scripts/record-d1-semantics.mjs

import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { Miniflare } from "miniflare";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
const version = (pkg) => require(`${pkg}/package.json`).version;

// Seed rows, applied through the binding before the scenarios run.
const SETUP = [
  ["INSERT INTO users (id, email, username) VALUES (?, ?, ?)", ["user_001", "a@example.com", "a"]],
  ["INSERT INTO tags (id, user_id, name, created_at) VALUES (?, ?, ?, ?)", ["tag_1", "user_001", "python", "2026-01-01"]],
  ["INSERT INTO tags (id, user_id, name, created_at) VALUES (?, ?, ?, ?)", ["tag_2", "user_001", "rust", "2026-01-01"]],
  ["INSERT INTO articles (id, user_id, original_url, title) VALUES (?, ?, ?, ?)", ["art_1", "user_001", "https://example.com/1", "One"]],
  ["INSERT INTO articles (id, user_id, original_url, title) VALUES (?, ?, ?, ?)", ["art_2", "user_001", "https://example.com/2", "Two"]],
];

// `unconfigured_mock`: an empty MockD1 with no execute callback should
// reproduce this outcome (nothing matches, nothing conflicts, no triggers).
// The others need real rows, constraints or triggers, so only the
// SQLite-backed double (tests/conftest.py SqliteD1) is compared on them.
const SCENARIOS = [
  {
    name: "insert_new_row",
    unconfigured_mock: true,
    sql: "INSERT INTO tags (id, user_id, name, created_at) VALUES (?, ?, ?, ?)",
    params: ["tag_3", "user_001", "go", "2026-01-02"],
  },
  {
    name: "insert_or_ignore_new_row",
    unconfigured_mock: true,
    sql: "INSERT OR IGNORE INTO tags (id, user_id, name, created_at) VALUES (?, ?, ?, ?)",
    params: ["tag_4", "user_001", "zig", "2026-01-02"],
  },
  {
    // src/tags/routes.py create_tag: a concurrent request created the name first.
    name: "insert_or_ignore_unique_conflict",
    unconfigured_mock: false,
    sql: "INSERT OR IGNORE INTO tags (id, user_id, name, created_at) VALUES (?, ?, ?, ?)",
    params: ["tag_5", "user_001", "python", "2026-01-02"],
  },
  {
    // D1 counts the FTS5 sync trigger's writes too, so this is not 1.
    name: "insert_into_table_with_fts_trigger",
    unconfigured_mock: false,
    sql: "INSERT INTO articles (id, user_id, original_url, title) VALUES (?, ?, ?, ?)",
    params: ["art_3", "user_001", "https://example.com/3", "Three"],
  },
  {
    name: "update_one_matching_row",
    unconfigured_mock: false,
    sql: "UPDATE tags SET name = ? WHERE id = ? AND user_id = ?",
    params: ["python3", "tag_1", "user_001"],
  },
  {
    // src/articles/routes.py batch_update_articles for an id that does not exist.
    name: "update_no_matching_row",
    unconfigured_mock: true,
    sql: "UPDATE articles SET is_favorite = ?, updated_at = ? WHERE id = ? AND user_id = ?",
    params: [1, "2026-01-03", "nonexistent", "user_001"],
  },
  {
    name: "update_rows_with_fts_trigger",
    unconfigured_mock: false,
    sql: "UPDATE articles SET is_favorite = ? WHERE user_id = ?",
    params: [1, "user_001"],
  },
  {
    name: "delete_one_matching_row",
    unconfigured_mock: false,
    sql: "DELETE FROM tags WHERE id = ? AND user_id = ?",
    params: ["tag_2", "user_001"],
  },
  {
    name: "delete_no_matching_row",
    unconfigured_mock: true,
    sql: "DELETE FROM tags WHERE id = ?",
    params: ["nonexistent"],
  },
  {
    name: "select_via_run",
    unconfigured_mock: true,
    sql: "SELECT id FROM tags WHERE user_id = ?",
    params: ["user_001"],
  },
];

const wranglerConfig = readFileSync(join(root, "wrangler.jsonc"), "utf8");
const databaseId = /"binding":\s*"DB",\s*"database_name":\s*"[^"]+",\s*"database_id":\s*"([^"]+)"/.exec(
  wranglerConfig,
)[1];

const persist = mkdtempSync(join(tmpdir(), "tasche-d1-"));
let mf;
try {
  execFileSync(
    "npx",
    ["wrangler", "d1", "migrations", "apply", "DB", "--local", "--persist-to", persist],
    { cwd: root, stdio: ["ignore", "ignore", "inherit"], env: { ...process.env, CI: "true" } },
  );
  mf = new Miniflare({
    modules: true,
    script: "export default { fetch() { return new Response(null, { status: 204 }); } };",
    d1Databases: { DB: databaseId },
    d1Persist: join(persist, "v3", "d1"),
  });
  const db = await mf.getD1Database("DB");
  for (const [sql, params] of SETUP) {
    await db.prepare(sql).bind(...params).run();
  }
  const scenarios = [];
  for (const scenario of SCENARIOS) {
    const result = await db.prepare(scenario.sql).bind(...scenario.params).run();
    scenarios.push({ ...scenario, success: result.success, meta_changes: result.meta.changes });
  }
  const fixture = {
    description:
      "meta.changes returned by D1's stmt.bind(...).run(), recorded locally by " +
      "scripts/record-d1-semantics.mjs. Scenarios run in order after setup.",
    recorded_with: {
      wrangler: version("wrangler"),
      miniflare: version("miniflare"),
      workerd: version("workerd"),
      node: process.version,
    },
    setup: SETUP.map(([sql, params]) => ({ sql, params })),
    scenarios,
  };
  const out = join(root, "tests", "fixtures", "d1_run_semantics.json");
  writeFileSync(out, JSON.stringify(fixture, null, 2) + "\n");
  for (const s of scenarios) console.log(`${s.name}: changes=${s.meta_changes}`);
  console.log(`wrote ${out}`);
} finally {
  await mf?.dispose();
  rmSync(persist, { recursive: true, force: true });
}
