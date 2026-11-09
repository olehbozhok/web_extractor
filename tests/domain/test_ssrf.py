"""Table-driven tests for SSRF address rules (01_requirements.md section 7)."""

from ipaddress import IPv4Address, IPv6Address, ip_address

import pytest

from web_extractor.domain.ssrf import SsrfRule, check_address

CASES_ALLOWED = [
    "8.8.8.8",
    "1.1.1.1",
    "93.184.216.34",
    "2606:4700::1111",
    "2001:4860:4860::8888",
    # IPv4-mapped form of a public IPv4 address is contactable.
    "::ffff:8.8.8.8",
]

CASES_DISALLOWED = [
    # (address, expected rule)
    ("0.0.0.0", SsrfRule.UNSPECIFIED),
    ("::", SsrfRule.UNSPECIFIED),
    ("127.0.0.1", SsrfRule.LOOPBACK),
    ("127.255.255.254", SsrfRule.LOOPBACK),
    ("10.0.0.1", SsrfRule.PRIVATE),
    ("172.16.0.1", SsrfRule.PRIVATE),
    ("192.168.1.1", SsrfRule.PRIVATE),
    ("100.64.0.1", SsrfRule.PRIVATE),
    ("169.254.169.254", SsrfRule.LINK_LOCAL),
    ("224.0.0.1", SsrfRule.MULTICAST),
    ("240.0.0.1", SsrfRule.RESERVED),
    ("255.255.255.255", SsrfRule.RESERVED),
    ("192.0.2.1", SsrfRule.PRIVATE),  # TEST-NET-1 documentation range
    ("::1", SsrfRule.LOOPBACK),
    ("fe80::1", SsrfRule.LINK_LOCAL),
    ("fc00::1", SsrfRule.PRIVATE),
    ("fd12:3456:789a::1", SsrfRule.PRIVATE),
    ("ff02::1", SsrfRule.MULTICAST),
    ("::ffff:127.0.0.1", SsrfRule.V4_MAPPED),
    ("::ffff:10.0.0.1", SsrfRule.V4_MAPPED),
    ("::ffff:169.254.1.1", SsrfRule.V4_MAPPED),
    ("64:ff9b::7f00:1", SsrfRule.NAT64),  # NAT64 of 127.0.0.1
    ("64:ff9b::a00:1", SsrfRule.NAT64),  # NAT64 of 10.0.0.1
    ("::127.0.0.1", SsrfRule.V4_COMPATIBLE),
    ("::10.0.0.1", SsrfRule.V4_COMPATIBLE),
]


@pytest.mark.parametrize("raw", CASES_ALLOWED)
def test_public_addresses_are_allowed(raw: str) -> None:
    ip = ip_address(raw)
    assert check_address(ip) is None


@pytest.mark.parametrize(("raw", "rule"), CASES_DISALLOWED)
def test_restricted_addresses_rejected_with_rule(raw: str, rule: SsrfRule) -> None:
    ip: IPv4Address | IPv6Address = ip_address(raw)
    violation = check_address(ip)
    assert violation is not None
    assert violation.rule == rule
    assert violation.detail != ""


def test_mapped_public_address_allowed_but_mapped_private_rejected() -> None:
    assert check_address(IPv6Address("::ffff:8.8.8.8")) is None
    violation = check_address(IPv6Address("::ffff:127.0.0.1"))
    assert violation is not None
    assert "127.0.0.1" in violation.detail
