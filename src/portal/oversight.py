"""What an organizer reads to check the event is fair: the audit log, the
duplicate submissions, and the exports."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import audit
from .exports import EXPORTS
from .models import AuditLog, Project
from .organizer import organizer_required


@organizer_required
def audit_page(request, event):
    rows = AuditLog.objects.filter(event=event).select_related("actor")
    action = request.GET.get("action", "")
    actor = request.GET.get("actor", "").strip()
    if action:
        rows = rows.filter(action__startswith=action)
    if actor:
        rows = rows.filter(actor__email__icontains=actor)
    actions = sorted({a.split(".")[0] for a in AuditLog.objects.filter(event=event).values_list("action", flat=True).distinct()})
    page = Paginator(rows.order_by("-seq"), 50).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "portal/organizer/audit.html", {
        "event": event, "page": page, "actions": actions, "action": action, "actor": actor, "query": query.urlencode(),
    })


@organizer_required
def duplicates(request, event):
    flagged = (Project.objects.filter(event=event, duplicate_of__isnull=False)
               .select_related("team", "duplicate_of__team").order_by("pk"))
    return render(request, "portal/organizer/duplicates.html", {"event": event, "flagged": flagged})


@organizer_required
@require_POST
def resolve_duplicate(request, event, pk):
    """Either confirm the flag (the copy stays out of the rankings) or clear
    it (it was a different project after all and goes back in, for good:
    duplicate_cleared keeps the detector from flagging it again)."""
    project = get_object_or_404(Project, event=event, pk=pk, duplicate_of__isnull=False)
    if request.POST.get("decision") == "distinct":
        before = {"duplicate_of": project.duplicate_of_id}
        project.duplicate_of, project.duplicate_cleared = None, True
        project.save(update_fields=["duplicate_of", "duplicate_cleared", "updated_at"])
        audit.record("project.not_duplicate", request=request, event=event, obj=project, before=before)
        messages.success(request, f"{project.title} is back in the rankings. Hand it out for review if it needs more.")
    else:
        audit.record("project.duplicate_confirmed", request=request, event=event, obj=project,
                     after={"duplicate_of": project.duplicate_of_id})
        messages.success(request, f"{project.title} stays out of the rankings.")
    return redirect("duplicates", slug=event.slug)


@organizer_required
def exports_page(request, event):
    return render(request, "portal/organizer/exports.html", {"event": event, "kinds": [
        ("registrations", "Everyone registered, with their role and tracks"),
        ("teams", "Teams and their members"),
        ("projects", "Every project, drafts included, with its duplicate flag"),
        ("assignments", "Who reviews what, and how far they got"),
        ("scores", "Every review: each criterion, the weighted score, the comment"),
        ("results", "The latest calibration: raw and calibrated scores, ranks, standard errors"),
        ("judges", "Each judge's calibration: leniency, scale, noise, flag"),
        ("audit", "The audit log with its hash chain"),
    ], "known": set(EXPORTS)})
