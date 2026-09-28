"""The database-backed limiter, and the login and sign-up limits built on it.
The ballot and comment limits are tested with those features."""
from datetime import timedelta

import pytest

from portal import ratelimit, views
from portal.access import db_now
from portal.models import RateHit

from .conftest import EMAILS

pytestmark = pytest.mark.django_db


def test_allow_counts_up_to_the_limit_then_refuses():
    assert [ratelimit.allow("t:a", 3, 60) for _ in range(5)] == [True, True, True, False, False]
    assert ratelimit.allow("t:b", 3, 60), "another key has its own count"
    assert RateHit.objects.filter(key="t:a").count() == 3, "refused hits aren't stored"


def test_old_hits_fall_out_of_the_window_and_are_deleted():
    for _ in range(3):
        ratelimit.allow("t:old", 3, 60)
    RateHit.objects.filter(key="t:old").update(created_at=db_now() - timedelta(seconds=61))
    assert ratelimit.allow("t:old", 3, 60)
    assert RateHit.objects.filter(key="t:old").count() == 1


@pytest.mark.parametrize("ip, v4_bits, want", [
    ("203.0.113.7", 32, "203.0.113.7/32"),
    ("203.0.113.7", 24, "203.0.113.0/24"),
    ("2001:db8:1:2:aaaa::1", 32, "2001:db8:1:2::/64"),
    ("2001:db8:1:2:ffff:ffff:ffff:ffff", 32, "2001:db8:1:2::/64"),
    ("::ffff:198.51.100.9", 24, "198.51.100.0/24"),
    ("", 32, "unknown"),
    ("not an ip", 32, "unknown"),
])
def test_network_groups_ipv6_by_64(ip, v4_bits, want):
    assert ratelimit.network(ip, v4_bits=v4_bits) == want


def test_one_ipv6_subscriber_shares_one_limit(rf):
    addresses = ["2001:db8:0:7::1", "2001:db8:0:7::2", "2001:db8:0:7:dead:beef:1:2"]
    keys = {ratelimit.ip_key(rf.get("/", REMOTE_ADDR=a), "x") for a in addresses}
    assert keys == {"x:2001:db8:0:7::/64"}


def test_login_attempts_are_limited_per_address(web):
    client = web()
    limit, _ = views.LOGIN_LIMIT
    for _ in range(limit):
        assert client.post("/login", {"username": EMAILS["participant"], "password": "wrong"}).status_code == 200
    response = client.post("/login", {"username": EMAILS["participant"], "password": "ballotbench-demo"})
    assert response.status_code == 429 and b"Slow down" in response.content
    other = web().post("/login", {"username": EMAILS["participant"], "password": "ballotbench-demo"},
                       REMOTE_ADDR="198.51.100.20")
    assert other.status_code == 302, "a different address isn't held up"


def test_signups_are_limited_per_address(web):
    client = web()
    limit, _ = views.SIGNUP_LIMIT
    for i in range(limit):
        client.post("/signup", {"name": "x", "email": f"sybil{i}@example.org", "password": "long-enough-pw"})
    response = client.post("/signup", {"name": "x", "email": "sybil-last@example.org", "password": "long-enough-pw"})
    assert response.status_code == 429
