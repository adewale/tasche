#!/usr/bin/env node
/**
 * Design language lint script for Tasche.
 * Zero dependencies — uses only Node.js built-ins.
 *
 * Checks:
 * 1. Hardcoded colors outside variable definition blocks
 * 2. Hardcoded box-shadow outside variable definition blocks
 * 3. Off-grid spacing (padding/margin/gap not on 4px grid)
 * 4. Inline styles in JSX files
 *
 * Run: node scripts/lint-design.mjs
 * Exit code: 0 if clean, 1 if violations found.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, basename } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = fileURLToPath(new URL(".", import.meta.url));
const ROOT = join(__dirname, "..");
const CSS_FILE = join(ROOT, "frontend", "src", "app.css");
const JSX_DIRS = [
  join(ROOT, "frontend", "src", "views"),
  join(ROOT, "frontend", "src", "components"),
  join(ROOT, "frontend", "src"),
];

// ---------------------------------------------------------------------------
// Config: allowed values
// ---------------------------------------------------------------------------

// Spacing values on the 4px grid, plus cosmetic exceptions (2, 3)
// 6px: used in meta separators, toolbar groups, compact spacing
// 10px: used in input/button padding (vertical), toolbar padding, audio bar
// 14px: used in input/button horizontal padding, table cell padding
// 100px: used for audio player bottom padding
const ALLOWED_SPACING_PX = new Set([0, 1, 2, 3, 4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 32, 40, 48, 100]);

// Files/patterns exempt from inline style checks
const INLINE_STYLE_EXEMPTIONS = [
  // Reader preferences set CSS custom properties dynamically
  { file: "Reader.jsx", pattern: /getReaderStyle/ },
  // Stats bar chart widths are data-driven percentages
  { file: "Stats.jsx", pattern: /width:/ },
  // Audio player progress bar width is dynamic
  { file: "AudioPlayer.jsx", pattern: /width:/ },
  // Article card reading progress bar is data-driven
  { file: "ArticleCard.jsx", pattern: /width:/ },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function collectJsxFiles() {
  const files = [];
  for (const dir of JSX_DIRS) {
    let entries;
    try {
      entries = readdirSync(dir);
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (entry.endsWith(".jsx") || entry.endsWith(".js")) {
        const full = join(dir, entry);
        if (statSync(full).isFile()) {
          files.push(full);
        }
      }
    }
  }
  return files;
}

/**
 * Determine if a CSS line is inside a variable-definition context
 * (:root, @media(prefers-color-scheme), [data-reader-theme]).
 */
function isInVariableBlock(selectorStack) {
  for (const sel of selectorStack) {
    if (
      sel.includes(":root") ||
      sel.includes("prefers-color-scheme") ||
      sel.includes("data-reader-theme")
    ) {
      return true;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// Check 1: Hardcoded colors
// ---------------------------------------------------------------------------

const HEX_RE = /#[0-9a-fA-F]{3,8}\b/;
const RGBA_RE = /rgba?\s*\(/;

function checkHardcodedColors(cssLines) {
  const violations = [];
  const selectorStack = [];

  for (let i = 0; i < cssLines.length; i++) {
    const line = cssLines[i];
    const trimmed = line.trim();

    // Track selector nesting
    if (trimmed.includes("{")) {
      const sel = trimmed.replace(/\{.*/, "").trim();
      selectorStack.push(sel);
    }
    if (trimmed.includes("}")) {
      selectorStack.pop();
    }

    // Skip variable definition blocks
    if (isInVariableBlock(selectorStack)) continue;

    // Skip comments
    if (trimmed.startsWith("/*") || trimmed.startsWith("*") || trimmed.startsWith("//")) continue;

    // Skip lines that are just selectors or closing braces
    if (!trimmed.includes(":") || trimmed.endsWith("{")) continue;

    // Check for hardcoded colors
    if (HEX_RE.test(trimmed) || RGBA_RE.test(trimmed)) {
      // Allow lines that already use var() references
      if (trimmed.includes("var(")) continue;
      // Allow CSS custom property definitions
      if (trimmed.startsWith("--")) continue;
      // Allow spinner track border-color (intentionally subtle rgba on dark overlay)
      if (trimmed.includes("border-color") && trimmed.includes("rgba(255")) continue;
      // Allow lines with lint-ignore comment
      if (trimmed.includes("lint-ignore")) continue;

      violations.push({
        line: i + 1,
        text: trimmed,
      });
    }
  }
  return violations;
}

// ---------------------------------------------------------------------------
// Check 2: No hardcoded box-shadow (only var(--shadow-float) allowed)
// ---------------------------------------------------------------------------

function checkHardcodedShadows(cssLines) {
  const violations = [];
  const selectorStack = [];

  for (let i = 0; i < cssLines.length; i++) {
    const line = cssLines[i];
    const trimmed = line.trim();

    if (trimmed.includes("{")) {
      selectorStack.push(trimmed.replace(/\{.*/, "").trim());
    }
    if (trimmed.includes("}")) {
      selectorStack.pop();
    }

    if (isInVariableBlock(selectorStack)) continue;
    if (trimmed.startsWith("/*") || trimmed.startsWith("*")) continue;

    // Only var(--shadow-float) is allowed outside variable definitions
    if (/box-shadow\s*:/.test(trimmed)) {
      if (trimmed.includes("var(--shadow-float)")) continue;
      violations.push({
        line: i + 1,
        text: trimmed,
      });
    }
  }
  return violations;
}

// ---------------------------------------------------------------------------
// Check 3: Off-grid spacing
// ---------------------------------------------------------------------------

const SPACING_PROPS = /^(padding|margin|gap|padding-(top|right|bottom|left)|margin-(top|right|bottom|left))\s*:/;

function extractPxValues(value) {
  const matches = [];
  // Match standalone px values (not inside var() or calc())
  const parts = value.replace(/var\([^)]+\)/g, "").replace(/calc\([^)]+\)/g, "");
  const pxPattern = /(\d+(?:\.\d+)?)px/g;
  let m;
  while ((m = pxPattern.exec(parts)) !== null) {
    matches.push(parseFloat(m[1]));
  }
  return matches;
}

function checkSpacing(cssLines) {
  const violations = [];

  for (let i = 0; i < cssLines.length; i++) {
    const trimmed = cssLines[i].trim();
    if (!SPACING_PROPS.test(trimmed)) continue;
    if (trimmed.startsWith("/*") || trimmed.startsWith("*")) continue;

    const value = trimmed.split(":").slice(1).join(":").trim();
    const pxValues = extractPxValues(value);

    for (const px of pxValues) {
      if (!ALLOWED_SPACING_PX.has(px)) {
        violations.push({
          line: i + 1,
          text: trimmed,
          value: px,
        });
      }
    }
  }
  return violations;
}

// ---------------------------------------------------------------------------
// Check 4: Inline styles in JSX
// ---------------------------------------------------------------------------

function checkInlineStyles(jsxFiles) {
  const violations = [];

  for (const file of jsxFiles) {
    const name = basename(file);
    const content = readFileSync(file, "utf-8");
    const lines = content.split("\n");

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];

      // Check for JSX object styles
      const hasObjectStyle = /style\s*=\s*\{\{/.test(line);
      // Check for HTML string inline styles
      const hasStringStyle = /style\s*=\s*"/.test(line);

      if (!hasObjectStyle && !hasStringStyle) continue;

      // Check exemptions
      let exempt = false;
      for (const ex of INLINE_STYLE_EXEMPTIONS) {
        if (name === ex.file && ex.pattern.test(line)) {
          exempt = true;
          break;
        }
      }
      if (exempt) continue;

      violations.push({
        file: relative(ROOT, file),
        line: i + 1,
        text: line.trim().slice(0, 80),
      });
    }
  }
  return violations;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

function main() {
  console.log("=== Design Language Lint ===\n");

  const css = readFileSync(CSS_FILE, "utf-8");
  const cssLines = css.split("\n");
  const jsxFiles = collectJsxFiles();

  let totalViolations = 0;

  // Check 1: Hardcoded colors
  const colorViolations = checkHardcodedColors(cssLines);
  if (colorViolations.length) {
    console.log(`HARDCODED COLORS (${colorViolations.length}):`);
    for (const v of colorViolations) {
      console.log(`  app.css:${v.line} - ${v.text}`);
    }
    console.log();
    totalViolations += colorViolations.length;
  }

  // Check 2: Hardcoded shadows
  const shadowViolations = checkHardcodedShadows(cssLines);
  if (shadowViolations.length) {
    console.log(`HARDCODED SHADOWS (${shadowViolations.length}):`);
    for (const v of shadowViolations) {
      console.log(`  app.css:${v.line} - ${v.text}`);
    }
    console.log();
    totalViolations += shadowViolations.length;
  }

  // Check 3: Off-grid spacing
  const spacingViolations = checkSpacing(cssLines);
  if (spacingViolations.length) {
    console.log(`OFF-GRID SPACING (${spacingViolations.length}):`);
    for (const v of spacingViolations) {
      console.log(`  app.css:${v.line} - ${v.text} (${v.value}px not on grid)`);
    }
    console.log();
    totalViolations += spacingViolations.length;
  }

  // Check 4: Inline styles
  const inlineViolations = checkInlineStyles(jsxFiles);
  if (inlineViolations.length) {
    console.log(`INLINE STYLES (${inlineViolations.length}):`);
    for (const v of inlineViolations) {
      console.log(`  ${v.file}:${v.line} - ${v.text}`);
    }
    console.log();
    totalViolations += inlineViolations.length;
  }

  // Summary
  if (totalViolations === 0) {
    console.log("All checks passed! 0 violations.");
    process.exit(0);
  } else {
    console.log(`Total: ${totalViolations} violations`);
    process.exit(1);
  }
}

main();
