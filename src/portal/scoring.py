"""Weighted rubric scores.

A review's score is the weighted mean of its criteria, each first mapped to
0..1 by its own range, so a criterion scored 1..10 and one scored 1..5 count
by their weights and not by the width of their scale:

    score = sum_c w_c * (x_c - min_c) / (max_c - min_c)  /  sum_c w_c

A review missing a criterion is incomplete and has no score (None)."""


def weighted_score(review, criteria=None):
    criteria = criteria if criteria is not None else list(review.assignment.event.criteria.all())
    values = {s.criterion_id: s.value for s in review.scores.all()}
    if not criteria or any(c.pk not in values for c in criteria):
        return None
    total = sum(float(c.weight) for c in criteria)
    return sum(float(c.weight) * (values[c.pk] - c.min_value) / (c.max_value - c.min_value)
               for c in criteria) / total
