"""Explain my rank: after results are published, a team can see how its
score was put together, review by review, with the judges anonymized.

The question every team that didn't win asks is "why?". The usual answer is a
number. This page answers it with the model: each review's score, what that
judge gives a typical project, how far above or below their own habit they
put you, and how much their review weighed in the end, including why a judge
weighed nothing. It shows weighted totals only: no per-criterion scores, no
comments, and no judge names, and review order is shuffled per project so a
label like "judge 2" can't be matched across teams' pages.

The weights are the model's own (see calibration.py): a judge counts in
proportion to s_j² / v_j, and the prior, the pull towards the middle, counts
as 1. They're read from the published run, so the page explains exactly the
ranking the public sees."""
import hashlib
import random
import statistics

from django.http import Http404
from django.shortcuts import get_object_or_404, render

from .access import is_organizer
from .calibration import digest
from .models import CalibratedProject, JudgeCalibration, Project, Review
from .scoring import weighted_score


def explanation(run, project):
    event = run.event
    criteria = list(event.criteria.all())
    row = CalibratedProject.objects.get(run=run, project=project)
    judges = {j.judge_id: j for j in JudgeCalibration.objects.filter(run=run)}
    learned = [j for j in judges.values() if j.flag == "ok"]
    typical_offset = statistics.median(j.offset for j in learned) if learned else 0.0
    typical_scale = statistics.median(j.scale for j in learned) if learned else 0.0

    reviews = list(Review.objects.filter(assignment__project=project, submitted_at__isnull=False)
                   .select_related("assignment").prefetch_related("scores"))
    rng = random.Random(int(hashlib.sha256(f"{run.pk}:{project.pk}".encode()).hexdigest(), 16))
    rng.shuffle(reviews)
    precision = {r.pk: (judges[r.assignment.judge_id].scale ** 2 / judges[r.assignment.judge_id].noise)
                 if r.assignment.judge_id in judges and judges[r.assignment.judge_id].scale > 0 else 0.0
                 for r in reviews}
    total = 1.0 + sum(precision.values())
    lines = []
    for n, r in enumerate(reviews, 1):
        j = judges.get(r.assignment.judge_id)
        y = weighted_score(r, criteria)
        if j is None or y is None:
            continue
        sharpness = j.scale / typical_scale if typical_scale and j.scale else None
        lines.append({
            "label": f"Judge {n}",
            "about": describe(j.flag, j.offset - typical_offset, sharpness),
            "score": y,
            "usual": j.offset,
            "habit": j.offset - typical_offset,
            "versus_usual": y - j.offset,
            "share": precision[r.pk] / total,
            "flag": j.flag,
        })
    return {"row": row, "lines": lines, "prior_share": 1.0 / total,
            "typical_offset": typical_offset, "stale": digest_now(event, criteria) != run.input_digest}


def describe(flag, habit, sharpness):
    """The judge in words, from their calibration."""
    if flag == "constant":
        return "gave every project the same score"
    if flag == "single_review":
        return "wrote only this one review"
    if flag == "discordant":
        return "scored against the consensus of the other judges"
    words = "a generous judge" if habit > 0.02 else "a harsh judge" if habit < -0.02 else "a middle-of-the-road judge"
    if sharpness and sharpness > 1.3:
        words += " who separates projects sharply"
    elif sharpness and sharpness < 0.77:
        words += " who keeps scores close together"
    return words


def digest_now(event, criteria):
    """The digest the published run would have if computed now; if it
    differs, scores changed after publication and the page says so."""
    canon = []
    for r in (Review.objects.filter(assignment__event=event, submitted_at__isnull=False)
              .select_related("assignment__judge").prefetch_related("scores__criterion").order_by("pk")):
        if weighted_score(r, criteria) is None:
            continue
        canon.append([r.pk, r.assignment.judge.email, r.assignment.project_id,
                      sorted((s.criterion.key, s.value) for s in r.scores.all())])
    return digest(canon)


def explain_page(request, slug, pk):
    """Open to the project's team and the event's organizers, and only once
    results are published (organizers may preview). Everyone else: 404."""
    project = get_object_or_404(Project.objects.select_related("event", "team"), event__slug=slug, pk=pk)
    event = project.event
    organizer = is_organizer(request.user, event)
    on_team = request.user.is_authenticated and project.team.members.filter(user=request.user).exists()
    run = event.published_run or (event.calibration_runs.order_by("-pk").first() if organizer else None)
    if run is None or not (on_team or organizer):
        raise Http404
    if not CalibratedProject.objects.filter(run=run, project=project).exists():
        raise Http404
    return render(request, "portal/explain.html", {
        "event": event, "project": project, "run": run, "preview": not event.published_run_id,
        **explanation(run, project),
    })
