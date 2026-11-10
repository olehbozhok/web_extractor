"""Same-site listing links extracted from the raw HTML of every html page.

The main-text extractor discards headline lists as navigation, so an index
page comes back with an arbitrary fragment while its actual content (the
list of links) is lost. This parser recovers that content as a structured
array returned alongside the main text (docs/03). Pages are not classified:
the array is derived from every html body alike.

Pure parser: no I/O, deterministic; broken HTML yields an empty array, not
an error, because the field is best-effort by design.
"""

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

_BOILERPLATE_ANCESTOR_TAGS = frozenset({"nav", "header", "footer", "aside"})
"""Containers whose anchors are site navigation, not page content. An
index-page menu easily outgrows the cap (a 55-item menu displaced every
headline), so anchors inside these are never listed."""

_BOILERPLATE_ROLES = frozenset({"navigation", "banner", "contentinfo"})
"""ARIA roles marking the same boilerplate on tag-less containers."""


def extract_listing_links(html: bytes, final_url: str) -> list[LinkItem]:
    """Collect same-site anchor links from ``html``, in document order.

    ``final_url`` resolves relative hrefs and defines the site: only anchors
    whose host (lowercased, scheme ignored) equals its host are kept. An
    anchor qualifies when its href is fetchable (not a fragment or a
    ``javascript:``/``mailto:``/``tel:`` scheme), it does not sit inside
    navigation boilerplate (a ``nav``/``header``/``footer``/``aside`` ancestor
    or an ancestor with a ``navigation``/``banner``/``contentinfo`` role), its
    whitespace-normalized text is 3..200 characters long and carries at least
    one letter (which filters out dates, counters and icons). Duplicates of
    ``(name, link)`` keep their first occurrence; at most ``LISTING_LINKS_MAX``
    links are returned. Unparseable HTML yields ``[]``.
    """
    try:
        tree = fromstring(_as_parser_input(html))
    except etree.LxmlError:
        # The recovering parser still rejects garbage (e.g. an empty or
        # binary body); an empty array is the documented best-effort value.
        return []
    base_host = _host_of(final_url)
    if base_host is None:
        return []
    links: list[LinkItem] = []
    seen: set[tuple[str, str]] = set()
    for anchor in tree.iter("a"):
        href = anchor.get("href")
        if not href or href.startswith("#"):
            continue
        lowered = href.lower()
        if any(lowered.startswith(prefix) for prefix in _SKIPPED_HREF_PREFIXES):
            continue
        link = urljoin(final_url, href)
        if _host_of(link) != base_host:
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
    return links


def _in_boilerplate(anchor: etree._Element) -> bool:
    """Whether an ancestor of ``anchor`` marks site navigation."""
    for ancestor in anchor.iterancestors():
        if ancestor.tag in _BOILERPLATE_ANCESTOR_TAGS:
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
