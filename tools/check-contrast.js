#!/usr/bin/env node

/**
 * check-contrast.js — WCAG 2.1 contrast ratio checker for dark-mode CSS
 *
 * Usage:
 *   node tools/check-contrast.js docs/design-language.html
 *
 * Checks all [data-theme='dark'] text/background color pairs against
 * WCAG AA (4.5:1 normal, 3:1 large) and AAA (7:1 normal) thresholds.
 *
 * Can also be required as a library:
 *   const { contrastRatio } = require('./tools/check-contrast.js');
 *   contrastRatio('#f5f5f7', '#272628'); // => 13.07...
 */

const fs = require("fs");
const path = require("path");

// ── Color math ──────────────────────────────────────────────────────────────

/**
 * Parse a hex color (#rgb, #rrggbb) to [r, g, b] in 0-255.
 */
function parseHex(hex) {
  hex = hex.replace(/^#/, "");
  if (hex.length === 3) {
    hex = hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2];
  }
  if (hex.length !== 6) return null;
  const n = parseInt(hex, 16);
  if (isNaN(n)) return null;
  return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
}

/**
 * Linearize an sRGB channel value (0-255) to linear light (0-1).
 */
function linearize(channel) {
  const s = channel / 255;
  return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}

/**
 * Relative luminance per WCAG 2.1.
 * @param {number[]} rgb - [r, g, b] each 0-255
 * @returns {number} luminance 0-1
 */
function relativeLuminance(rgb) {
  const [r, g, b] = rgb.map(linearize);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/**
 * WCAG 2.1 contrast ratio between two hex colors.
 * @param {string} hex1 - e.g. '#f5f5f7'
 * @param {string} hex2 - e.g. '#272628'
 * @returns {number} contrast ratio (1-21)
 */
function contrastRatio(hex1, hex2) {
  const rgb1 = parseHex(hex1);
  const rgb2 = parseHex(hex2);
  if (!rgb1 || !rgb2) return NaN;
  const l1 = relativeLuminance(rgb1);
  const l2 = relativeLuminance(rgb2);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

// ── CSS extraction ──────────────────────────────────────────────────────────

/**
 * Extract CSS from all <style> blocks in an HTML string.
 */
function extractStyleBlocks(html) {
  const blocks = [];
  const re = /<style[^>]*>([\s\S]*?)<\/style>/gi;
  let m;
  while ((m = re.exec(html)) !== null) {
    blocks.push(m[1]);
  }
  return blocks.join("\n");
}

/**
 * Parse CSS rule blocks. Returns an array of { selector, body }.
 *
 * Intentionally naive — handles the single-level selectors used in the
 * design-language file, not nested rules or @media.
 */
function parseRuleBlocks(css) {
  const rules = [];
  // Strip comments
  css = css.replace(/\/\*[\s\S]*?\*\//g, "");
  // Strip @keyframes blocks (they contain nested {} which confuse our parser)
  css = css.replace(/@keyframes\s+\S+\s*\{[^}]*\{[^}]*\}[^}]*\}/g, "");

  const re = /([^{}]+)\{([^}]*)\}/g;
  let m;
  while ((m = re.exec(css)) !== null) {
    const selector = m[1].trim();
    const body = m[2].trim();
    if (selector && body) {
      rules.push({ selector, body });
    }
  }
  return rules;
}

/**
 * Extract color and background/background-color from a declaration block.
 */
function extractColors(body) {
  const result = {};
  // Match color: #xxx (but not border-color, background-color, etc.)
  const colorMatch = body.match(/(?:^|;\s*)color\s*:\s*(#[0-9a-fA-F]{3,8})/);
  if (colorMatch) result.color = colorMatch[1].toLowerCase();

  // Match background-color: #xxx or background: #xxx
  const bgMatch = body.match(
    /(?:^|;\s*)background(?:-color)?\s*:\s*(#[0-9a-fA-F]{3,8})/,
  );
  if (bgMatch) result.background = bgMatch[1].toLowerCase();

  return result;
}

// ── Dark-mode pair resolution ───────────────────────────────────────────────

/**
 * Walk up a selector hierarchy to find the nearest dark background.
 *
 * For ".demo-card-title", tries ".demo-card", ".demo", etc.
 * For ".toast-demo--error", tries ".toast-demo", etc.
 * For ".foo .bar", tries ".foo".
 */
function findParentBackground(selector, bgMap) {
  // Direct match
  if (bgMap[selector]) return bgMap[selector];

  // BEM modifier stripping: ".foo--bar" -> ".foo"
  const bemBase = selector.replace(/--[a-zA-Z0-9_-]+$/, "");
  if (bemBase !== selector && bgMap[bemBase]) return bgMap[bemBase];

  // Suffix stripping on hyphenated class names
  const className = selector.replace(/^\./, "");
  const segments = className.split("-");
  for (let i = segments.length - 1; i >= 1; i--) {
    const candidate = "." + segments.slice(0, i).join("-");
    if (bgMap[candidate]) return bgMap[candidate];
  }

  // Space-separated ancestor (e.g. ".foo .bar" -> check ".foo")
  const spaceParts = selector.split(/\s+/);
  if (spaceParts.length > 1) {
    for (let i = spaceParts.length - 2; i >= 0; i--) {
      if (bgMap[spaceParts[i]]) return bgMap[spaceParts[i]];
    }
  }

  return null;
}

/**
 * Find which selector provided the background (for reporting).
 */
function findBgSourceSelector(selector, bgMap) {
  if (bgMap[selector]) return selector;
  const bemBase = selector.replace(/--[a-zA-Z0-9_-]+$/, "");
  if (bemBase !== selector && bgMap[bemBase]) return bemBase;
  const className = selector.replace(/^\./, "");
  const segments = className.split("-");
  for (let i = segments.length - 1; i >= 1; i--) {
    const candidate = "." + segments.slice(0, i).join("-");
    if (bgMap[candidate]) return candidate;
  }
  const spaceParts = selector.split(/\s+/);
  if (spaceParts.length > 1) {
    for (let i = spaceParts.length - 2; i >= 0; i--) {
      if (bgMap[spaceParts[i]]) return spaceParts[i];
    }
  }
  return null;
}

/**
 * Build dark-mode text/background pairs from CSS.
 *
 * Strategy:
 * 1. Collect all [data-theme='dark'] rules with color/background declarations.
 * 2. For each rule that sets a text `color`, find the effective background:
 *    a. Check if the same selector also sets a background.
 *    b. Walk up a simple hierarchy (e.g. .demo-card for .demo-card-title).
 *    c. Fall back to the page dark background.
 */
function buildDarkPairs(css) {
  const rules = parseRuleBlocks(css);

  const DARK_PREFIX = "[data-theme='dark']";

  const darkRules = [];

  for (const rule of rules) {
    const colors = extractColors(rule.body);
    if (Object.keys(colors).length === 0) continue;

    if (rule.selector.startsWith(DARK_PREFIX)) {
      const inner = rule.selector.slice(DARK_PREFIX.length).trim();
      darkRules.push({
        fullSelector: rule.selector,
        innerSelector: inner || ":root",
        ...colors,
      });
    }
  }

  // Build dark background map: innerSelector -> background
  const darkBgMap = {};
  for (const r of darkRules) {
    if (r.background) {
      darkBgMap[r.innerSelector] = r.background;
    }
  }

  // Page-level dark background
  const pageDarkBg = darkBgMap[":root"] || darkBgMap[""] || "#272628";

  // For each dark rule that sets a text color, find the background
  const pairs = [];
  for (const r of darkRules) {
    if (!r.color) continue;

    let bg = null;
    const sel = r.innerSelector;

    // 1. Does this exact selector also have a background in dark mode?
    if (r.background) {
      bg = r.background;
    }

    // 2. Try to find a parent surface background.
    if (!bg) {
      bg = findParentBackground(sel, darkBgMap);
    }

    // 3. Fall back to page dark background
    if (!bg) {
      bg = pageDarkBg;
    }

    const srcSel = findBgSourceSelector(sel, darkBgMap);
    pairs.push({
      selector: r.fullSelector,
      fg: r.color,
      bg: bg,
      bgSource:
        bg === pageDarkBg && !srcSel
          ? "(page bg)"
          : "(from " + (srcSel || "self") + ")",
    });
  }

  return pairs;
}

// ── WCAG evaluation ─────────────────────────────────────────────────────────

function evaluate(pairs) {
  const results = [];
  for (const p of pairs) {
    const ratio = contrastRatio(p.fg, p.bg);
    const aa = ratio >= 4.5;
    const aaLarge = ratio >= 3.0;
    const aaa = ratio >= 7.0;
    results.push({
      selector: p.selector,
      fg: p.fg,
      bg: p.bg,
      bgSource: p.bgSource,
      ratio: ratio,
      aa: aa,
      aaLarge: aaLarge,
      aaa: aaa,
    });
  }
  return results;
}

// ── Output ──────────────────────────────────────────────────────────────────

function formatTable(results) {
  const lines = [];

  lines.push("");
  lines.push("WCAG 2.1 Contrast Check -- Dark Mode");
  lines.push("=".repeat(60));
  lines.push("");

  // Sort: failures first, then by ratio ascending
  const sorted = [...results].sort((a, b) => {
    if (a.aa !== b.aa) return a.aa ? 1 : -1;
    return a.ratio - b.ratio;
  });

  const failures = sorted.filter((r) => !r.aa);
  const warnings = sorted.filter((r) => r.aa && !r.aaa);
  const passes = sorted.filter((r) => r.aaa);

  if (failures.length > 0) {
    lines.push(
      "FAIL AA (< 4.5:1 normal text)  [" +
        failures.length +
        " pair" +
        (failures.length !== 1 ? "s" : "") +
        "]",
    );
    lines.push("-".repeat(60));
    for (const r of failures) {
      const tag = r.aaLarge ? "PASS-lg" : "FAIL";
      lines.push(
        "  " +
          tag.padEnd(8) +
          " " +
          r.ratio.toFixed(2).padStart(6) +
          ":1  " +
          r.fg +
          " on " +
          r.bg +
          "  " +
          r.bgSource,
      );
      lines.push("           " + r.selector);
    }
    lines.push("");
  }

  if (warnings.length > 0) {
    lines.push(
      "PASS AA, FAIL AAA (< 7:1)  [" +
        warnings.length +
        " pair" +
        (warnings.length !== 1 ? "s" : "") +
        "]",
    );
    lines.push("-".repeat(60));
    for (const r of warnings) {
      lines.push(
        "  AA-ok   " +
          r.ratio.toFixed(2).padStart(6) +
          ":1  " +
          r.fg +
          " on " +
          r.bg +
          "  " +
          r.bgSource,
      );
      lines.push("           " + r.selector);
    }
    lines.push("");
  }

  if (passes.length > 0) {
    lines.push(
      "PASS AAA (>= 7:1)  [" +
        passes.length +
        " pair" +
        (passes.length !== 1 ? "s" : "") +
        "]",
    );
    lines.push("-".repeat(60));
    for (const r of passes) {
      lines.push(
        "  AAA     " +
          r.ratio.toFixed(2).padStart(6) +
          ":1  " +
          r.fg +
          " on " +
          r.bg +
          "  " +
          r.bgSource,
      );
      lines.push("           " + r.selector);
    }
    lines.push("");
  }

  // Summary
  lines.push("=".repeat(60));
  const passCount = warnings.length + passes.length;
  lines.push(
    "Total: " +
      results.length +
      " pairs | " +
      "AA-fail: " +
      failures.length +
      " | AA-pass: " +
      passCount +
      " | " +
      "AAA-pass: " +
      passes.length,
  );

  if (failures.length > 0) {
    lines.push("");
    lines.push(
      "!! " +
        failures.length +
        " pair" +
        (failures.length !== 1 ? "s" : "") +
        " fail" +
        (failures.length === 1 ? "s" : "") +
        " WCAG AA for normal text.",
    );
    const largeOk = failures.filter((r) => r.aaLarge).length;
    if (largeOk > 0) {
      lines.push(
        "   (" +
          largeOk +
          " of those pass AA for large text >= 18pt / 14pt bold)",
      );
    }
  } else {
    lines.push("");
    lines.push("All pairs pass WCAG AA for normal text.");
  }

  lines.push("");
  return lines.join("\n");
}

// ── Main ────────────────────────────────────────────────────────────────────

function main() {
  const args = process.argv.slice(2);

  if (args.length === 0 || args.includes("--help") || args.includes("-h")) {
    console.log("Usage: node check-contrast.js <path-to-html-file>");
    console.log("");
    console.log(
      "Checks dark-mode text/background contrast ratios against WCAG 2.1.",
    );
    process.exit(args.includes("--help") || args.includes("-h") ? 0 : 1);
  }

  const filePath = path.resolve(args[0]);
  if (!fs.existsSync(filePath)) {
    console.error("File not found: " + filePath);
    process.exit(1);
  }

  const html = fs.readFileSync(filePath, "utf-8");
  const css = extractStyleBlocks(html);

  if (!css) {
    console.error("No <style> blocks found in the file.");
    process.exit(1);
  }

  const pairs = buildDarkPairs(css);
  if (pairs.length === 0) {
    console.log("No [data-theme='dark'] color pairs found.");
    process.exit(0);
  }

  const results = evaluate(pairs);
  console.log(formatTable(results));

  // Exit with code 1 if any AA failures
  const failures = results.filter((r) => !r.aa);
  process.exit(failures.length > 0 ? 1 : 0);
}

// If run directly, execute main(). If required as a module, export the library.
if (require.main === module) {
  main();
} else {
  module.exports = {
    contrastRatio,
    relativeLuminance,
    parseHex,
    linearize,
    evaluate,
    buildDarkPairs,
    extractStyleBlocks,
  };
}
