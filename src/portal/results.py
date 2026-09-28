"""Calibration runs, the organizer's view of them, and published results.

Results stay hidden from everyone but the event's organizers until they are
published, on the pages and in the API. Publishing freezes the results to
one calibration run; a later run changes nothing public until someone
publishes again."""
from django.contrib import messages
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from . import audit, pairwise
from .access import db_now, is_organizer
from .calibration import bootstrap, digest, fit, signal_test
from .models import CalibratedProject, CalibrationRun, Event, JudgeCalibration, Review
from .organizer import organizer_required
from .progress import rankable_projects
from .scoring import weighted_score

METHOD = "offset-scale-noise/v1"


def submitted_reviews(event):
    return (Review.objects.filter(assignment__event=event, submitted_at__isnull=False)
            .select_related("assignment__judge", "assignment__project")
            .prefetch_related("scores__criterion").order_by("pk"))


@transaction.atomic
def run_calibration(event, actor=None, request=None):
    criteria = list(event.criteria.all())
    obs, canon = [], []
    for r in submitted_reviews(event):
        y = weighted_score(r, criteria)
        if y is None:
            continue
        a = r.assignment
        obs.append((a.judge_id, a.project_id, y))
        canon.append([r.pk, a.judge.email, a.project_id, sorted((s.criterion.key, s.value) for s in r.scores.all())])
    result = fit(obs)
    _, _, p = signal_test(obs, shuffles=1000, seed=event.pk) if obs else (0, 0, None)
    run = CalibrationRun.objects.create(
        event=event, created_by=actor, method=METHOD, input_digest=digest(canon),
        params={"nu": 3.0, "ridge": 1.0, "criteria": {c.key: str(c.weight) for c in criteria}},
        connected=result.components <= 1, converged=result.converged or not obs, signal_p=p, n_reviews=len(obs))

    rankable = set(rankable_projects(event).values_list("pk", flat=True))
    duplicates = set(event.projects.filter(duplicate_of__isnull=False).values_list("pk", flat=True))
    rows = []
    for pid, pf in result.projects.items():
        excluded = ("duplicate" if pid in duplicates else "not submitted" if pid not in rankable
                    else "no usable reviews" if pf.usable == 0 else "")
        rows.append(CalibratedProject(run=run, project_id=pid, n_reviews=pf.n, raw_mean=pf.raw_mean,
                                      quality=pf.quality, display=result.display(pid),
                                      se=pf.se * result.spread, excluded=excluded))
    ranked = [r for r in rows if not r.excluded]
    for rank, row in enumerate(sorted(ranked, key=lambda r: (-r.quality, -r.raw_mean, r.project_id)), 1):
        row.rank = rank
    for rank, row in enumerate(sorted(ranked, key=lambda r: (-r.raw_mean, r.project_id)), 1):
        row.raw_rank = rank
    intervals = bootstrap(obs, rankable={r.project_id for r in ranked}) if ranked else {}
    for row in ranked:
        row.rank_low, row.rank_high, _ = intervals[row.project_id]
    CalibratedProject.objects.bulk_create(rows)
    JudgeCalibration.objects.bulk_create(
        JudgeCalibration(run=run, judge_id=j, n_reviews=jf.n, offset=jf.offset, scale=jf.scale, noise=jf.noise,
                         flag=jf.flag) for j, jf in result.judges.items())
    audit.record("calibration.run", request=request, actor=actor, event=event, obj=run,
                 after={"digest": run.input_digest, "reviews": len(obs), "signal_p": p,
                        "flagged": {str(j): jf.flag for j, jf in result.judges.items() if jf.flag != "ok"}})
    return run


def second_opinion(event, ranked):
    """Rank the ranked projects by the rubric read as pairwise picks
    (portal/pairwise.py), from the current submitted reviews."""
    criteria = list(event.criteria.all())
    obs = [(r.assignment.judge_id, r.assignment.project_id, y) for r in submitted_reviews(event)
           if (y := weighted_score(r, criteria)) is not None]
    strengths = pairwise.fit(pairwise.picks(obs), items=ranked)
    order = sorted(ranked, key=lambda p: (-strengths[p], p))
    return {p: i for i, p in enumerate(order, 1)}


def ranking(run):
    return (run.projects.select_related("project__team", "project__track")
            .order_by("rank", "excluded", "-quality", "project_id"))


@organizer_required
def calibration_page(request, event):
    if request.method == "POST":
        run = run_calibration(event, actor=request.user, request=request)
        messages.success(request, f"Calibrated {run.n_reviews} reviews.")
        return redirect("calibration", slug=event.slug)
    run = event.calibration_runs.order_by("-pk").first()
    rows = list(ranking(run)) if run else []
    pairwise_rank = second_opinion(event, [r.project_id for r in rows if r.rank]) if run else {}
    for row in rows:
        row.moved = (row.raw_rank - row.rank) if row.rank and row.raw_rank else None
        row.pairwise_rank = pairwise_rank.get(row.project_id)
    judges = (run.judges.select_related("judge").order_by("flag", "judge__email") if run else [])
    return render(request, "portal/organizer/calibration.html", {
        "event": event, "run": run, "rows": rows, "judges": judges,
        "flagged": [j for j in judges if j.flag != "ok"],
        "k": event.reviews_per_project,
        "runs": event.calibration_runs.order_by("-pk")[:10],
    })


@organizer_required
@require_POST
def publish(request, event):
    """Freeze the latest run as the public result, or take results down."""
    if request.POST.get("action") == "unpublish":
        before = {"run": event.published_run_id}
        event.results_published_at, event.published_run = None, None
        event.save(update_fields=["results_published_at", "published_run"])
        audit.record("results.unpublish", request=request, event=event, obj=event, before=before)
        messages.success(request, "Results are hidden again.")
        return redirect("calibration", slug=event.slug)
    run = event.calibration_runs.order_by("-pk").first() or run_calibration(event, request.user, request)
    event.results_published_at, event.published_run = db_now(), run
    event.save(update_fields=["results_published_at", "published_run"])
    audit.record("results.publish", request=request, event=event, obj=event,
                 after={"run": run.pk, "digest": run.input_digest})
    messages.success(request, "Results are public.")
    return redirect("results", slug=event.slug)


def visible_run(user, event):
    """The run a caller may see: the published one for everyone, the latest
    for the event's organizers. None means hidden."""
    if event.published_run_id:
        return event.published_run
    if is_organizer(user, event):
        return event.calibration_runs.order_by("-pk").first()
    return None


def results_page(request, slug):
    event = get_object_or_404(Event, slug=slug)
    run = visible_run(request.user, event)
    if run is None:
        return render(request, "portal/results_hidden.html", {"event": event}, status=404)
    rows = [r for r in ranking(run) if r.rank]
    return render(request, "portal/results.html", {"event": event, "run": run, "rows": rows,
                                                   "preview": not event.published_run_id})


ResultRow = inline_serializer("ResultRow", {
    "rank": serializers.IntegerField(), "raw_rank": serializers.IntegerField(), "project": serializers.IntegerField(),
    "title": serializers.CharField(), "team": serializers.CharField(), "calibrated": serializers.FloatField(),
    "se": serializers.FloatField(), "raw_mean": serializers.FloatField(), "reviews": serializers.IntegerField(),
    "rank_interval": serializers.ListField(child=serializers.IntegerField())},
    many=True)


@extend_schema(responses=inline_serializer("Results", {
    "event": serializers.CharField(), "published_at": serializers.DateTimeField(allow_null=True),
    "run": serializers.IntegerField(), "input_digest": serializers.CharField(), "method": serializers.CharField(),
    "projects": ResultRow}),
    description="Ranked results. 404 until published, except for the event's organizers, who see the "
                           "latest calibration run.")
@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def api_results(request, slug):
    event = get_object_or_404(Event, slug=slug)
    run = visible_run(request.user, event)
    if run is None:
        raise Http404
    return Response({
        "event": event.slug, "published_at": event.results_published_at, "run": run.pk,
        "input_digest": run.input_digest, "method": run.method,
        "projects": [{"rank": r.rank, "raw_rank": r.raw_rank, "project": r.project_id, "title": r.project.title,
                      "team": r.project.team.name, "calibrated": round(r.display, 4), "se": round(r.se, 4),
                      "raw_mean": round(r.raw_mean, 4), "reviews": r.n_reviews,
                      "rank_interval": [r.rank_low, r.rank_high]}
                     for r in ranking(run) if r.rank],
    })
