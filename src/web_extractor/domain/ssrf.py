"""SSRF protection rules over IP addresses.

Pure functions: given an IP address the service is about to connect to,
decide whether it may be contacted. The fetcher is responsible for resolving
DNS once, checking the resolved address here, and connecting to exactly that
address (DNS-rebinding defense); this module never resolves anything.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network

_NAT64_PREFIX = IPv6Network("64:ff9b::/96")
"""RFC 6052 well-known prefix; last 32 bits carry an embedded IPv4 address."""

_SHARED_V4_PREFIX = IPv4Network("100.64.0.0/10")
"""RFC 6598 shared address space (CGNAT); not globally routable, yet not
covered by ``is_private`` on current Python releases."""

_IPV6_LOOPBACK = IPv6Address("::1")
_IPV6_UNSPECIFIED = IPv6Address("::")
"""Addresses excluded from the IPv4-compatible form check: they are IPv6
addresses in their own right (loopback, unspecified), not embedded IPv4."""

_V4_COMPATIBLE_MASK = (1 << 128) - (1 << 32)
"""Mask selecting the first 96 bits; a zero remainder identifies deprecated
IPv4-compatible addresses of the form ``::a.b.c.d`` (e.g. ``::127.0.0.1``)."""


class SsrfRule(StrEnum):
    """Which SSRF rule an address violates."""

    UNSPECIFIED = "unspecified"
    LOOPBACK = "loopback"
    LINK_LOCAL = "link_local"
    MULTICAST = "multicast"
    RESERVED = "reserved"
    PRIVATE = "private"
    V4_MAPPED = "v4_mapped"
    NAT64 = "nat64"
    V4_COMPATIBLE = "v4_compatible"


@dataclass(frozen=True, slots=True)
class SsrfViolation:
    """Why an address may not be contacted; ``detail`` names the exact network."""

    rule: SsrfRule
    detail: str


_IPV4_RULES: tuple[tuple[SsrfRule, Callable[[IPv4Address], bool]], ...] = (
    # Ordered most specific first: private covers several narrower ranges,
    # so it comes last.
    (SsrfRule.UNSPECIFIED, lambda ip: ip.is_unspecified),
    (SsrfRule.LOOPBACK, lambda ip: ip.is_loopback),
    (SsrfRule.LINK_LOCAL, lambda ip: ip.is_link_local),
    (SsrfRule.MULTICAST, lambda ip: ip.is_multicast),
    (SsrfRule.RESERVED, lambda ip: ip.is_reserved),
    (SsrfRule.PRIVATE, lambda ip: ip in _SHARED_V4_PREFIX),
    (SsrfRule.PRIVATE, lambda ip: ip.is_private),
)

_IPV6_RULES: tuple[tuple[SsrfRule, Callable[[IPv6Address], bool]], ...] = (
    (SsrfRule.UNSPECIFIED, lambda ip: ip.is_unspecified),
    (SsrfRule.LOOPBACK, lambda ip: ip.is_loopback),
    (SsrfRule.LINK_LOCAL, lambda ip: ip.is_link_local),
    (SsrfRule.MULTICAST, lambda ip: ip.is_multicast),
    (SsrfRule.RESERVED, lambda ip: ip.is_reserved),
    (SsrfRule.PRIVATE, lambda ip: ip.is_private),
)


def check_address(ip: IPv4Address | IPv6Address) -> SsrfViolation | None:
    """Return a violation when the address must not be contacted, else None.

    Allowed are public unicast addresses only: loopback, private, link-local,
    multicast, reserved and unspecified ranges are rejected. IPv4-mapped
    (``::ffff:a.b.c.d``), NAT64 (``64:ff9b::/96``) and deprecated
    IPv4-compatible (``::a.b.c.d``) addresses are judged by the IPv4 rules
    applied to their embedded address, so ``::ffff:10.0.0.1`` is rejected
    even though it is a syntactically public IPv6.
    """
    if isinstance(ip, IPv4Address):
        return _check_ipv4(ip)
    return _check_ipv6(ip)


def _check_ipv4(ip: IPv4Address) -> SsrfViolation | None:
    for rule, is_disallowed in _IPV4_RULES:
        if is_disallowed(ip):
            return SsrfViolation(rule=rule, detail=str(ip))
    return None


def _check_ipv6(ip: IPv6Address) -> SsrfViolation | None:
    embedded = _embedded_ipv4_violation(ip)
    if embedded is not None:
        return embedded
    for rule, is_disallowed in _IPV6_RULES:
        if is_disallowed(ip):
            return SsrfViolation(rule=rule, detail=str(ip))
    return None


def _embedded_ipv4_violation(ip: IPv6Address) -> SsrfViolation | None:
    """Check the IPv4 address embedded in mapped, NAT64 or compatible forms.

    Returns None both when there is no embedded address and when the embedded
    address is allowed; in the latter case the enclosing IPv6 address still
    goes through the plain IPv6 rule list.
    """
    if ip.ipv4_mapped is not None:
        embedded, wrapper_rule = ip.ipv4_mapped, SsrfRule.V4_MAPPED
    elif ip in _NAT64_PREFIX:
        embedded, wrapper_rule = _trailing_ipv4(ip), SsrfRule.NAT64
    elif (int(ip) & _V4_COMPATIBLE_MASK) == 0 and ip not in (
        _IPV6_UNSPECIFIED,
        _IPV6_LOOPBACK,
    ):
        # ::a.b.c.d (all-zero first 96 bits): deprecated IPv4-compatible
        # form. :: and ::1 are excluded: they are IPv6 addresses in their
        # own right, not compatible forms of 0.0.0.0 / 0.0.0.1.
        embedded, wrapper_rule = _trailing_ipv4(ip), SsrfRule.V4_COMPATIBLE
    else:
        return None
    violation = _check_ipv4(embedded)
    if violation is None:
        return None
    return SsrfViolation(wrapper_rule, violation.detail)


def _trailing_ipv4(ip: IPv6Address) -> IPv4Address:
    """The IPv4 address carried in the last 32 bits of an IPv6 address."""
    return IPv4Address(int(ip) & 0xFFFFFFFF)
