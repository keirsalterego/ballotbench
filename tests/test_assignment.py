"""The planner on plain records: no database needed."""
import random
from collections import Counter

from portal.assignment import J, P, plan


def test_every_project_gets_k_and_load_is_even():
    projects = [P(i, team=100 + i, track=None) for i in range(12)]
    judges = [J(j) for j in range(6)]
    result = plan(projects, judges, [], k=3)
    per_project = Counter(p for _, p in result.new)
    per_judge = Counter(j for j, _ in result.new)
    assert all(per_project[p.id] == 3 for p in projects)
    assert max(per_judge.values()) - min(per_judge.values()) <= 1
    assert result.short == {}
    assert result.components_after == 1


def test_never_judges_own_team_and_respects_tracks():
    projects = [P(1, team=7, track=1), P(2, team=8, track=2), P(3, team=9, track=1)]
    judges = [J(1, teams=frozenset({7})), J(2, tracks=frozenset({2})), J(3), J(4, tracks=frozenset({1}))]
    result = plan(projects, judges, [], k=2)
    assert (1, 1) not in result.new
    assert (2, 1) not in result.new and (2, 3) not in result.new
    assert (4, 2) not in result.new


def test_existing_reviews_count_and_recusals_are_not_reassigned():
    projects = [P(1, 10, None), P(2, 20, None)]
    judges = [J(1), J(2), J(3)]
    existing = [(1, 1, True), (2, 1, False)]   # judge 2 stepped aside from project 1
    result = plan(projects, judges, existing, k=2)
    assert (2, 1) not in result.new
    assert Counter(p for _, p in result.new)[1] == 1   # one more on top of judge 1's


def test_reports_shortfall_when_too_few_eligible_judges():
    result = plan([P(1, 10, 5)], [J(1, tracks=frozenset({5})), J(2, tracks=frozenset({6}))], [], k=3)
    assert result.short == {1: 2}


def test_bridges_two_islands():
    # Two tracks with disjoint judges: without a bridge the graph splits.
    projects = [P(1, 10, 1), P(2, 20, 1), P(3, 30, 2), P(4, 40, 2)]
    judges = [J(1, tracks=frozenset({1})), J(2, tracks=frozenset({1})), J(3, tracks=frozenset({2})),
              J(4, tracks=frozenset({2})), J(5)]
    result = plan(projects, judges, [], k=1)
    assert result.components_after == 1


def test_plans_are_deterministic_and_valid_on_random_events():
    rng = random.Random(7)
    for _ in range(300):
        tracks = list(range(rng.randint(1, 4)))
        projects = [P(i, team=i, track=rng.choice(tracks)) for i in range(rng.randint(1, 25))]
        judges = [J(100 + j, tracks=frozenset(rng.sample(tracks, rng.randint(0, len(tracks)))),
                    teams=frozenset(rng.sample(range(len(projects)), min(1, len(projects)))))
                  for j in range(rng.randint(1, 10))]
        k = rng.randint(1, 4)
        a, b = plan(projects, judges, [], k), plan(list(reversed(projects)), list(reversed(judges)), [], k)
        assert a.new == b.new
        assert len(set(a.new)) == len(a.new)
        by_id = {j.id: j for j in judges}
        pj = {p.id: p for p in projects}
        for j, p in a.new:
            assert pj[p].team not in by_id[j].teams
            assert not by_id[j].tracks or pj[p].track in by_id[j].tracks
        got = Counter(p for _, p in a.new)
        for p in projects:
            assert got[p.id] + a.short.get(p.id, 0) == k or got[p.id] > k - 1


def test_loads_stay_within_one_when_everyone_is_eligible():
    rng = random.Random(11)
    for _ in range(200):
        n_projects, n_judges, k = rng.randint(1, 40), rng.randint(1, 12), rng.randint(1, 5)
        result = plan([P(i, 1000 + i, None) for i in range(n_projects)], [J(j) for j in range(n_judges)], [], k)
        # Bridging reviews are extra on purpose; balance is about coverage.
        loads = Counter(j for j, p in result.new if (j, p) not in result.bridges)
        counts = [loads.get(j, 0) for j in range(n_judges)]
        assert max(counts) - min(counts) <= 1, (n_projects, n_judges, k, counts)
