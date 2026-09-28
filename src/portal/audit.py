"""Every state change writes one audit row, in the same transaction as the
change (requests are atomic), so there is never a change without its row or
a row without its change."""
from django.core.serializers.json import DjangoJSONEncoder
import json

from .models import AuditLog


def client_ip(request):
    # Behind a proxy, configure it to overwrite REMOTE_ADDR; we don't trust
    # X-Forwarded-For from the client.
    return request.META.get("REMOTE_ADDR") if request is not None else None


def _plain(value):
    return None if value is None else json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def record(action, *, request=None, actor=None, event=None, obj=None, before=None, after=None):
    if actor is None and request is not None and request.user.is_authenticated:
        actor = request.user
    return AuditLog.objects.create(
        actor=actor,
        event=event,
        action=action,
        object_type=type(obj).__name__.lower() if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        before=_plain(before),
        after=_plain(after),
        ip=client_ip(request),
    )


def snapshot(obj, fields):
    return {f: getattr(obj, f) for f in fields}
