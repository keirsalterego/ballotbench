"""Who reviews what. A pure planner on plain records, so it can be tested
without a database, plus the thin layer that reads and writes the models.

The plan tops every project up to k reviews:

1. Work in rounds. In each round every project still short of k gets one more
   judge, fewest reviews first, so a shortage is spread evenly rather than
   landing on whichever project happened to come last.
2. The judge is the least-loaded eligible one. Eligible means: not on the
   project's team, the project's track is one of theirs (or they have none),
   and they don't already hold that project (a recusal counts: we don't
   hand a project back to a judge who stepped aside from it).
3. On a tie in load, prefer a judge who joins two parts of the judge-project
   graph that aren't yet connected, then the lowest id, so plans are
   deterministic.
4. Afterwards, if the graph is still in pieces, add one bridging review per
   extra piece. Calibration compares judges through the projects they share;
   two groups with no judge in common can't be put on one scale.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class P:
    id: int
    team: int
    track: int | None


@dataclass(frozen=True)
class J:
    id: int
    tracks: frozenset = frozenset()
    teams: frozenset = frozenset()


@dataclass
class Plan:
    new: list = field(default_factory=list)          # (judge id, project id)
    short: dict = field(default_factory=dict)        # project id -> reviews still missing
    components_before: int = 0
    components_after: int = 0
    bridges: list = field(default_factory=list)      # the subset of `new` added only to connect the graph


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def eligible(judge: J, project: P) -> bool:
    if project.team in judge.teams:
        return False
    return not judge.tracks or project.track in judge.tracks


def components(edges, projects, judges):
    """Connected pieces of the judge-project graph, counting only projects
    and judges that have at least one review between them."""
    uf = UnionFind()
    nodes = set()
    for j, p in edges:
        uf.union(("j", j), ("p", p))
        nodes |= {("j", j), ("p", p)}
    return uf, len({uf.find(n) for n in nodes})


def plan(projects, judges, existing, k):
    """`existing` is a list of (judge id, project id, active) where active is
    False for a recusal. Returns a Plan; changes nothing."""
    judges = sorted(judges, key=lambda j: j.id)
    projects = sorted(projects, key=lambda p: p.id)
    taken = {(j, p) for j, p, _ in existing}
    active = [(j, p) for j, p, is_active in existing if is_active]
    load = {j.id: 0 for j in judges}
    have = {p.id: 0 for p in projects}
    for j, p in active:
        if j in load:
            load[j] += 1
        if p in have:
            have[p] += 1
    uf, before = components(active, projects, judges)
    result = Plan(components_before=before)

    def add(judge, project, bridge=False):
        taken.add((judge.id, project.id))
        load[judge.id] += 1
        have[project.id] += 1
        uf.union(("j", judge.id), ("p", project.id))
        result.new.append((judge.id, project.id))
        if bridge:
            result.bridges.append((judge.id, project.id))

    progress = True
    while progress:
        progress = False
        for project in sorted(projects, key=lambda p: (have[p.id], p.id)):
            if have[project.id] >= k:
                continue
            candidates = [j for j in judges if (j.id, project.id) not in taken and eligible(j, project)]
            if not candidates:
                continue
            pnode = uf.find(("p", project.id))
            best = min(candidates, key=lambda j: (load[j.id], uf.find(("j", j.id)) == pnode, j.id))
            add(best, project)
            progress = True

    # Bridge whatever is still in pieces: join each smaller piece to the
    # largest with the least-loaded eligible pair across the gap.
    while True:
        edges = [(j, p) for j, p in active] + result.new
        uf2, count = components(edges, projects, judges)
        if count <= 1:
            break
        sizes = {}
        for node in uf2.parent:
            root = uf2.find(node)
            sizes[root] = sizes.get(root, 0) + 1
        main = max(sizes, key=lambda r: (sizes[r], str(r)))
        crossing = [(load[j.id], j.id, p.id, j, p) for j in judges for p in projects
                    if (j.id, p.id) not in taken and eligible(j, p)
                    and ("j", j.id) in uf2.parent and ("p", p.id) in uf2.parent
                    and (uf2.find(("j", j.id)) == main) != (uf2.find(("p", p.id)) == main)]
        if not crossing:
            break
        _, _, _, j, p = min(crossing)
        add(j, p, bridge=True)

    _, result.components_after = components([(j, p) for j, p in active] + result.new, projects, judges)
    result.short = {p.id: k - have[p.id] for p in projects if have[p.id] < k}
    return result
