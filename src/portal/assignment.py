"""Who reviews what. A pure planner on plain records, so it can be tested
without a database, plus the thin layer that reads and writes the models.

The plan tops every project up to k reviews:

1. The least-loaded judge who can still help picks next, so loads stay
   within one review of each other where eligibility allows.
2. They take the neediest project they are eligible for: fewest reviews so
   far, then fewest judges left who could take it, so a shortage is spread
   evenly and scarce judges go where only they can help. Eligible means: not
   on the project's team, the project's track is one of theirs (or they
   have none), and they don't already hold it (a recusal counts: we don't
   hand a project back to a judge who stepped aside from it).
3. On a tie, prefer a project that joins two parts of the judge-project
   graph not yet connected, then the lowest id, so plans are deterministic.
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

    def candidates(project):
        return [j for j in judges if (j.id, project.id) not in taken and eligible(j, project)]

    # Judge-driven: the least-loaded judge who can still help takes the
    # neediest project they're eligible for. Driving by judge keeps loads
    # within one of each other; choosing the neediest project (fewest
    # reviews, then fewest judges left who could take it) keeps coverage even.
    while True:
        needy = [p for p in projects if have[p.id] < k]
        if not needy:
            break
        for judge in sorted(judges, key=lambda j: (load[j.id], j.id)):
            options = [p for p in needy if (judge.id, p.id) not in taken and eligible(judge, p)]
            if options:
                jnode = uf.find(("j", judge.id))
                project = min(options, key=lambda p: (have[p.id], len(candidates(p)),
                                                      uf.find(("p", p.id)) == jnode, p.id))
                add(judge, project)
                break
        else:
            break

    # Greedy can paint itself into a corner: the idle judges already hold the
    # last projects that need someone. Repair by moving one of this plan's new
    # reviews from the busiest judge to the idlest judge who may take it,
    # until no such move narrows the gap. Existing reviews are never moved.
    by_id = {j.id: j for j in judges}
    by_pid = {p.id: p for p in projects}
    while True:
        order = sorted(load, key=lambda j: (load[j], j))
        moved = False
        for low in order:
            for high in reversed(order):
                if load[high] - load[low] <= 1:
                    break
                for index, (j, pid) in enumerate(result.new):
                    if j == high and (low, pid) not in taken and eligible(by_id[low], by_pid[pid]):
                        taken.discard((high, pid))
                        taken.add((low, pid))
                        result.new[index] = (low, pid)
                        load[high] -= 1
                        load[low] += 1
                        moved = True
                        break
                if moved:
                    break
            if moved:
                break
        if not moved:
            break
    uf = UnionFind()
    for j, p in active + result.new:
        uf.union(("j", j), ("p", p))

    # Bridge whatever is still in pieces with the least-loaded eligible pair
    # that joins two of them. A judge with no reviews yet can bridge too, in
    # two steps: first into one piece, then across to another. Each pass adds
    # one new pair, so this ends.
    while True:
        edges = [(j, p) for j, p in active] + result.new
        uf2, count = components(edges, projects, judges)
        if count <= 1:
            break
        joining, entering = [], []
        for j in judges:
            jin = ("j", j.id) in uf2.parent
            for p in projects:
                if (j.id, p.id) in taken or not eligible(j, p) or ("p", p.id) not in uf2.parent:
                    continue
                if jin and uf2.find(("j", j.id)) != uf2.find(("p", p.id)):
                    joining.append((load[j.id], j.id, p.id, j, p))
                elif not jin and any(eligible(j, q) and (j.id, q.id) not in taken and ("p", q.id) in uf2.parent
                                     and uf2.find(("p", q.id)) != uf2.find(("p", p.id)) for q in projects):
                    entering.append((load[j.id], j.id, p.id, j, p))
        options = joining or entering
        if not options:
            break
        _, _, _, j, p = min(options)
        add(j, p, bridge=True)

    _, result.components_after = components([(j, p) for j, p in active] + result.new, projects, judges)
    result.short = {p.id: k - have[p.id] for p in projects if have[p.id] < k}
    return result
