"""Duplicate submissions: the same repository and title submitted twice in
one event (the fixture has one: prj_41 repeats prj_07). The later copy gets
duplicate_of set and stays out of the rankings until an organizer resolves
it. Its reviews are kept either way."""
import re
from urllib.parse import urlsplit

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
    any earlier project. Returns a list; changes nothing."""
    seen_repo, seen_title, seen_both = {}, {}, {}
    pairs = []
    for p in Project.objects.filter(event=event).order_by("pk"):
        repo, title = key(p)
        original = seen_both.get((repo, title)) if repo else None
        if original is None:
            for index, value in ((seen_repo, repo), (seen_title, title)):
                prior = index.get(value) if value else None
                if prior is not None and prior.team_id == p.team_id:
                    original = prior
                    break
        if original is not None:
            pairs.append((p, original))
            continue
        if repo:
            seen_repo.setdefault(repo, p)
            seen_both.setdefault((repo, title), p)
        seen_title.setdefault(title, p)
    return pairs
