"""Email HTML cleanup utilities for newsletter ingestion.

Sanitizes newsletter HTML by removing scripts, styles, tracking pixels,
and email-specific boilerplate while preserving the main content.

Uses BeautifulSoup (already a project dependency) for all HTML manipulation.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

# Domains commonly used for email tracking pixels.
_TRACKING_DOMAINS = {
    "open.convertkit.com",
    "tracking.tldrnewsletter.com",
    "email.mg.substack.com",
    "links.m.chronosphere.io",
    "pixel.mailchimp.com",
    "list-manage.com",
    "ct.sendgrid.net",
    "mandrillapp.com",
    "mailgun.org",
    "email-tracking.brevo.com",
    "sendinblue.com",
    "t.emailupdates.org",
    "open.substack.com",
    "email.mailgun.net",
    "trk.klclick.com",
    "trk.klclick1.com",
    "cmail19.com",
    "cmail20.com",
    "createsend.com",
    "emltrk.com",
    "go.pardot.com",
}

# Patterns in image URLs that indicate tracking.
_TRACKING_URL_PATTERNS = re.compile(
    r"(open|track|pixel|beacon|wf|o\.gif|t\.gif|spacer|1x1|blank\.gif"
    r"|transparent|__open|email-open|email_open|/e/o/|/track/open)",
    re.IGNORECASE,
)

# Patterns that identify unsubscribe / footer sections.
_FOOTER_PATTERNS = re.compile(
    r"(unsubscribe|manage.{0,3}preferences|email.{0,3}preferences|opt.{0,3}out"
    r"|update.{0,3}profile|view.{0,20}browser|view.{0,10}online|mailing.{0,3}list"
    r"|no.{0,10}longer.{0,10}wish|sent.{0,5}to|received.{0,5}this|you.{0,5}are.{0,10}receiving"
    r"|manage.{0,3}subscription|notification.{0,3}settings|this.{0,5}email.{0,5}was.{0,5}sent)",
    re.IGNORECASE,
)


def _is_tracking_pixel(img: Tag) -> bool:
    """Return True if an <img> tag is likely a tracking pixel.

    Checks for:
    - Explicit 1x1 dimensions (width/height attributes or inline style)
    - Known tracking domains in the src URL
    - Tracking-related URL path patterns
    - Hidden images (display:none, visibility:hidden)
    """
    src = img.get("src", "")
    width = img.get("width", "")
    height = img.get("height", "")
    style = img.get("style", "")

    # Check for 1x1 pixel dimensions via attributes
    if str(width).strip() in ("0", "1") and str(height).strip() in ("0", "1"):
        return True
    if str(width).strip() in ("0", "1") or str(height).strip() in ("0", "1"):
        # Single dimension being 0 or 1 is suspicious if combined with
        # the other dimension also being small or absent
        other_dim = str(height).strip() if str(width).strip() in ("0", "1") else str(width).strip()
        if not other_dim or other_dim in ("0", "1"):
            return True

    # Check for 1x1 in inline style
    if re.search(r"width\s*:\s*[01]px", style) and re.search(r"height\s*:\s*[01]px", style):
        return True

    # Check for hidden via style
    if "display:none" in style.replace(" ", "").lower():
        return True
    if "visibility:hidden" in style.replace(" ", "").lower():
        return True

    # Check for known tracking domains
    if src:
        try:
            parsed = urlparse(src)
            hostname = (parsed.hostname or "").lower()
            if hostname in _TRACKING_DOMAINS:
                return True
            # Check parent domains (e.g., sub.tracking.example.com)
            for domain in _TRACKING_DOMAINS:
                if hostname.endswith("." + domain):
                    return True
        except Exception:
            pass

        # Check for tracking URL patterns
        if _TRACKING_URL_PATTERNS.search(src):
            return True

    return False


def _is_footer_section(tag: Tag) -> bool:
    """Return True if a tag appears to be an email footer / unsubscribe section.

    Checks the tag's text content for common footer phrases. Only matches
    if the tag is relatively small (under 1000 characters of text) to avoid
    accidentally removing large content blocks that happen to mention
    unsubscribe somewhere.
    """
    text = tag.get_text(strip=True)
    if len(text) > 1000:
        return False
    if _FOOTER_PATTERNS.search(text):
        return True
    return False


def clean_email_html(html: str) -> str:
    """Clean newsletter HTML for reader-friendly display.

    Removes:
    - ``<script>`` and ``<style>`` tags
    - Tracking pixels (1x1 images, known tracking domains)
    - Email footer boilerplate (unsubscribe sections)
    - Hidden elements (display:none, visibility:hidden)

    Preserves:
    - Main article content (paragraphs, headings, lists, links, images)
    - Inline formatting (bold, italic, etc.)

    Parameters
    ----------
    html:
        Raw newsletter HTML string.

    Returns
    -------
    str
        Cleaned HTML suitable for reading.
    """
    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")

    # Step 1: Remove <script> and <style> tags entirely
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    # Step 2: Remove tracking pixels
    for img in soup.find_all("img"):
        if _is_tracking_pixel(img):
            img.decompose()

    # Step 3: Remove hidden elements
    for tag in soup.find_all(True):
        if tag.decomposed:
            continue
        style = tag.get("style", "")
        if isinstance(style, str):
            style_lower = style.replace(" ", "").lower()
            if "display:none" in style_lower or "visibility:hidden" in style_lower:
                tag.decompose()

    # Step 4: Remove footer / unsubscribe sections
    # Walk bottom-up to find the last section(s) that contain unsubscribe text.
    # We target <div>, <table>, <tr>, <td>, <p>, <footer> elements.
    for tag in soup.find_all(["div", "table", "tr", "td", "p", "footer", "section"]):
        if tag.decomposed:
            continue
        if _is_footer_section(tag):
            tag.decompose()

    # Step 5: Extract just the body content if there is a <body> tag
    body = soup.find("body")
    if body:
        return body.decode_contents().strip()

    return str(soup).strip()


def extract_first_url(html: str) -> str | None:
    """Extract the first HTTP(S) URL found in the email HTML.

    Scans ``<a>`` tags for ``href`` attributes with http/https schemes.
    Skips mailto:, unsubscribe, and tracking links.

    Parameters
    ----------
    html:
        HTML string to scan.

    Returns
    -------
    str or None
        The first content URL found, or ``None`` if no suitable URL exists.
    """
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # Skip patterns for non-content links
    _skip_patterns = re.compile(
        r"(unsubscribe|manage.?preferences|opt.?out|mailto:|#|javascript:)",
        re.IGNORECASE,
    )

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        if href.startswith(("http://", "https://")):
            if _skip_patterns.search(href):
                continue
            # Skip known tracking redirect domains
            try:
                parsed = urlparse(href)
                hostname = (parsed.hostname or "").lower()
                if hostname in _TRACKING_DOMAINS:
                    continue
            except Exception:
                pass
            return href

    return None
