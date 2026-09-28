"""The organizer's side of judging: hand out reviews with the planner,
adjust by hand, and watch progress per judge and per project."""
from collections import defaultdict

from django.contrib import messages
from django.db import IntegrityError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from rest_framework.exceptions import APIException

from . import audit
from .access import guarded
from .assignment import J, P, plan
from .models import JudgeAssignment, Membership, Project, Review, TeamMember
from .organizer import organizer_required


def rankable_projects(event):
    """Submitted projects that aren't flagged as duplicates: the ones judges
    review and rankings include."""
    return Project.objects.filter(event=event, status=Project.Status.SUBMITTED, duplicate_of__isnull=True)


def planner_input(event):
    projects = [P(p.pk, p.team_id, p.track_id) for p in rankable_projects(event)]
    teams = defaultdict(set)
    for m in TeamMember.objects.filter(event=event):
        teams[m.user_id].add(m.team_id)
    judges = [J(m.user_id, frozenset(t.pk for t in m.tracks.all()), frozenset(teams[m.user_id]))
              for m in Membership.objects.filter(event=event, role=Membership.Role.JUDGE).prefetch_related("tracks")]
    existing = [(a.judge_id, a.project_id, a.status != JudgeAssignment.Status.RECUSED)
                for a in JudgeAssignment.objects.filter(event=event)]
    return projects, judges, existing


def make_plan(event, k):
    projects, judges, existing = planner_input(event)
    return plan(projects, judges, existing, k)


@organizer_required
def assign(request, event):
    """GET previews a plan; POST applies it. The plan is recomputed on POST
    from the database as it is then, never taken from the form."""
    try:
        k = max(1, min(10, int(request.POST.get("k") or request.GET.get("k") or event.reviews_per_project)))
    except ValueError:
        k = event.reviews_per_project
    result = make_plan(event, k)
    if request.method == "POST":
        with guarded():
            JudgeAssignment.objects.bulk_create(
                JudgeAssignment(event=event, judge_id=j, project_id=p, source=JudgeAssignment.Source.AUTO)
                for j, p in result.new)
        audit.record("assignment.plan_apply", request=request, event=event, obj=event, after={
            "k": k, "created": len(result.new), "bridges": len(result.bridges),
            "components_after": result.components_after, "short": result.short})
        messages.success(request, f"Handed out {len(result.new)} review{'s' if len(result.new) != 1 else ''}.")
        return redirect("progress", slug=event.slug)
    projects = {p.pk: p for p in rankable_projects(event)}
    judges = {m.user_id: m.user for m in Membership.objects.filter(event=event, role="judge").select_related("user")}
    by_judge = defaultdict(list)
    for j, p in result.new:
        by_judge[judges[j]].append(projects[p])
    return render(request, "portal/organizer/assign.html", {
        "event": event, "k": k, "plan": result, "by_judge": sorted(by_judge.items(), key=lambda kv: kv[0].email),
        "short": [(projects[p], n) for p, n in result.short.items()],
        "bridges": {(j, p) for j, p in result.bridges},
        "manual_judges": sorted(judges.values(), key=lambda u: u.email),
        "manual_projects": sorted(projects.values(), key=lambda p: p.title),
    })


@organizer_required
@require_POST
def assign_manual(request, event):
    project = get_object_or_404(rankable_projects(event), pk=request.POST.get("project") or 0)
    judge = get_object_or_404(Membership, event=event, role="judge", user_id=request.POST.get("judge") or 0).user
    try:
        with guarded():
            a = JudgeAssignment.objects.create(event=event, judge=judge, project=project,
                                               source=JudgeAssignment.Source.MANUAL)
    except APIException as exc:
        messages.error(request, str(exc.detail))
    except IntegrityError:
        messages.error(request, f"{judge.email} already has {project.title}.")
    else:
        audit.record("assignment.create", request=request, event=event, obj=a,
                     after={"judge": judge.email, "project": project.pk})
        messages.success(request, f"{project.title} is now with {judge.email}.")
    return redirect("progress", slug=event.slug)


@organizer_required
@require_POST
def unassign(request, event, pk):
    """Take back a review nobody has started. Started or finished reviews
    stay: removing them would quietly delete a judge's scores."""
    a = get_object_or_404(JudgeAssignment, event=event, pk=pk)
    if Review.objects.filter(assignment=a).exists():
        messages.error(request, "That review has been started, so it stays.")
        return redirect("progress", slug=event.slug)
    audit.record("assignment.delete", request=request, event=event, obj=a,
                 before={"judge": a.judge.email, "project": a.project_id})
    a.delete()
    return redirect("progress", slug=event.slug)


def judge_progress(event):
    rows = (JudgeAssignment.objects.filter(event=event).values("judge_id", "judge__email", "judge__name")
            .annotate(assigned=Count("pk", filter=~Q(status="recused")),
                      done=Count("pk", filter=Q(status="done")),
                      started=Count("pk", filter=Q(status="pending", review__isnull=False)),
                      recused=Count("pk", filter=Q(status="recused")))
            .order_by("judge__email"))
    judges = Membership.objects.filter(event=event, role="judge").values_list("user_id", "user__email", "user__name")
    seen = {r["judge_id"] for r in rows}
    rows = list(rows) + [{"judge_id": u, "judge__email": e, "judge__name": n, "assigned": 0, "done": 0, "started": 0,
                          "recused": 0} for u, e, n in judges if u not in seen]
    for r in rows:
        r["not_started"] = r["assigned"] - r["done"] - r["started"]
        r["pct"] = round(100 * r["done"] / r["assigned"]) if r["assigned"] else 0
    return sorted(rows, key=lambda r: (r["pct"], r["judge__email"]))


def project_progress(event):
    k = event.reviews_per_project
    rows = (rankable_projects(event).select_related("track")
            .annotate(done=Count("assignments", filter=Q(assignments__status="done")),
                      pending=Count("assignments", filter=Q(assignments__status="pending")))
            .order_by("done", "pk"))
    return [{"project": p, "done": p.done, "pending": p.pending, "missing": max(0, k - p.done - p.pending),
             "low": p.done < k} for p in rows]


@organizer_required
def progress(request, event):
    judges = judge_progress(event)
    projects = project_progress(event)
    total = sum(r["assigned"] for r in judges)
    done = sum(r["done"] for r in judges)
    pending_assignments = (JudgeAssignment.objects.filter(event=event, status="pending")
                           .select_related("judge", "project").order_by("project__title"))
    return render(request, "portal/organizer/progress.html", {
        "event": event, "judges": judges, "projects": projects, "total": total, "done": done,
        "live": request.GET.get("live") == "1",
        "pct": round(100 * done / total) if total else 0,
        "low": [r for r in projects if r["low"]],
        "unfilled": sum(r["missing"] for r in projects),
        "pending_assignments": pending_assignments,
        "manual_judges": Membership.objects.filter(event=event, role="judge").select_related("user").order_by("user__email"),
        "manual_projects": rankable_projects(event).order_by("title"),
    })
