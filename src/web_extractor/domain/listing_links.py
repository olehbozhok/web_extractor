"""The link item type for same-site listing links (docs/03, 3.4).

The type lives in ``domain`` because it crosses layer boundaries (service
outcome, MCP structured output); the parser that produces it uses lxml and
therefore lives in ``extractors/listing_links.py``.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LinkItem:
    """One same-site link worth reporting: normalized anchor text, absolute URL."""

    name: str
    link: str
