# Tasche TODO

## Header Navigation Redesign

~~Replaced individual nav buttons and Help dropdown with a hamburger menu. Search stays as a direct icon button; everything else (Tags, Stats, Settings, theme toggle, keyboard shortcuts, design language) lives in the hamburger dropdown.~~

Remaining:

- **Add active-tab indicator**: no icon currently shows which view you're on (no `aria-current`, no highlight)
- **Mobile bottom nav**: on small screens, move primary navigation (Library, Search, Tags, Settings) to a bottom bar for thumb reachability — top header is the hardest zone to reach one-handed

## Spec Gaps (Future Phases)

Items described in the spec but not yet implemented.

### ~~Search Result Highlighting (Phase 10)~~ — Done

Implemented: `HighlightedText` component in `Search.jsx`, `highlightTerms()` in `utils.js`, `<mark>` CSS with light/dark mode support.

### ~~Media Session API Polish (Phase 10)~~ — Done

Implemented: MediaMetadata with title, domain (artist), album, and thumbnail artwork. Position state updates for lock-screen progress bar. Action handlers: play, pause, seekbackward, seekforward, seekto.

### ~~Offline UI Polish (Phase 12)~~ — Done

Implemented: "Save for offline" and "Download audio" buttons in Reader, offline indicator (checkmark) on article cards, cache stats in Settings, auto-precache toggle, offline bar in Header, background sync, LRU eviction (100 articles max).

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
