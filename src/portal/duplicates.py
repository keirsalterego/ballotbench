"""Duplicate submissions: the same repository and title submitted twice in
one event (the fixture has one: prj_41 repeats prj_07). The later copy gets
duplicate_of set and stays out of the rankings until an organizer resolves
it. Its reviews are kept either way.

"Later" is by submitted_at, which only the server sets, and only submitted
projects count: a draft is private and can't be anyone's original. A submit
only ever flags the project being submitted, and a project an organizer
cleared (duplicate_cleared) is never flagged again, so copying someone's
public title and repo can't push their project out of judging."""
import re
from urllib.parse import urlsplit

from . import audit
from .models import Project


def normalize_repo(url: str) -> str:
    """https://www.GitHub.com/a/b.git/ and http://github.com/a/b are one repo."""
    if not url:
        return ""
    parts = urlsplit(url.strip().lower())
    host = parts.netloc.removeprefix("www.")
    path = parts.path.rstrip("/").removesuffix(".git").rstrip("/")
    return f"{host}{path}"


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def key(project) -> tuple[str, str]:
    return normalize_repo(project.repo_url), normalize_title(project.title)


def find_duplicates(event):
    """Pairs (later, earlier) of projects in `event` that share a repo or a
    title with an earlier project from the same team, or share both with
    any earlier project. Earlier means submitted first; drafts and projects
    an organizer cleared are never a later copy. Returns a list; changes
    nothing."""
    seen_repo, seen_title, seen_both = {}, {}, {}
    pairs = []
    for p in (Project.objects.filter(event=event, status=Project.Status.SUBMITTED)
              .order_by("submitted_at", "pk")):
        repo, title = key(p)
        original = seen_both.get((repo, title)) if repo else None
        if original is None:
            for index, value in ((seen_repo, repo), (seen_title, title)):
                prior = index.get(value) if value else None
                if prior is not None and prior.team_id == p.team_id:
                    original = prior
                    break
        if original is not None and not p.duplicate_cleared:
            pairs.append((p, original))
            continue
        if repo:
            seen_repo.setdefault(repo, p)
            seen_both.setdefault((repo, title), p)
        seen_title.setdefault(title, p)
    return pairs


def flag_on_submit(request, event, project):
    """Flag `project`, just submitted, if it repeats an earlier submission.
    Nothing else in the event is touched: a submit can't make someone
    else's project the copy."""
    if project.duplicate_of_id is not None:
        return
    original = next((o for dup, o in find_duplicates(event) if dup.pk == project.pk), None)
    if original is not None:
        project.duplicate_of = original
        project.save(update_fields=["duplicate_of", "updated_at"])
        audit.record("project.flag_duplicate", request=request, event=event, obj=project,
                     after={"duplicate_of": original.pk})
