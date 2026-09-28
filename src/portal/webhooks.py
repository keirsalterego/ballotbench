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
address check for receivers on a private network."""
import hashlib
import hmac
import http.client
import ipaddress
import json
import secrets
import socket
import ssl
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

TIMEOUT = 5             # seconds, for the connection and each read
MAX_ATTEMPTS = 8
BACKOFF = 30            # seconds before the first retry, doubled each time: the 8th try is ~1 hour after the 1st
Status = WebhookDelivery.Status


class Refused(Exception):
    pass


def public(ip):
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
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


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, ip, **kwargs):
        super().__init__(host, port, **kwargs)
        self.ip = ip

    def connect(self):
        self.sock = socket.create_connection((self.ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, ip, **kwargs):
        super().__init__(host, port, context=ssl.create_default_context(), **kwargs)
        self.ip = ip

    def connect(self):
        sock = socket.create_connection((self.ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def post(url, body, headers):
    """POST and return the status code. No redirects: http.client has none."""
    parts, port, ip = resolve(url)
    connection = (_PinnedHTTPS if parts.scheme == "https" else _PinnedHTTP)(parts.hostname, port, ip, timeout=TIMEOUT)
    try:
        connection.request("POST", (parts.path or "/") + (f"?{parts.query}" if parts.query else ""), body, headers)
        return connection.getresponse().status
    finally:
        connection.close()


def signature(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def attempt(delivery, send=post):
    """Try one delivery once and record how it went."""
    body = json.dumps(delivery.payload, sort_keys=True, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "ballotbench-webhooks",
               "X-Ballotbench-Action": delivery.action, "X-Ballotbench-Delivery": str(delivery.pk),
               "X-Ballotbench-Signature": signature(delivery.webhook.secret, body)}
    delivery.attempts += 1
    try:
        code = send(delivery.webhook.url, body, headers)
        error = "" if 200 <= code < 300 else f"answered HTTP {code}"
    except (Refused, OSError, ValueError, http.client.HTTPException) as exc:
        error = f"{type(exc).__name__}: {exc}"
    delivery.last_error = error[:500]
    if not error:
        delivery.status = Status.DELIVERED
    elif delivery.attempts >= MAX_ATTEMPTS:
        delivery.status = Status.FAILED
    else:
        delivery.next_attempt_at = db_now() + timedelta(seconds=BACKOFF * 2 ** (delivery.attempts - 1))
    delivery.save(update_fields=["attempts", "last_error", "status", "next_attempt_at"])
    return not error


def deliver_due(send=post):
    """Send every delivery that is due, one row lock at a time (so two
    senders never send the same one). Returns how many were tried."""
    tried = 0
    while True:
        with transaction.atomic():
            delivery = (WebhookDelivery.objects.select_for_update(skip_locked=True, of=("self",))
                        .select_related("webhook")
                        .filter(status=Status.PENDING, next_attempt_at__lte=Now(), webhook__active=True)
                        .order_by("next_attempt_at", "pk").first())
            if delivery is None:
                return tried
            attempt(delivery, send)
            tried += 1


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
    hook.save(update_fields=["active"])
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
