"""Putting every judge on one scale.

Each review gives one number y (the weighted rubric score, 0..1). We model

    y_ij = a_j + s_j * q_i + e_ij,    e_ij ~ N(0, v_j),    q_i ~ N(0, 1)

q_i is project i's quality, the thing we want. a_j is judge j's leniency
(offset), s_j how strongly their scores follow quality (scale), v_j how noisy
they are. The fit alternates two least-squares steps until q stops moving:

  q_i  = sum_j s_j (y_ij - a_j) / v_j  /  (1 + sum_j s_j^2 / v_j)
         the posterior mean of q_i under its N(0, 1) prior: a project seen
         by few or noisy judges is pulled towards the middle, not trusted
         blindly. Then q is centred and scaled to mean 0, sd 1.
  s_j, a_j = ridge least squares of the judge's scores on q over their
         projects, s_j = Sxy / (Sxx + 1), with s_j >= 0
  v_j  = (nu * v0_j + RSS_j) / (nu + n_j),  v0_j = var(y_j) / 2
         an empirical-Bayes shrink of the judge's noise towards a prior
         built from their own spread, so a judge with three reviews doesn't
         get a noise of zero by fitting them exactly.

Why not a per-judge z-score: it divides by zero for a judge who gives the
same score every time, and it shifts a judge whose batch happened to be
strong or weak, since it assumes every judge saw an average batch. Here a
judge's offset is anchored by the projects they share with other judges.

Judges the model can't learn from get s_j = 0, and a judge with s_j = 0
drops out of every q exactly, as if their reviews were not there:
  constant       every review the same score (the fixture's jdg_07)
  single_review  one review: nothing to compare it with (jdg_01, jdg_23)
  discordant     their scores don't rise with everyone else's (s_j fits <= 0)

Invariances, which tests/test_calibration.py checks: adding a constant to
one judge's scores, or multiplying them by a positive factor, leaves every
q exactly unchanged; removing a constant judge changes nothing.
"""
import hashlib
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field

NU = 3.0            # prior weight on a judge's noise, in reviews
RIDGE = 1.0         # prior weight pulling a judge's scale towards 0, in q units
MAX_ITER = 1000
TOL = 1e-12


@dataclass
class JudgeFit:
    n: int
    mean: float
    offset: float = 0.0
    scale: float = 0.0
    noise: float = 0.0
    flag: str = "ok"


@dataclass
class ProjectFit:
    n: int
    raw_mean: float
    quality: float = 0.0
    se: float = 1.0
    usable: int = 0     # reviews from judges with s_j > 0


@dataclass
class Fit:
    projects: dict = field(default_factory=dict)
    judges: dict = field(default_factory=dict)
    iterations: int = 0
    converged: bool = False
    components: int = 0
    mean: float = 0.0
    spread: float = 0.0

    def display(self, pid):
        """q back on the rubric's 0..1 scale: the average project plus q
        standard deviations of the project means."""
        return self.mean + self.spread * self.projects[pid].quality


def _mean(xs):
    return sum(xs) / len(xs)


def _var(xs, m=None):
    m = _mean(xs) if m is None else m
    return sum((x - m) ** 2 for x in xs) / len(xs)


def components(obs):
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for j, p, _ in obs:
        parent[find(("j", j))] = find(("p", p))
    return len({find(x) for x in list(parent)})


def fit(obs):
    """obs: iterable of (judge, project, y). Returns a Fit."""
    obs = list(obs)
    by_j, by_p = defaultdict(list), defaultdict(list)
    for j, p, y in obs:
        by_j[j].append((p, y))
        by_p[p].append((j, y))
    result = Fit(components=components(obs))
    if not obs:
        return result

    judges = {}
    for j, rows in by_j.items():
        ys = [y for _, y in rows]
        m, var = _mean(ys), _var(ys)
        jf = JudgeFit(n=len(ys), mean=m, offset=m)
        if len(ys) == 1:
            jf.flag = "single_review"
        elif var <= 1e-12 * max(1.0, m * m):
            jf.flag = "constant"
        else:
            jf.scale, jf.noise = math.sqrt(var), var / 2
        judges[j] = jf
    prior = {j: _var([y for _, y in by_j[j]]) / 2 for j in judges}

    q = {p: 0.0 for p in by_p}
    for iteration in range(1, MAX_ITER + 1):
        new, precision = {}, {}
        for p, rows in by_p.items():
            num, den = 0.0, 1.0
            for j, y in rows:
                jf = judges[j]
                if jf.scale > 0:
                    num += jf.scale * (y - jf.offset) / jf.noise
                    den += jf.scale ** 2 / jf.noise
            new[p], precision[p] = num / den, den
        informed = [p for p in new if precision[p] > 1.0]
        if informed:
            centre = _mean([new[p] for p in informed])
            sd = math.sqrt(_var([new[p] for p in informed], centre)) or 1.0
        else:
            centre, sd = 0.0, 1.0
        new = {p: (new[p] - centre) / sd if precision[p] > 1.0 else 0.0 for p in new}
        delta = max(abs(new[p] - q[p]) for p in q)
        q = new
        for j, jf in judges.items():
            if jf.flag in ("single_review", "constant"):
                continue
            xs = [q[p] for p, _ in by_j[j]]
            ys = [y for _, y in by_j[j]]
            mx, my = _mean(xs), _mean(ys)
            sxx = sum((x - mx) ** 2 for x in xs)
            sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            # Ridge: without it, a judge whose few projects landed close
            # together in q gets an enormous scale, then drags those projects
            # further together, and the fit runs away. RIDGE is in q units,
            # which have no scale of their own, so the invariances still hold.
            scale = sxy / (sxx + RIDGE)
            if scale <= 0:
                jf.scale, jf.offset, jf.flag = 0.0, my, "discordant"
                continue
            jf.flag = "ok"
            jf.scale, jf.offset = scale, my - scale * mx
            rss = sum((y - jf.offset - jf.scale * x) ** 2 for x, y in zip(xs, ys))
            jf.noise = max((NU * prior[j] + rss) / (NU + jf.n), 1e-9 * prior[j])
        if delta < TOL:
            result.converged = True
            break
    result.iterations = iteration

    means = {p: _mean([y for _, y in rows]) for p, rows in by_p.items()}
    result.mean = _mean(list(means.values()))
    result.spread = math.sqrt(_var(list(means.values()), result.mean))
    for p, rows in by_p.items():
        den = 1.0 + sum(judges[j].scale ** 2 / judges[j].noise for j, _ in rows if judges[j].scale > 0)
        result.projects[p] = ProjectFit(n=len(rows), raw_mean=means[p], quality=q[p], se=1 / math.sqrt(den),
                                        usable=sum(1 for j, _ in rows if judges[j].scale > 0))
    result.judges = judges
    return result


def signal_test(obs, shuffles=2000, seed=1):
    """Do the judges agree about which projects are better any more than
    chance would? Compare the spread of project means with the spread after
    shuffling every score across the same judge-project slots. Returns
    (observed variance, mean shuffled variance, p-value)."""
    obs = list(obs)
    slots = [(j, p) for j, p, _ in obs]
    ys = [y for _, _, y in obs]

    def spread(values):
        sums, counts = defaultdict(float), defaultdict(int)
        for (_, p), y in zip(slots, values):
            sums[p] += y
            counts[p] += 1
        means = [sums[p] / counts[p] for p in sums]
        return _var(means)

    observed = spread(ys)
    rng = random.Random(seed)
    shuffled, beats = [], 0
    for _ in range(shuffles):
        values = ys[:]
        rng.shuffle(values)
        v = spread(values)
        shuffled.append(v)
        beats += v >= observed
    return observed, _mean(shuffled), (beats + 1) / (shuffles + 1)


def digest(rows):
    """SHA-256 over the exact scores a run read, in a canonical order, so
    anyone with the scores export can check a published result."""
    canon = json.dumps(sorted(rows), separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def bootstrap(obs, rankable=None, resamples=200, seed=1):
    """How much would the ranking move if the same judges had happened to
    write slightly different reviews? Resample each project's reviews with
    replacement, refit, and record where each project lands.

    The model's own standard error assumes the judges' offsets and scales
    are known exactly. They aren't: with three or four reviews each they are
    estimated from little, and on scores with no real signal the fit can
    move a project many places on noise. The bootstrap interval shows that.

    Returns {project: (rank_low, rank_high, sd_of_q)} with a 90% interval."""
    obs = list(obs)
    by_p = defaultdict(list)
    for o in obs:
        by_p[o[1]].append(o)
    projects = sorted(by_p, key=str)
    ranked = [p for p in projects if rankable is None or p in rankable]
    rng = random.Random(seed)
    ranks, qs = defaultdict(list), defaultdict(list)
    for _ in range(resamples):
        sample = [rng.choice(by_p[p]) for p in projects for _ in by_p[p]]
        f = fit(sample)
        order = sorted(ranked, key=lambda p: (-f.projects[p].quality, str(p)))
        for rank, p in enumerate(order, 1):
            ranks[p].append(rank)
            qs[p].append(f.projects[p].quality)
    result = {}
    for p in ranked:
        rs = sorted(ranks[p])
        low, high = rs[int(0.05 * (len(rs) - 1))], rs[int(math.ceil(0.95 * (len(rs) - 1)))]
        result[p] = (low, high, math.sqrt(_var(qs[p])))
    return result
