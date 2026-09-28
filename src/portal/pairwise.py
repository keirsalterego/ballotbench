"""A second opinion: the rubric read as pairwise picks.

Every judge who scored projects A and B differently has, in effect, said "A
is better than B". Collect those picks across all judges and fit a
Bradley-Terry model: P(A beats B) = p_A / (p_A + p_B).

What this buys over the calibration: only the order of a judge's own scores
matters, so it's unchanged by any increasing transformation of any judge's
scores, not just a shift and stretch. A judge who squashes the top of the
scale, which the linear calibration can't undo, still gives the same picks.
What it costs: how much better A is than B is thrown away, and a judge with
one review, or who gave everyone the same score, contributes no picks.

Fitted with Hunter's MM iteration (2004). Each project also plays one virtual
game won and one lost against a fixed reference of strength 1, a weak prior
that keeps an unbeaten project's strength finite and an isolated project at
the middle.
"""
import math
from collections import defaultdict
from itertools import combinations

MAX_ITER = 10000
TOL = 1e-10
PRIOR_GAMES = 1.0     # virtual wins and losses against the reference


def picks(obs):
    """obs: (judge, project, score). Returns [(winner, loser)], one per pair
    of projects a judge scored differently. Ties give no pick."""
    by_judge = defaultdict(dict)
    for j, p, y in obs:
        by_judge[j][p] = y
    out = []
    for scores in by_judge.values():
        for a, b in combinations(sorted(scores, key=str), 2):
            if scores[a] > scores[b]:
                out.append((a, b))
            elif scores[b] > scores[a]:
                out.append((b, a))
    return out


def fit(pairs, items=()):
    """Bradley-Terry strengths (log scale, mean 0) from (winner, loser) pairs."""
    items = sorted(set(items) | {x for pair in pairs for x in pair}, key=str)
    wins = defaultdict(float)
    games = defaultdict(lambda: defaultdict(float))
    for w, l in pairs:
        wins[w] += 1
        games[w][l] += 1
        games[l][w] += 1
    p = {i: 1.0 for i in items}
    for _ in range(MAX_ITER):
        new = {}
        for i in items:
            denom = sum(n / (p[i] + p[k]) for k, n in games[i].items())
            denom += 2 * PRIOR_GAMES / (p[i] + 1.0)             # one win, one loss vs the reference
            new[i] = (wins[i] + PRIOR_GAMES) / denom
        delta = max((abs(math.log(new[i]) - math.log(p[i])) for i in items), default=0.0)
        p = new
        if delta < TOL:
            break
    logs = {i: math.log(p[i]) for i in items}
    mean = sum(logs.values()) / len(logs) if logs else 0.0
    return {i: v - mean for i, v in logs.items()}


def kendall(order_a, order_b):
    """Kendall's tau between two orderings of the same items."""
    pos = {x: i for i, x in enumerate(order_b)}
    common = [x for x in order_a if x in pos]
    agree = total = 0
    for i in range(len(common)):
        for k in range(i + 1, len(common)):
            total += 1
            agree += pos[common[i]] < pos[common[k]]
    return 2 * agree / total - 1 if total else 1.0
