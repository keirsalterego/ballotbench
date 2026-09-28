"""Comments on submitted projects. Anyone signed in may comment; the event's
organizers may hide a comment, which then disappears for everyone but them.
Hidden comments are kept, not deleted, so hiding can be undone and the audit
row about it still points at something. Bodies are plain text, escaped
wherever they are shown."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from drf_spectacular.utils import extend_schema
from rest_framework import exceptions, permissions, serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from . import audit, ratelimit
from .access import Conflict, db_now, is_organizer, visible_projects
from .models import Comment, Project

COMMENT_LIMIT = (10, 600)      # per account per 10 minutes


def visible_comments(user, project):
    rows = project.comments.select_related("author")
    if not is_organizer(user, project.event):
        rows = rows.filter(hidden_at__isnull=True)
    return rows


class CommentSerializer(serializers.ModelSerializer):
    body = serializers.CharField(max_length=2000, help_text="plain text")
    author = serializers.SerializerMethodField()
    hidden = serializers.SerializerMethodField()

    class Meta:
        model = Comment
        fields = ["id", "author", "body", "created_at", "hidden"]
        read_only_fields = ["created_at"]

    def get_author(self, comment: Comment) -> str:
        return comment.author.name or "Someone"

    def get_hidden(self, comment: Comment) -> bool:
        return comment.hidden_at is not None


def add_comment(request, project, data):
    """Validate and save a comment by request.user. Raises an APIException
    (409 on a draft, 400 for a bad body, 429 when rate limited)."""
    ratelimit.check((f"comment:{request.user.pk}", *COMMENT_LIMIT))
    if project.status != Project.Status.SUBMITTED:
        raise Conflict("comments open once the project is submitted")
    body = CommentSerializer(data=data)
    body.is_valid(raise_exception=True)
    comment = Comment.objects.create(project=project, author=request.user, body=body.validated_data["body"])
    audit.record("comment.create", request=request, event=project.event, obj=comment,
                 after={"project": project.pk, "length": len(comment.body)})
    return comment


@login_required
@require_POST
def post_comment(request, pk):
    project = get_object_or_404(visible_projects(request.user), pk=pk)
    try:
        comment = add_comment(request, project, {"body": request.POST.get("body", "")})
    except exceptions.Throttled:
        return ratelimit.refused(request)
    except exceptions.ValidationError:
        messages.error(request, "A comment needs some text, up to 2000 characters.")
        return redirect(reverse("project", args=[pk]) + "#comments")
    except exceptions.APIException as exc:
        messages.error(request, str(exc.detail).capitalize() + ".")
        return redirect(reverse("project", args=[pk]) + "#comments")
    return redirect(reverse("project", args=[pk]) + f"#comment-{comment.pk}")


@login_required
@require_POST
def moderate(request, pk):
    """Hide a comment, or show it again. Organizers of its event only."""
    comment = get_object_or_404(Comment.objects.select_related("project__event"), pk=pk)
    event = comment.project.event
    if not is_organizer(request.user, event):
        raise PermissionDenied
    hide = request.POST.get("action") != "unhide"
    before = {"hidden_at": comment.hidden_at}
    comment.hidden_at, comment.hidden_by = (db_now(), request.user) if hide else (None, None)
    comment.save(update_fields=["hidden_at", "hidden_by"])
    audit.record("comment.hide" if hide else "comment.unhide", request=request, event=event, obj=comment,
                 before=before, after={"project": comment.project_id, "hidden_at": comment.hidden_at})
    messages.success(request, "Comment hidden from everyone but the organizers." if hide else "Comment shown again.")
    return redirect(reverse("project", args=[comment.project_id]) + f"#comment-{comment.pk}")


@extend_schema(methods=["GET"], responses=CommentSerializer(many=True),
               description="A project's comments, oldest first. Hidden ones are left out, except for the "
                           "event's organizers.")
@extend_schema(methods=["POST"], request=CommentSerializer, responses={201: CommentSerializer},
               description="Comment on a submitted project. 401 without credentials, 409 on a draft, "
                           "400 for an empty or over-long body, 429 when rate limited.")
@api_view(["GET", "POST"])
@permission_classes([permissions.AllowAny])
def api_comments(request, pk):
    project = get_object_or_404(visible_projects(request.user).select_related("event"), pk=pk)
    if request.method == "GET":
        return Response(CommentSerializer(visible_comments(request.user, project), many=True).data)
    if not request.user.is_authenticated:
        raise exceptions.NotAuthenticated()
    try:
        comment = add_comment(request, project, request.data)
    except exceptions.APIException as exc:
        # Returned, not raised, so the rate-limit hit isn't rolled back
        # with the request (see voting.api_ballot).
        return Response(exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}, status=exc.status_code)
    return Response(CommentSerializer(comment).data, status=201)
