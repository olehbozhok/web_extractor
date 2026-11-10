"""Same-site listing links extracted from the raw HTML of every html page.

The main-text extractor discards headline lists as navigation, so an index
page comes back with an arbitrary fragment while its actual content (the
list of links) is lost. This parser recovers that content as a structured
array returned alongside the main text (docs/03). Pages are not classified:
the array is derived from every html body alike.

Pure parser: no I/O, deterministic; broken HTML yields an empty array, not
an error, because the field is best-effort by design.
"""

from typing import Final
from urllib.parse import urljoin, urlsplit

from lxml import etree
from lxml.html import fromstring

from web_extractor.domain.listing_links import LinkItem

LISTING_LINKS_MAX = 50
"""At most this many links are returned: the first ones in document order."""

_SKIPPED_HREF_PREFIXES = ("javascript:", "mailto:", "tel:")
"""Hrefs that point nowhere fetchable; compared lowercase (schemes are
case-insensitive). Fragment hrefs (``#...``) are skipped separately: they
resolve to the current page."""

_MIN_NAME_LENGTH = 3
_MAX_NAME_LENGTH = 200

_UNCONDITIONAL_BOILERPLATE_TAGS = frozenset({"nav", "aside"})
"""Containers whose anchors are site navigation, not page content, in every
position. An index-page menu easily outgrows the cap (a 55-item menu displaced
every headline), so anchors inside these are never listed."""

_SECTION_SCOPED_BOILERPLATE_TAGS = frozenset({"header", "footer"})
"""Boilerplate tags that belong to their nearest sectioning ancestor per the
HTML5 sectioning rules: a ``header``/``footer`` nested inside ``article``/
``aside``/``main``/``nav``/``section`` is that section's headline block or
related-stories footer (the standard index markup is
``<article><header><h2><a>Headline</a></h2></header></article>``), not the
site banner. One counts as site chrome only when no sectioning element sits
above it in the ancestor chain."""

_SECTIONING_ANCESTOR_TAGS = frozenset({"article", "aside", "main", "nav", "section"})
"""Elements that scope a nested ``header``/``footer`` (HTML5 sectioning
content, plus ``main`` which the HTML-ARIA rules treat the same way for
banner/contentinfo)."""

_BOILERPLATE_ROLES = frozenset({"navigation", "banner", "contentinfo"})
"""ARIA roles marking the same boilerplate on tag-less containers."""

_URL_FORBIDDEN_CHARACTERS = frozenset(chr(code) for code in range(0x21)) | {"\x7f"}
"""Whitespace and control characters: any of them inside a URL rejects the
link. ``urljoin`` preserves a trailing space of an href (CPython strips only
leading C0-and-space) and raw newlines taken from numeric references in an
href, and a link printed into the fenced listing section could forge its
markers or break the "link is the text after the last `` — ``" reading rule.
A URL with such characters inside is malformed for HTTP anyway, and the field
is best-effort by design. At the edges of an href the same characters are not
a rejection case: they are trimmed off before resolution, as browsers trim
both edges (see ``_URL_EDGE_TRIM_CHARACTERS``)."""

_URL_EDGE_TRIM_CHARACTERS: Final[str] = "".join(sorted(_URL_FORBIDDEN_CHARACTERS))
"""The forbidden characters as one string: ``str.strip`` takes a string of
characters, not a frozenset. Derived from the same set, so the edge trim
applied to an href and the inside rejection applied to the resolved URL
always cover the same characters."""


def extract_listing_links(html: bytes, final_url: str) -> tuple[LinkItem, ...]:
    """Collect same-site anchor links from ``html``, in document order.

    ``final_url`` resolves relative hrefs and defines the site: only anchors
    whose host (lowercased, scheme ignored) equals its host are kept. An
    anchor qualifies when its href is fetchable (not a fragment or a
    ``javascript:``/``mailto:``/``tel:`` scheme), it does not sit inside
    navigation boilerplate (a ``nav``/``aside`` ancestor, a
    ``header``/``footer`` ancestor with no sectioning element above it, or an
    ancestor with a ``navigation``/``banner``/``contentinfo`` role), its
    whitespace-normalized text is 3..200 characters long and carries at least
    one letter (which filters out dates, counters and icons), and its URL is
    resolved from the href with the edge whitespace and control characters
    trimmed off first (browsers trim both edges of an href; a whitespace or
    control character inside the resolved URL still rejects the link, so a
    whitespace-only href is dropped like a fragment). Duplicates of
    ``(name, link)`` keep their first occurrence; at most ``LISTING_LINKS_MAX``
    links are returned. Unparseable HTML yields ``()``.
    """
    try:
        tree = fromstring(_as_parser_input(html))
    except etree.LxmlError:
        # The recovering parser still rejects garbage (e.g. an empty or
        # binary body); an empty tuple is the documented best-effort value.
        return ()
    base_host = _host_of(final_url)
    if base_host is None:
        return ()
    links: list[LinkItem] = []
    seen: set[tuple[str, str]] = set()
    for anchor in tree.iter("a"):
        link = _same_site_link(anchor, final_url, base_host)
        if link is None:
            continue
        if _in_boilerplate(anchor):
            continue
        name = " ".join(_anchor_text(anchor).split())
        if not _MIN_NAME_LENGTH <= len(name) <= _MAX_NAME_LENGTH:
            continue
        if not any(character.isalpha() for character in name):
            continue
        key = (name, link)
        if key in seen:
            continue
        seen.add(key)
        links.append(LinkItem(name=name, link=link))
        if len(links) == LISTING_LINKS_MAX:
            break
    return tuple(links)


def _same_site_link(anchor: etree._Element, final_url: str, base_host: str) -> str | None:
    """The absolute same-site URL behind ``anchor``'s href, or None.

    None when the anchor yields no fetchable same-site URL: the href is
    missing, empty after the edge trim (a whitespace-only href resolves to
    the current page with no destination of its own), a fragment, a
    ``javascript:``/``mailto:``/``tel:`` scheme, a link to another host, or
    a URL carrying a forbidden character inside. The edge whitespace and
    control characters of the href are trimmed off before resolution, like
    browsers trim both edges of an href (WHATWG basic URL parser); the
    characters inside the resolved URL still reject the link.
    """
    href = anchor.get("href")
    if href is None:
        return None
    # CPython's urljoin strips only leading C0-and-space and preserves a
    # trailing space, so the trim must happen here or the forbidden filter
    # would drop a real link with a stray trailing space.
    href = href.strip(_URL_EDGE_TRIM_CHARACTERS)
    if not href or href.startswith("#"):
        return None
    lowered = href.lower()
    if any(lowered.startswith(prefix) for prefix in _SKIPPED_HREF_PREFIXES):
        return None
    link = urljoin(final_url, href)
    if _host_of(link) != base_host:
        return None
    if any(character in _URL_FORBIDDEN_CHARACTERS for character in link):
        return None
    return link


def _in_boilerplate(anchor: etree._Element) -> bool:
    """Whether an ancestor of ``anchor`` marks site navigation.

    ``nav`` and ``aside`` ancestors and the boilerplate ARIA roles disqualify
    the anchor unconditionally. A ``header``/``footer`` ancestor counts as
    site chrome only when no sectioning element (``article``/``aside``/
    ``main``/``nav``/``section``) sits above it in the chain: per the HTML5
    sectioning rules a header/footer belongs to its nearest sectioning
    ancestor, so the standard index markup
    ``<article><header><h2><a>Headline</a></h2></header></article>`` is
    content, not banner. A section-scoped header/footer keeps the walk going:
    a further ancestor (a real ``nav``) can still disqualify the anchor.
    """
    ancestors = list(anchor.iterancestors())
    for position, ancestor in enumerate(ancestors):
        if ancestor.tag in _SECTION_SCOPED_BOILERPLATE_TAGS and not any(
            higher.tag in _SECTIONING_ANCESTOR_TAGS for higher in ancestors[position + 1 :]
        ):
            return True
        if ancestor.tag in _UNCONDITIONAL_BOILERPLATE_TAGS:
            return True
        role = ancestor.get("role")
        if role is not None and role.lower() in _BOILERPLATE_ROLES:
            return True
    return False


def _host_of(url: str) -> str | None:
    """The lowercase host of ``url``, or None when it does not parse."""
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _as_parser_input(html: bytes) -> str | bytes:
    """UTF-8 text when the body is UTF-8, the raw bytes otherwise.

    Given raw bytes, libxml2 defaults to latin-1 when the document declares
    no encoding, which mangles the modern default (UTF-8); decoding first
    keeps anchor texts intact. When UTF-8 does not fit, the bytes go to the
    parser as-is: libxml2 then reads the declared charset itself.
    """
    try:
        return html.decode("utf-8")
    except UnicodeDecodeError:
        return html


def _anchor_text(anchor: etree._Element) -> str:
    """The concatenated descendant text of an anchor, like ``text_content()``.

    Written over the generic ``_Element`` API because the maintained
    ``lxml-stubs`` package types ``lxml.html.fromstring`` as returning
    ``etree._Element``, which has no ``text_content``. For HTML documents
    every text piece is a ``str``; the isinstance filter only narrows the
    stubs' ``str | bytes`` union.
    """
    return "".join(piece for piece in anchor.itertext() if isinstance(piece, str))
