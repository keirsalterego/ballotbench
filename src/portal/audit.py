"""Every state change writes one audit row, in the same transaction as the
change (requests are atomic), so there is never a change without its row or
a row without its change.

The same goes for webhooks: each audit row of an event queues one delivery
per matching webhook, in that transaction too (an outbox), so a change that
rolls back is never announced and one that commits always is."""
import ipaddress
import json
import os

from django.core.serializers.json import DjangoJSONEncoder

from .models import AuditLog, Webhook, WebhookDelivery


def client_ip(request):
    """The caller's address. Behind N reverse proxies, set
    BALLOTBENCH_TRUSTED_PROXIES=N and the address the outermost of them saw is
    taken from X-Forwarded-For, counting from the right: each proxy appends
    the address it received from, and anything further left was written by
    the client and can't be trusted. With 0 (the default), REMOTE_ADDR."""
    if request is None:
        return None
    hops = int(os.environ.get("BALLOTBENCH_TRUSTED_PROXIES", "0") or 0)
    forwarded = [a.strip() for a in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if a.strip()]
    if hops and len(forwarded) >= hops:
        candidate = forwarded[-hops]
        try:
            ipaddress.ip_address(candidate)
            return candidate
        except ValueError:
            pass
    return request.META.get("REMOTE_ADDR")


def _plain(value):
    return None if value is None else json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def record(action, *, request=None, actor=None, event=None, obj=None, before=None, after=None):
    if actor is None and request is not None and request.user.is_authenticated:
        actor = request.user
    row = AuditLog.objects.create(
        actor=actor,
        event=event,
        action=action,
        object_type=type(obj).__name__.lower() if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        before=_plain(before),
        after=_plain(after),
        ip=client_ip(request),
    )
    if event is not None:
        _enqueue(row, event)
    return row


def _enqueue(row, event):
    # ponytail: one query per audited change to find the event's webhooks;
    # cache them per request if an event ever has thousands of changes a second.
    hooks = [h for h in Webhook.objects.filter(event=event, active=True) if h.wants(row.action)]
    if not hooks:
        return
    payload = _plain({"id": row.pk, "action": row.action, "event": event.slug, "at": row.ts,
                      "actor": row.actor.email if row.actor else None,
                      "object": {"type": row.object_type, "id": row.object_id},
                      "before": row.before, "after": row.after})
    WebhookDelivery.objects.bulk_create(WebhookDelivery(webhook=h, action=row.action, payload=payload) for h in hooks)


def snapshot(obj, fields):
    return {f: getattr(obj, f) for f in fields}
