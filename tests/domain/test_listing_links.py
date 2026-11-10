"""Listing link extraction: document order, filters, same-site rule, cap."""

from web_extractor.domain.listing_links import LISTING_LINKS_MAX, LinkItem, extract_listing_links

_PAGE = "https://news.test/index"


def _page(*anchors: str) -> bytes:
    return ("<html><body>" + "".join(anchors) + "</body></html>").encode()


def _anchor(href: str, text: str) -> str:
    return f'<a href="{href}">{text}</a>'


def test_document_order_and_whitespace_normalization() -> None:
    html = _page(
        _anchor("/first", "  News   about\nrobots\ttoday "),
        '<a href="/second"><span>Sports</span> section</a>',
    )

    links = extract_listing_links(html, _PAGE)

    assert links == [
        LinkItem(name="News about robots today", link="https://news.test/first"),
        LinkItem(name="Sports section", link="https://news.test/second"),
    ]


def test_relative_hrefs_resolve_against_final_url() -> None:
    html = _page(_anchor("section/item", "Deep news"), _anchor("/absolute", "Absolute news"))

    links = extract_listing_links(html, _PAGE)

    assert [link.link for link in links] == [
        "https://news.test/section/item",
        "https://news.test/absolute",
    ]


def test_foreign_host_links_are_dropped() -> None:
    html = _page(
        _anchor("https://other.test/story", "Foreign"),
        _anchor("//another.test/story", "Protocol-relative foreign"),
        _anchor("/local", "Local"),
    )

    links = extract_listing_links(html, _PAGE)

    assert [link.link for link in links] == ["https://news.test/local"]


def test_host_comparison_ignores_the_scheme() -> None:
    html = _page(_anchor("http://news.test/insecure", "Insecure twin"))

    links = extract_listing_links(html, _PAGE)

    assert [link.link for link in links] == ["http://news.test/insecure"]


def test_anchor_without_letters_or_wrong_length_is_dropped() -> None:
    html = _page(
        _anchor("/date", "2026-11-10"),
        _anchor("/counter", "1234"),
        _anchor("/short", "ab"),
        _anchor("/long", "a" * 201),
        _anchor("/boundary", "a" * 200),
        _anchor("/cyrillic", "Слово"),
    )

    links = extract_listing_links(html, _PAGE)

    assert [link.name for link in links] == ["a" * 200, "Слово"]


def test_unfetchable_hrefs_are_dropped() -> None:
    html = _page(
        _anchor("#fragment", "Fragment"),
        _anchor("", "Empty href"),
        _anchor("javascript:void(0)", "Script"),
        _anchor("JAVASCRIPT:void(0)", "Script uppercased"),
        _anchor("mailto:news@news.test", "Mail"),
        _anchor("tel:+38012345678", "Phone"),
        _anchor("/kept", "Kept"),
    )

    links = extract_listing_links(html, _PAGE)

    assert [link.name for link in links] == ["Kept"]


def test_duplicates_keep_the_first_occurrence() -> None:
    html = _page(
        _anchor("/dup", "Same headline"),
        _anchor("/other", "Same headline"),
        _anchor("/dup", "Same headline"),
    )

    links = extract_listing_links(html, _PAGE)

    assert links == [
        LinkItem(name="Same headline", link="https://news.test/dup"),
        LinkItem(name="Same headline", link="https://news.test/other"),
    ]


def test_result_is_capped_in_document_order() -> None:
    html = _page(*(_anchor(f"/page-{number}", f"Headline {number}") for number in range(60)))

    links = extract_listing_links(html, _PAGE)

    assert len(links) == LISTING_LINKS_MAX == 50
    assert links[0].link == "https://news.test/page-0"
    assert links[-1].link == "https://news.test/page-49"


def test_broken_html_yields_no_links() -> None:
    assert extract_listing_links(b"", _PAGE) == []
    assert extract_listing_links(b"\x00\x01\x02\xff", _PAGE) == []


def test_utf8_anchor_text_survives_without_a_declared_charset() -> None:
    """Without a meta charset, libxml2 would read the bytes as latin-1."""
    html = ('<html><body><a href="/news">Новини \U0001f996 дня</a></body></html>').encode()

    links = extract_listing_links(html, _PAGE)

    assert links == [LinkItem(name="Новини \U0001f996 дня", link="https://news.test/news")]


def test_non_utf8_page_still_parses_through_the_declared_charset() -> None:
    """Not-UTF-8 bytes go to the parser as-is: libxml2 reads the meta charset."""
    html = (
        '<html><head><meta charset="windows-1251"></head>'
        '<body><a href="/news">\xcd\xee\xe2\xe8\xed\xe8</a></body></html>'
    ).encode("latin-1")  # the escapes above are the cp1251 bytes of "Новини"

    links = extract_listing_links(html, _PAGE)

    assert links == [LinkItem(name="Новини", link="https://news.test/news")]
