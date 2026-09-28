"""Webhooks: every change in an event, POSTed to the organizer's endpoint.

audit.record queues a delivery in the change's own transaction; the
deliver_webhooks command sends it. Each POST carries
`X-Ballotbench-Signature: sha256=<hex HMAC-SHA256 of the body>` keyed with
the webhook's secret, and is retried with exponential backoff.

The portal makes these requests, so it must not be steerable at its own
network (SSRF). A URL must resolve only to public addresses, checked when
it's saved and again when it's sent; the connection then goes to the address
that was checked, so a DNS answer that changes in between can't redirect it.
Redirects aren't followed. BALLOTBENCH_WEBHOOKS_ALLOW_PRIVATE=1 lifts the
address check for receivers on a private network.

A receiver can't hold the sender either: each attempt has one deadline for
everything, only the head of the answer is read, and the sends happen with
no transaction or row lock held, several at once."""
import hashlib
import hmac
import http.client
import ipaddress
import json
import re
import secrets
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import urlsplit

from django import forms
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models.functions import Now
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import audit
from .access import db_now
from .models import Webhook, WebhookDelivery
from .organizer import organizer_required

TIMEOUT = 10            # seconds for a whole attempt: connecting, sending and reading the answer's head
MAX_HEAD = 16 * 1024    # bytes of an answer read, at most, looking for its status line; the body never is
BATCH = 10              # deliveries sent at once, each to a different webhook
LEASE = 120             # seconds a claimed delivery is its sender's; if the sender dies, it's due again after
MAX_ATTEMPTS = 8
BACKOFF = 30            # seconds before the first retry, doubled each time: the 8th try is ~1 hour after the 1st
Status = WebhookDelivery.Status


class Refused(Exception):
    pass


# IPv6 prefixes whose last 32 bits are an IPv4 address the packet ends up at:
# compatible (::/96), mapped, translated (SIIT) and NAT64's well-known prefix.
IPV4_INSIDE = [ipaddress.ip_network(n) for n in ("::/96", "::ffff:0:0/96", "::ffff:0:0:0/96", "64:ff9b::/96")]
SITE_LOCAL = ipaddress.ip_network("fec0::/10")     # deprecated, but still a private network where it's used


def public(ip):
    """Whether an address is on the public internet. An IPv6 address that
    carries an IPv4 one is judged by the IPv4 one (ipaddress calls
    ::127.0.0.1 or 64:ff9b::10.0.0.1 global)."""
    if ip.version == 6:
        if ip in SITE_LOCAL:
            return False
        if any(ip in net for net in IPV4_INSIDE):
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip.is_global and not ip.is_multicast


def resolve(url):
    """(url parts, port, the address to connect to). Refused unless it's
    http(s) and every address the name resolves to is public."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Refused("only http and https URLs")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        infos = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    except (ValueError, OSError):
        raise Refused(f"can't resolve {parts.hostname}") from None
    ips = [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]
    if not settings.WEBHOOKS_ALLOW_PRIVATE:
        for ip in ips:
            if not public(ip):
                raise Refused(f"{parts.hostname} is a private address ({ip}); webhooks go to public ones only")
    return parts, port, str(ips[0])


def _left(deadline):
    """Seconds left before the deadline, for the next socket operation."""
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError(f"no answer within {TIMEOUT} seconds")
    return left


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, ip, deadline):
        super().__init__(host, port)
        self.ip, self.deadline = ip, deadline

    def connect(self):
        self.sock = socket.create_connection((self.ip, self.port), _left(self.deadline))


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, ip, deadline):
        super().__init__(host, port, context=ssl.create_default_context())
        self.ip, self.deadline = ip, deadline

    def connect(self):
        sock = socket.create_connection((self.ip, self.port), _left(self.deadline))
        sock.settimeout(_left(self.deadline))       # the handshake, as a whole
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


STATUS_LINE = re.compile(rb"HTTP/\d\.\d (\d{3})[^\r\n]*\r\n")


def _status(sock, deadline):
    """The answer's status code, read from its head and no further: at most
    MAX_HEAD bytes, each read given only what's left of the deadline.
    Interim (1xx) answers are skipped."""
    head = b""
    while True:
        rest = head
        while b"\r\n" in rest:
            if not (match := STATUS_LINE.match(rest)):
                raise http.client.BadStatusLine(rest.split(b"\r\n")[0][:100].decode("latin-1"))
            if int(match[1]) >= 200:
                return int(match[1])
            _, blank, rest = rest.partition(b"\r\n\r\n")
            if not blank:
                break
        if len(head) >= MAX_HEAD:
            raise http.client.HTTPException(f"no status line in the first {MAX_HEAD} bytes")
        sock.settimeout(_left(deadline))
        if not (chunk := sock.recv(MAX_HEAD - len(head))):
            raise http.client.RemoteDisconnected("closed the connection without answering")
        head += chunk


def post(url, body, headers):
    """POST and return the status code. No redirects (http.client has none),
    and one deadline for the whole attempt: a socket timeout alone counts
    each read afresh, so a receiver sending a byte every few seconds could
    hold the sender for as long as it liked."""
    deadline = time.monotonic() + TIMEOUT
    parts, port, ip = resolve(url)
    connection = (_PinnedHTTPS if parts.scheme == "https" else _PinnedHTTP)(parts.hostname, port, ip, deadline)
    try:
        connection.connect()
        connection.sock.settimeout(_left(deadline))
        connection.request("POST", (parts.path or "/") + (f"?{parts.query}" if parts.query else ""), body, headers)
        return _status(connection.sock, deadline)
    finally:
        connection.close()


def signature(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _send(delivery, send):
    """Send one delivery once: "" if the receiver took it, else what went
    wrong. No database here, so it can run in any thread."""
    body = json.dumps(delivery.payload, sort_keys=True, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "ballotbench-webhooks",
               "X-Ballotbench-Action": delivery.action, "X-Ballotbench-Delivery": str(delivery.pk),
               "X-Ballotbench-Signature": signature(delivery.webhook.secret, body)}
    try:
        code = send(delivery.webhook.url, body, headers)
        return "" if 200 <= code < 300 else f"answered HTTP {code}"
    except (Refused, OSError, ValueError, http.client.HTTPException) as exc:
        return f"{type(exc).__name__}: {exc}"


def _record(delivery, error):
    """Write down how an attempt went. An update, not a save, so a delivery
    whose webhook was deleted meanwhile is simply gone."""
    delivery.attempts += 1
    delivery.last_error = error[:500]
    if not error:
        delivery.status = Status.DELIVERED
    elif delivery.attempts >= MAX_ATTEMPTS:
        delivery.status = Status.FAILED
    else:
        delivery.next_attempt_at = db_now() + timedelta(seconds=BACKOFF * 2 ** (delivery.attempts - 1))
    WebhookDelivery.objects.filter(pk=delivery.pk).update(
        attempts=delivery.attempts, last_error=delivery.last_error, status=delivery.status,
        next_attempt_at=delivery.next_attempt_at)


def attempt(delivery, send=post):
    """Try one delivery once and record how it went."""
    error = _send(delivery, send)
    _record(delivery, error)
    return not error


def deliver_due(send=post):
    """Send every delivery that is due. Each round claims up to BATCH of
    them, the oldest due one of each of as many webhooks, by moving their
    next_attempt_at a LEASE ahead (under SKIP LOCKED row locks, so two
    senders never claim the same one) and commits. Then they are sent all at
    once with no transaction or lock held, so a slow receiver holds up
    neither the database nor, for longer than one TIMEOUT a round, the
    others; and a sender that dies mid-send leaves them due again once the
    lease runs out. Returns how many were tried."""
    # ponytail: rounds wait for their slowest send; a queue per worker thread
    # if one TIMEOUT a round of delay for everyone ever matters.
    tried = 0
    while True:
        due = WebhookDelivery.objects.filter(status=Status.PENDING, next_attempt_at__lte=Now())
        oldest = (due.filter(webhook__active=True).order_by("webhook_id", "next_attempt_at", "pk")
                  .distinct("webhook_id").values("pk"))
        with transaction.atomic():
            # The due conditions again: they're rechecked on a row another sender claimed meanwhile.
            batch = list(due.select_for_update(skip_locked=True, of=("self",)).select_related("webhook")
                         .filter(pk__in=oldest).order_by("next_attempt_at", "pk")[:BATCH])
            WebhookDelivery.objects.filter(pk__in=[d.pk for d in batch]).update(
                next_attempt_at=Now() + timedelta(seconds=LEASE))
        if not batch:
            return tried
        with ThreadPoolExecutor(len(batch)) as pool:
            errors = list(pool.map(_send, batch, [send] * len(batch)))
        for delivery, error in zip(batch, errors):
            _record(delivery, error)
        tried += len(batch)


# The organizer's page.

class WebhookForm(forms.Form):
    url = forms.URLField(max_length=500, assume_scheme="https", label="URL")
    actions = forms.CharField(required=False, max_length=500, label="Only these changes",
                              help_text="Action prefixes, comma separated, like project, review.submit. "
                                        "Leave empty for every change.")

    def clean_url(self):
        url = self.cleaned_data["url"]
        try:
            resolve(url)
        except Refused as exc:
            raise forms.ValidationError(str(exc)) from None
        return url

    def clean_actions(self):
        prefixes = [a.strip() for a in self.cleaned_data["actions"].split(",") if a.strip()]
        if any(len(a) > 100 for a in prefixes):
            raise forms.ValidationError("each prefix at most 100 characters")
        return prefixes


@organizer_required
def webhooks_page(request, event):
    form = WebhookForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        secret = secrets.token_hex(32)
        hook = Webhook.objects.create(event=event, url=form.cleaned_data["url"], secret=secret,
                                      actions=form.cleaned_data["actions"], created_by=request.user)
        audit.record("webhook.create", request=request, event=event, obj=hook,
                     after={"url": hook.url, "actions": hook.actions})
        request.session["new_webhook_secret"] = secret
        return redirect("webhooks", slug=event.slug)
    return render(request, "portal/organizer/webhooks.html", {
        "event": event, "form": form, "hooks": event.webhooks.order_by("pk"),
        "deliveries": (WebhookDelivery.objects.filter(webhook__event=event).select_related("webhook")
                       .order_by("-pk")[:25]),
        "new_secret": request.session.pop("new_webhook_secret", None),
    }, status=422 if request.method == "POST" else 200)


@organizer_required
@require_POST
def toggle_webhook(request, event, pk):
    hook = get_object_or_404(event.webhooks, pk=pk)
    hook.active = not hook.active
    if hook.active:
        hook.created_by = request.user      # it sends as whoever resumed it (see audit._enqueue)
    hook.save(update_fields=["active", "created_by"])
    audit.record("webhook.resume" if hook.active else "webhook.pause", request=request, event=event, obj=hook,
                 after={"url": hook.url})
    messages.success(request, f"{'Resumed' if hook.active else 'Paused'} {hook.url}.")
    return redirect("webhooks", slug=event.slug)


@organizer_required
@require_POST
def delete_webhook(request, event, pk):
    hook = get_object_or_404(event.webhooks, pk=pk)
    audit.record("webhook.delete", request=request, event=event, obj=hook, before={"url": hook.url})
    hook.delete()
    messages.success(request, f"Deleted {hook.url} and its deliveries.")
    return redirect("webhooks", slug=event.slug)
