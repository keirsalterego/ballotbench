"""Rate limits, counted in the database so every worker process shares them.

A limit is a key ("ballot-ip:203.0.113.7"), a number of hits and a window.
allow() counts the key's hits inside the window and records one more only if
there is room, so a flood can't grow the table past `limit` rows per key.
Old hits are deleted as the key is used again.

IPv6 addresses are limited per /64: one subscriber usually holds a whole
/64, so counting single addresses would let them rotate through billions.

Limits per network address (made with ip_key) are multiplied by
BALLOTBENCH_ADDRESS_LIMIT_SCALE, default 10. A hackathon puts hundreds of
people behind one venue NAT address, so the numbers written next to each
limit are per person, and the address gets room for a crowd. Limits per
account or per voter aren't scaled: those are the ones that stop one person.

Callers answer 429 when allow() says no: pages render ratelimited.html, the
API raises Throttled. A burst of requests in flight at the same moment can
overshoot a limit by that many; the limits are for floods, not accounting."""
import ipaddress
import os
from datetime import timedelta

from django.shortcuts import render
from rest_framework.exceptions import Throttled

from .access import db_now
from .audit import client_ip
from .models import RateHit


def network(ip, v4_bits=32, v6_bits=64):
    """The network `ip` belongs to, as text: the address itself for IPv4 by
    default, its /64 for IPv6. IPv4-mapped IPv6 counts as IPv4."""
    if not ip:
        return "unknown"
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "unknown"
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    bits = v4_bits if addr.version == 4 else v6_bits
    return str(ipaddress.ip_network(f"{addr}/{bits}", strict=False))


def address_scale():
    try:
        return max(1, int(os.environ.get("BALLOTBENCH_ADDRESS_LIMIT_SCALE", "10")))
    except ValueError:
        return 10


def allow(key, limit, window_seconds):
    """True, and one hit recorded, if `key` has had fewer than `limit` hits
    in the last `window_seconds`."""
    if key.startswith(ADDRESS_PREFIX):
        limit *= address_scale()
    since = db_now() - timedelta(seconds=window_seconds)
    RateHit.objects.filter(key=key, created_at__lt=since).delete()
    if RateHit.objects.filter(key=key).count() >= limit:
        return False
    RateHit.objects.create(key=key)
    return True


ADDRESS_PREFIX = "addr:"


def ip_key(request, name):
    return f"{ADDRESS_PREFIX}{name}:{network(client_ip(request))}"


def check(*rules):
    """Raise Throttled (429) if any (key, limit, window_seconds) is used up."""
    results = [allow(*rule) for rule in rules]
    if not all(results):
        raise Throttled(detail="Too many requests. Wait a while and try again.")


def refused(request):
    """The 429 page."""
    return render(request, "portal/ratelimited.html", status=429)
