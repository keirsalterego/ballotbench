"""Your own API tokens: issue one for a script, revoke it when you're done.
A token acts with your roles and nothing more. It's shown once; only its
hash is kept."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import audit
from .access import db_now
from .auth import issue_token
from .models import ApiToken


@login_required
def tokens(request):
    if request.method == "POST":
        label = request.POST.get("label", "").strip()[:100] or "unnamed"
        token = issue_token(request.user, label)
        row = ApiToken.objects.filter(user=request.user).latest("pk")
        audit.record("token.issue", request=request, obj=row, after={"label": label})
        request.session["new_token"] = token
        return redirect("tokens")
    return render(request, "portal/tokens.html", {
        "tokens": request.user.api_tokens.order_by("-pk"),
        "new_token": request.session.pop("new_token", None),
    })


@login_required
@require_POST
def revoke(request, pk):
    row = get_object_or_404(ApiToken, user=request.user, pk=pk, revoked_at__isnull=True)
    row.revoked_at = db_now()
    row.save(update_fields=["revoked_at"])
    audit.record("token.revoke", request=request, obj=row, before={"label": row.label})
    messages.success(request, f"Revoked {row.label}. Anything using it now gets 401.")
    return redirect("tokens")
