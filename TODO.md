# Tasche TODO

## Header Navigation Redesign

~~Replaced individual nav buttons and Help dropdown with a hamburger menu. Search stays as a direct icon button; everything else (Tags, Stats, Settings, theme toggle, keyboard shortcuts, design language) lives in the hamburger dropdown.~~

Remaining:

- **Add active-tab indicator**: no icon currently shows which view you're on (no `aria-current`, no highlight)
- **Mobile bottom nav**: on small screens, move primary navigation (Library, Search, Tags, Settings) to a bottom bar for thumb reachability — top header is the hardest zone to reach one-handed

## Spec Gaps (Future Phases)

Items described in the spec but not yet implemented.

### ~~Search Result Highlighting (Phase 10)~~ — Removed

Removed with search unification: `Search.jsx` deleted, `highlightTerms()` removed from `utils.js`. Search is now inline in the Library view.

### ~~Media Session API Polish (Phase 10)~~ — Done

Implemented: MediaMetadata with title, domain (artist), album, and thumbnail artwork. Position state updates for lock-screen progress bar. Action handlers: play, pause, seekbackward, seekforward, seekto.

### ~~Offline UI Polish (Phase 12)~~ — Done

Implemented: "Save for offline" and "Download audio" buttons in Reader, offline indicator (checkmark) on article cards, cache stats in Settings, auto-precache toggle, offline bar in Header, background sync, LRU eviction (100 articles max).

## FFI / Observability Patterns from planet_cf

Patterns observed in [adewale/planet_cf](https://github.com/adewale/planet_cf) that are worth adopting.

1. **`create_pyproxies=False` in `to_js()` calls** — raises `ConversionError` instead of silently creating leaking PyProxy objects when non-primitive types (custom classes, functions, bytes, datetime) slip into data passed to bindings. All binding data should be primitives/lists/dicts, so this is a cheap safety net.

2. **Type assertions at the FFI boundary** — `SafeD1.first()` asserts result is `dict|None`, `SafeAI.run()` asserts result is `dict`, etc. Violations emit a structured log event (`boundary_type_violation`) instead of crashing. Catches conversion bugs before they propagate to business logic.

3. **Pyodide FFI fakes for testing** — monkeypatch `HAS_PYODIDE=True` in CPython tests and inject fake JsNull/JsUndefined/FakeJsProxy classes. This catches the `JsNull is not None` class of bugs that regular mock-based testing misses. Tasche documents this gotcha in MEMORY.md but has no regression tests for it.

4. **D1 query counting** — increment a counter in `SafeD1.prepare()` and include it in the wide event. Makes N+1 query patterns visible in production logs without profiling.

5. **Queue backpressure visibility** — include `enqueued_at` timestamp in queue message payloads. The consumer computes `time_in_queue_ms` and logs it. Surfaces queue latency without external monitoring.

6. **`dict_converter=Object.fromEntries` always in `to_js()`** — without this, `to_js()` creates a JS `LiteralMap` instead of a plain `Object`. Bindings that use property access (Vectorize, Workers AI, R2 put options) see `undefined` for every field. planet_cf's latest commit (2026-03-28) fixes a production bug caused by this. Note: Pyodide 0.29.0 will default to Object conversion, making `dict_converter` redundant.

## Kindle Integration

Ideas for getting articles onto Kindle devices and into the Kindle reading experience.

### Send to Kindle via Email

Amazon's Send to Kindle service accepts documents via email. Each Kindle device has a unique `@kindle.com` address.

- **Settings page**: field for the user's Send-to-Kindle email address
- **Per-article action**: "Send to Kindle" button in the reader toolbar
- **Batch send**: select multiple articles in the library and send them all at once
- **Format**: send HTML with inline images (Amazon converts to Kindle format server-side)
- **Approved sender**: user must add Tasche's sending address to their Amazon approved list
- **Implementation**: Cloudflare Workers can send email via MailChannels or an SMTP relay binding

### Send to Kindle via the Official API

Amazon provides a Send to Kindle API for third-party apps.

- **OAuth integration**: link Amazon account in settings
- **Direct push**: no email configuration needed, uses Amazon's API endpoint
- **Status tracking**: the API returns delivery status
- **Consideration**: requires Amazon developer account approval

### Kindle-Optimised HTML

Kindle's rendering engine has specific quirks worth optimising for.

- **Simplified CSS**: strip complex layouts, keep only typography and spacing
- **Image handling**: resize images to Kindle screen dimensions (1072x1448 for Paperwhite)
- **Table of contents**: generate a logical TOC for multi-article collections
- **Page breaks**: insert page breaks between articles in batch sends
- **Font embedding**: Kindle supports embedded fonts — could include our serif choice

### Kindle Scribe Integration

The Kindle Scribe supports handwritten annotations that sync to the Kindle app.

- **Highlight sync**: import Kindle highlights back into Tasche's annotation system
- **Two-way sync**: notes made in Tasche appear as annotations on Kindle and vice versa
- **Clippings import**: parse the `My Clippings.txt` file from Kindle devices

### Whispersync for Reading Position

- **Position sync**: match Tasche's scroll position with Kindle's reading progress
- **Furthest read**: track the furthest-read position across both platforms
- **Consideration**: this likely requires Kindle SDK access that may not be publicly available

### Scheduled Digest

- **Daily/weekly digest**: automatically bundle unread articles and send to Kindle
- **Configurable schedule**: choose time of day and frequency
- **Smart selection**: prioritise by estimated reading time, tags, or age
- **Digest format**: table of contents page followed by articles with clear separators
