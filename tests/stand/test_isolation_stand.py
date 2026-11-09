"""Isolation stand integration tests (M11, docs/spikes/02).

These tests require Docker; they are skipped when the docker CLI does not
answer (the stand is a Docker-only artifact - documented in the module
docstring and docs/04_plan.md M11). They assert the stand's own isolation
properties: the redirector is reachable from the public network, the
private target is not, and the private target's access log stays empty.
"""

from collections.abc import Iterator

import pytest

from tests.stand import stand
from tests.stand.stand import curl_from_pub_net, internal_target_hits

pytestmark = pytest.mark.skipif(not stand.docker_available(), reason="Docker is not available")

_REDIRECTOR = "http://93.184.216.10:8080"
_PRIVATE_TARGET = "http://172.30.0.20:8080"


@pytest.fixture(scope="module", autouse=True)
def _stand() -> Iterator[None]:
    stand.stand_up()
    try:
        yield
    finally:
        stand.stand_down()


def test_redirector_reachable_from_public_network() -> None:
    code, output = curl_from_pub_net(_REDIRECTOR + "/")
    assert code == 0
    assert "redirector-ok" in output


def test_redirector_issues_302_to_requested_target() -> None:
    code, output = curl_from_pub_net(f"--head {_REDIRECTOR}/redirect?to=http://93.184.216.10:8080/")
    assert code == 0
    assert "302" in output.splitlines()[0]
    assert "Location: http://93.184.216.10:8080/" in output


def test_private_target_unreachable_from_public_network() -> None:
    """Network segmentation: the private net is a separate docker network."""
    code, _output = curl_from_pub_net(_PRIVATE_TARGET + "/")
    assert code != 0


def test_internal_target_log_stays_empty_after_public_attempts() -> None:
    """The public-network curls above must never reach the private target."""
    assert internal_target_hits() == []


def test_subrequest_page_served_but_target_not_hit() -> None:
    """The redirector's sub-request page loads; the target inside it is
    unreachable from the public network (M12 verifies the same through the
    browser's egress proxy)."""
    code, _output = curl_from_pub_net(_REDIRECTOR + "/subreq")
    assert code == 0
    assert internal_target_hits() == []
