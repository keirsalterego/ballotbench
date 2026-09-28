"""The calibration model on plain numbers: its invariances, its handling of
judges it can't learn from, and whether it beats raw means on data where we
know the truth."""
import json
import random
from pathlib import Path

from portal.calibration import digest, fit, signal_test

FIXTURES = json.loads((Path(__file__).resolve().parent.parent / "fixtures.json").read_text())


def fixture_obs():
    return [(s["judge"], s["project"], (sum(s["criteria"].values()) / 3 - 1) / 4) for s in FIXTURES["scores"]]


def qs(result):
    return {p: f.quality for p, f in result.projects.items()}


def max_diff(a, b):
    return max(abs(a[p] - b[p]) for p in b)


def test_converges_on_the_fixture():
    result = fit(fixture_obs())
    assert result.converged
    assert result.components == 1


def test_shifting_one_judge_changes_nothing():
    base = fit(fixture_obs())
    shifted = fit([(j, p, y + 0.3 if j == "jdg_24" else y) for j, p, y in fixture_obs()])
    assert max_diff(qs(base), qs(shifted)) < 1e-9


def test_stretching_one_judge_changes_nothing():
    base = fit(fixture_obs())
    stretched = fit([(j, p, 2 * y if j == "jdg_24" else y) for j, p, y in fixture_obs()])
    assert max_diff(qs(base), qs(stretched)) < 1e-9


def test_every_judge_affine_at_once_changes_nothing():
    rng = random.Random(3)
    moves = {j["id"]: (rng.uniform(-2, 2), rng.uniform(0.2, 5)) for j in FIXTURES["judges"]}
    base = fit(fixture_obs())
    moved = fit([(j, p, moves[j][0] + moves[j][1] * y) for j, p, y in fixture_obs()])
    assert max_diff(qs(base), qs(moved)) < 1e-9


def test_constant_judge_counts_as_if_absent():
    obs = fixture_obs()
    result = fit(obs)
    assert result.judges["jdg_07"].flag == "constant"
    assert result.judges["jdg_07"].scale == 0
    without = fit([o for o in obs if o[0] != "jdg_07"])
    assert max_diff(qs(result), qs(without)) == 0


def test_single_review_judges_are_flagged_and_ignored():
    result = fit(fixture_obs())
    for j in ("jdg_01", "jdg_23"):
        assert result.judges[j].flag == "single_review"
        assert result.judges[j].scale == 0


def test_no_reviews_no_crash():
    assert fit([]).projects == {}


def test_all_constant_judges_gives_prior():
    result = fit([("a", 1, 0.5), ("a", 2, 0.5), ("b", 1, 0.7), ("b", 2, 0.7)])
    assert all(f.quality == 0 and f.se == 1 for f in result.projects.values())


def kendall(order_a, order_b):
    pos = {x: i for i, x in enumerate(order_b)}
    agree = total = 0
    for i in range(len(order_a)):
        for k in range(i + 1, len(order_a)):
            total += 1
            agree += pos[order_a[i]] < pos[order_a[k]]
    return 2 * agree / total - 1


def synthetic(seed, n_projects=40, n_judges=12, k=3):
    """Judges with their own leniency, harshness and noise; one judge gets
    only strong projects and scores them harshly, the case z-scores get
    wrong. Returns (observations, true order)."""
    rng = random.Random(seed)
    truth = {p: rng.gauss(0, 1) for p in range(n_projects)}
    judges = {j: (rng.uniform(-0.15, 0.15), rng.uniform(0.05, 0.2), rng.uniform(0.02, 0.08)) for j in range(n_judges)}
    ranked = sorted(truth, key=truth.get, reverse=True)
    obs = []
    for p in range(n_projects):
        chosen = rng.sample(range(1, n_judges), k)
        if p in ranked[:8]:
            chosen[0] = 0                       # the harsh judge sees the best projects
        for j in chosen:
            a, s, v = judges[j]
            if j == 0:
                a -= 0.25
            obs.append((j, p, 0.5 + a + s * truth[p] + rng.gauss(0, v)))
    return obs, ranked


def test_recovers_truth_better_than_raw_means():
    wins, gain = 0, 0.0
    for seed in range(20):
        obs, truth = synthetic(seed)
        result = fit(obs)
        model = sorted(result.projects, key=lambda p: -result.projects[p].quality)
        raw = sorted(result.projects, key=lambda p: -result.projects[p].raw_mean)
        tm, tr = kendall(model, truth), kendall(raw, truth)
        wins += tm > tr
        gain += tm - tr
    assert wins >= 16
    assert gain / 20 > 0.03


def test_signal_test_sees_signal_when_there_is_some():
    obs, _ = synthetic(1)
    _, _, p = signal_test(obs, shuffles=300)
    assert p < 0.01


def test_digest_ignores_order():
    rows = [["jdg_01", "prj_01", 3], ["jdg_02", "prj_01", 4]]
    assert digest(rows) == digest(list(reversed(rows)))


def test_the_proof_command_passes_on_the_seeded_event(db):
    import io
    from django.core.management import call_command
    out = io.StringIO()
    call_command("normalization_proof", stdout=out)
    text = out.getvalue()
    assert "Every invariance check holds." in text
    assert "jdg_07" in text and "constant" in text


def test_bootstrap_intervals_are_wide_on_noise_and_narrow_on_signal():
    from portal.calibration import bootstrap
    noise = bootstrap(fixture_obs(), resamples=60)
    obs, _ = synthetic(2)
    signal = bootstrap(obs, resamples=60)
    width = lambda b: sorted(h - l for l, h, _ in b.values())[len(b) // 2]
    assert width(noise) > 2 * width(signal)
