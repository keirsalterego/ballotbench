"""The pairwise second opinion on plain numbers."""
import json
import math
import random
from pathlib import Path

from portal.calibration import fit as calibrate
from portal.pairwise import fit, kendall, picks

FIXTURES = json.loads((Path(__file__).resolve().parent.parent / "fixtures.json").read_text())
OBS = [(s["judge"], s["project"], (sum(s["criteria"].values()) / 3 - 1) / 4) for s in FIXTURES["scores"]]


def order(strengths):
    return sorted(strengths, key=lambda p: (-strengths[p], p))


def test_picks_come_from_within_one_judge_and_skip_ties():
    obs = [("a", 1, 3), ("a", 2, 5), ("a", 3, 5), ("b", 1, 4), ("b", 3, 2)]
    assert sorted(picks(obs)) == sorted([(2, 1), (3, 1), (1, 3)])


def test_the_constant_judge_makes_no_picks():
    assert not [p for p in picks([o for o in OBS if o[0] == "jdg_07"])]


def test_any_increasing_transform_of_a_judge_changes_nothing():
    base = fit(picks(OBS))
    squashed = [(j, p, math.sqrt(y) ** 3 + 7 if j in ("jdg_24", "jdg_29") else y) for j, p, y in OBS]
    assert fit(picks(squashed)) == base


def test_strengths_are_finite_for_an_unbeaten_project():
    s = fit([(1, 2), (1, 3), (1, 2)])
    assert all(math.isfinite(v) for v in s.values()) and order(s)[0] == 1


def test_isolated_project_sits_in_the_middle():
    s = fit([(1, 2)], items=[1, 2, 3])
    assert abs(s[3]) < abs(s[1]) and abs(s[3]) < abs(s[2])


def test_recovers_a_clear_order():
    rng = random.Random(4)
    truth = list(range(12))
    pairs = []
    for _ in range(400):
        a, b = rng.sample(truth, 2)
        p_a = 1 / (1 + math.exp(-(b - a) * 0.6))       # lower number is stronger
        pairs.append((a, b) if rng.random() < p_a else (b, a))
    assert kendall(order(fit(pairs)), truth) > 0.8


def test_on_the_fixture_it_runs_and_is_comparable_to_calibration():
    s = fit(picks(OBS), items=[p["id"] for p in FIXTURES["projects"]])
    cal = calibrate(OBS)
    tau = kendall(order(s), sorted(cal.projects, key=lambda p: -cal.projects[p].quality))
    assert -1 <= tau <= 1 and len(s) == len(FIXTURES["projects"])
