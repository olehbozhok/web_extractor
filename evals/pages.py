"""Fixture pages for tool evaluation.

Real network is forbidden in tests and evaluation runs; these deterministic
pages are served by the evaluation fixture fetcher instead. Content is
crafted so each task kind has verifiable answers: deep needles only reachable
through pagination, typographic quotes for the verbatim distinction, a
blocked page, and a set of mixed-status links.
"""

_PAGE_QUOTE = """
<html><head><title>Quote Page</title></head><body><article>
<h1>Statement of Record</h1>
<p>The committee concluded that “no data was falsified — the anomaly came
from calibration drift” in its final session of March 1999.</p>
<p>Further remarks followed.</p>
</article></body></html>
"""

_PAGE_LONG_HEADER = "Long Report\n"
_PAGE_LONG_PARAGRAPH = "This filler paragraph exists to push content past the first chunk. "
_PAGE_LONG_NEEDLE = "The critical finding reads: TANDEM-77 stored 4192 entries before the failure."
_PAGE_LONG = (
    "<html><head><title>Long Report</title></head><body><article>"
    f"<h1>Long Report</h1><p>{_PAGE_LONG_PARAGRAPH * 400}</p>"
    f"<p>{_PAGE_LONG_NEEDLE}</p>"
    f"<p>{_PAGE_LONG_PARAGRAPH * 200}</p>"
    "</article></body></html>"
)

_PAGE_ARTICLE = """
<html><head><title>Reactor Timeline</title>
<meta name="author" content="History Desk">
<meta name="date" content="1986-04-26"></head><body><article>
<h1>Reactor Timeline</h1>
<p>Construction of unit four finished in 1983.</p>
<p>The accident occurred on 26 April 1986 at unit four.</p>
<p>Total installed capacity reached 4000 MW that year.</p>
</article></body></html>
"""

_PAGE_PLAIN = """
<html><head><title>Plain Notice</title></head><body><article>
<h1>Plain Notice</h1>
<p>The reading room opens at 09:00 and closes at 18:00.</p>
</article></body></html>
"""

FIXTURE_PAGES: dict[str, str] = {
    "http://fixtures.test/quote": _PAGE_QUOTE,
    "http://fixtures.test/long": _PAGE_LONG,
    "http://fixtures.test/article": _PAGE_ARTICLE,
    "http://fixtures.test/plain": _PAGE_PLAIN,
}
"""Deterministic pages keyed by their URL; the eval fetcher serves them."""

QUOTE_VERBATIM = "no data was falsified — the anomaly came from calibration drift"
"""The quote as written in the page (typographic quotes around it)."""

LONG_NEEDLE = "TANDEM-77"
"""Token deep inside the long page; unreachable without read_more."""

LONG_TOTAL_CHARS_HINT = 4000
"""Lower bound of the long page text length; used by task expectations."""

LINK_TARGETS: tuple[str, ...] = (
    "http://fixtures.test/article",
    "http://fixtures.test/plain",
    "http://fixtures.test/blocked",
    "http://fixtures.test/gone",
)
"""URLs used by check_urls tasks with known outcomes."""

BLOCKED_URL = "http://fixtures.test/blocked"
GONE_URL = "http://fixtures.test/gone"
