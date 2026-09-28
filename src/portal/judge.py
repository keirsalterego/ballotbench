"""Judges: their queue, the scoring form, recusal. Every lookup starts from
access.judge_assignments(request.user), so an assignment that isn't yours is
a 404 whatever id you type, on the pages and in the API alike."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied as PageDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import audit
from .access import Conflict, db_now, guarded, judge_assignments, judged_events
from .models import JudgeAssignment, Review, Score
from .scoring import weighted_score

MINUTES_PER_REVIEW = 10


def judging_closed_reason(event):
    now = db_now()
    if event.judging_open and now < event.judging_open:
        return f"Judging for {event.name} opens at {event.judging_open:%Y-%m-%d %H:%M} UTC."
    if event.judging_close and now >= event.judging_close:
        return f"Judging for {event.name} closed at {event.judging_close:%Y-%m-%d %H:%M} UTC."
    return None


def save_review(request, assignment, values, comment, submit):
    """Write a judge's review of one of their own assignments. `values` maps
    criterion key to an integer. Returns the review. Raises Conflict when
    judging is closed or the assignment was recused, Invalid-style
    ValueError for a bad score."""
    event = assignment.event
    if reason := judging_closed_reason(event):
        raise Conflict(reason)
    if assignment.status == JudgeAssignment.Status.RECUSED:
        raise Conflict("you recused yourself from this project")
    criteria = list(event.criteria.all())
    unknown = set(values) - {c.key for c in criteria}
    if unknown:
        raise ValueError(f"unknown criteria: {', '.join(sorted(unknown))}")
    if submit and any(c.key not in values for c in criteria):
        raise ValueError("score every criterion before submitting")
    for c in criteria:
        if c.key in values and not (c.min_value <= values[c.key] <= c.max_value):
            raise ValueError(f"{c.name} must be between {c.min_value} and {c.max_value}")
    review, _ = Review.objects.get_or_create(assignment=assignment)
    before = {"scores": {s.criterion.key: s.value for s in review.scores.select_related("criterion")},
              "submitted_at": review.submitted_at}
    with guarded():
        for c in criteria:
            if c.key in values:
                Score.objects.update_or_create(review=review, criterion=c, defaults={"value": values[c.key]})
        review.comment = comment
        if submit:
            review.submitted_at = db_now()
            assignment.status = JudgeAssignment.Status.DONE
            assignment.save(update_fields=["status"])
        review.save()
    audit.record("review.submit" if submit else "review.save", request=request, event=event, obj=review,
                 before=before, after={"scores": values, "submitted_at": review.submitted_at,
                                       "project": assignment.project_id})
    return review


@login_required
def queue(request):
    events = judged_events(request.user).order_by("-submissions_close")
    rows = []
    for event in events:
        mine = judge_assignments(request.user).filter(event=event)
        counts = mine.aggregate(total=Count("pk"), done=Count("pk", filter=Q(status="done")),
                                recused=Count("pk", filter=Q(status="recused")))
        pending = counts["total"] - counts["done"] - counts["recused"]
        rows.append({"event": event, **counts, "pending": pending, "minutes": pending * MINUTES_PER_REVIEW,
                     "assignments": mine.select_related("project", "project__track", "review").order_by("status", "pk"),
                     "closed": judging_closed_reason(event)})
    return render(request, "portal/judge/queue.html", {"rows": rows})


class ReviewForm(forms.Form):
    comment = forms.CharField(widget=forms.Textarea(attrs={"rows": 5}), required=False, max_length=5000)

    def __init__(self, *args, criteria, **kwargs):
        super().__init__(*args, **kwargs)
        self.criteria = criteria
        for c in criteria:
            self.fields[f"c_{c.key}"] = forms.TypedChoiceField(
                label=f"{c.name} (weight {c.weight.normalize()})", coerce=int, required=False,
                choices=[("", "-")] + [(v, v) for v in range(c.min_value, c.max_value + 1)],
                widget=forms.RadioSelect)

    def values(self):
        return {c.key: self.cleaned_data[f"c_{c.key}"] for c in self.criteria
                if self.cleaned_data.get(f"c_{c.key}") not in (None, "")}


@login_required
def assignment_page(request, pk):
    assignment = get_object_or_404(judge_assignments(request.user).select_related("event", "project__team", "project__track"),
                                   pk=pk)
    event = assignment.event
    criteria = list(event.criteria.all())
    review = Review.objects.filter(assignment=assignment).prefetch_related("scores__criterion").first()
    initial = {"comment": review.comment} if review else {}
    if review:
        initial.update({f"c_{s.criterion.key}": s.value for s in review.scores.all()})
    form = ReviewForm(request.POST or None, initial=initial, criteria=criteria)
    status = 200
    if request.method == "POST" and form.is_valid():
        submit = "submit" in request.POST
        try:
            save_review(request, assignment, form.values(), form.cleaned_data["comment"], submit)
            messages.success(request, "Review submitted." if submit else "Draft saved.")
            return redirect("judge-queue")
        except Conflict as exc:
            form.add_error(None, str(exc.detail))
            status = 409
        except ValueError as exc:
            form.add_error(None, str(exc))
            status = 422
    return render(request, "portal/judge/assignment.html", {
        "assignment": assignment, "project": assignment.project, "event": event, "form": form,
        "review": review, "total": weighted_score(review, criteria) if review else None,
        "closed": judging_closed_reason(event),
    }, status=status)


@login_required
@require_POST
def recuse(request, pk):
    """A judge who spots a conflict steps aside. The assignment is kept,
    marked recused, and the organizer sees a top-up is needed."""
    assignment = get_object_or_404(judge_assignments(request.user), pk=pk)
    if assignment.status == JudgeAssignment.Status.DONE:
        raise PageDenied
    assignment.status = JudgeAssignment.Status.RECUSED
    assignment.save(update_fields=["status"])
    audit.record("assignment.recuse", request=request, event=assignment.event, obj=assignment,
                 after={"reason": request.POST.get("reason", "")[:500], "project": assignment.project_id})
    messages.success(request, "You've stepped aside from that project. The organizer will reassign it.")
    return redirect("judge-queue")


# The judging API.

@extend_schema(responses=inline_serializer("JudgeAssignment", {
    "id": serializers.IntegerField(), "event": serializers.CharField(), "project": serializers.IntegerField(),
    "project_title": serializers.CharField(), "status": serializers.CharField()}, many=True))
@api_view(["GET"])
def api_assignments(request):
    """Your own assignments, nobody else's. 403 if you judge no event."""
    if not judged_events(request.user).exists():
        raise PermissionDenied("judges only")
    rows = judge_assignments(request.user).select_related("event", "project").order_by("pk")
    return Response([{"id": a.pk, "event": a.event.slug, "project": a.project_id, "project_title": a.project.title,
                      "status": a.status} for a in rows])


class ReviewIn(serializers.Serializer):
    scores = serializers.DictField(child=serializers.IntegerField(), help_text="criterion key to score")
    comment = serializers.CharField(required=False, allow_blank=True, max_length=5000)
    submit = serializers.BooleanField(required=False, default=False)


@extend_schema(request=ReviewIn, responses={200: inline_serializer("ReviewOut", {
    "assignment": serializers.IntegerField(), "submitted_at": serializers.DateTimeField(allow_null=True),
    "weighted_total": serializers.FloatField(allow_null=True)})},
    description="Score one of your own assignments. 404 if it isn't yours, 409 outside the judging window, "
                "422 for a score out of range.")
@api_view(["POST"])
def api_review(request, pk):
    assignment = get_object_or_404(judge_assignments(request.user).select_related("event"), pk=pk)
    body = ReviewIn(data=request.data)
    body.is_valid(raise_exception=True)
    try:
        review = save_review(request, assignment, body.validated_data["scores"],
                             body.validated_data.get("comment", ""), body.validated_data.get("submit", False))
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=422)
    return Response({"assignment": assignment.pk, "submitted_at": review.submitted_at,
                     "weighted_total": weighted_score(review)})
