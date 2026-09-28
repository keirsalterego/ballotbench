"""A gallery other sites can frame, and the script tag that frames it.

The embed renders as if nobody were signed in, whatever cookies the browser
sends: submitted projects only, never drafts, and results only once they're
published. These two views are the only ones any site may frame; every other
page keeps X-Frame-Options: DENY."""
from django.contrib.auth.models import AnonymousUser
from django.shortcuts import get_object_or_404, render
from django.views.decorators.clickjacking import xframe_options_exempt

from .models import Event, Project
from .results import ranking, visible_run

SHOWN = 60
TOP = 10


@xframe_options_exempt
def embed(request, slug):
    event = get_object_or_404(Event, slug=slug)
    projects = (Project.objects.filter(event=event, status=Project.Status.SUBMITTED)
                .select_related("team", "track").order_by("pk"))
    run = visible_run(AnonymousUser(), event)
    response = render(request, "portal/embed.html", {
        "event": event, "projects": projects[:SHOWN], "total": projects.count(),
        "results": [r for r in ranking(run) if r.rank][:TOP] if run else [],
    })
    response["Content-Security-Policy"] = "frame-ancestors *"
    return response


def embed_js(request):
    response = render(request, "portal/embed.js", content_type="text/javascript; charset=utf-8")
    response["Cache-Control"] = "public, max-age=3600"
    return response
