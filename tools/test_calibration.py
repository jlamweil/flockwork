import math

import pytest

from calibration import (
    LADDER,
    CorrectionGraph,
    Vertex,
    bradley_terry,
    gate_rung,
    jeffreys_win_prob,
    raee_elo,
    raee_elo_ci,
    wilson_lcb,
)


# --- Wilson gate ---------------------------------------------------------

def test_wilson_lcb_zero_observations_is_zero():
    assert wilson_lcb(0, 0) == 0.0


def test_wilson_lcb_perfect_small_sample_below_point_estimate():
    # 5/5 must land strictly below 1.0 — the gate must distrust small n.
    assert wilson_lcb(5, 5) < 1.0


def test_wilson_lcb_increases_with_n_at_fixed_rate():
    assert wilson_lcb(50, 50) > wilson_lcb(5, 5)
    assert wilson_lcb(90, 100) > wilson_lcb(9, 10)


def test_wilson_lcb_rejects_out_of_range():
    with pytest.raises(ValueError):
        wilson_lcb(6, 5)


def test_min_n_holds_four_for_four_unproven():
    # Recorded production behavior (survey B): a 4/4 lane is unproven.
    assert gate_rung(4, 4) != "proven"


def test_gate_holds_at_lcb_floor():
    # Sanity: the 0.70 floor must not admit a coin-flip lane at n=10.
    assert gate_rung(5, 10) in ("measured",)


def test_ladder_ordering():
    assert LADDER.index("refuted") < LADDER.index("proven")


def test_z_convention_changes_five_for_five_verdict():
    # The gate z ambiguity surfaced tonight (design.md): 5/5 promotes
    # under the one-sided-95% z but not the two-sided-90% z.
    one_sided_95 = gate_rung(5, 5, z=1.2816)
    two_sided_90 = gate_rung(5, 5, z=1.6449)
    assert one_sided_95 == "proven"
    assert two_sided_90 != "proven"


# --- RAEE ----------------------------------------------------------------

def test_jeffreys_smoothing_keeps_extremes_finite():
    p = jeffreys_win_prob(0, 0)
    assert 0.0 < p < 1.0
    assert math.isfinite(raee_elo(0, 0))
    assert math.isfinite(raee_elo(100, 100))


def test_raee_elo_anchored_near_zero_at_even_record():
    # (0.5 + 0.5)/(1 + 1) = 0.5 -> Elo 0 exactly.
    assert raee_elo(0, 0) == pytest.approx(0.0, abs=1e-9)
    assert raee_elo(5, 10) == pytest.approx(0.0, abs=1e-9)


def test_raee_elo_monotone_in_win_rate():
    assert raee_elo(8, 10) > raee_elo(6, 10) > raee_elo(4, 10)


def test_raee_ci_narrows_with_fpc_and_sample():
    lo1, hi1 = raee_elo_ci(8, 10, ref_size=100)
    lo2, hi2 = raee_elo_ci(80, 100, ref_size=100)
    assert (hi1 - lo1) > (hi2 - lo2)
    assert lo1 < raee_elo(8, 10) < hi1


def test_raee_ci_rejects_oversample():
    with pytest.raises(ValueError):
        raee_elo_ci(8, 10, ref_size=5)


# --- Correction graph ----------------------------------------------------

def test_correction_graph_records_and_ranks_critics():
    v1 = Vertex("m1", "p", "h1")
    v2 = Vertex("m2", "p", "h2")
    g = CorrectionGraph()
    g.add_edge(v1, v2, "refutes")
    g.add_edge(v2, v1, "corrects")
    assert g.critics_of(v2) == [v1.key()]
    assert g.repairs_by(v2) == [v1.key()]
    assert g.critics_of(v1) == [v2.key()]


def test_chronic_targets_require_independent_critics():
    src = Vertex("m1", "p", "h1")
    bad = Vertex("bad", "p", "h")
    other = Vertex("m2", "p", "h")
    g = CorrectionGraph()
    g.add_edge(src, bad, "refutes")
    assert g.chronic_targets() == []
    g.add_edge(other, bad, "refutes")
    targets = g.chronic_targets()
    assert targets and targets[0][0] == bad.key()


def test_refutation_flips_sign():
    src = Vertex("a", "p", "h")
    dst = Vertex("b", "p", "h")
    g = CorrectionGraph()
    g.add_edge(src, dst, "corrects")
    g.add_edge(src, dst, "refutes")
    assert g.edges[(src.key(), dst.key())] == 0.0


def test_unknown_edge_kind_rejected():
    g = CorrectionGraph()
    with pytest.raises(ValueError):
        g.add_edge(Vertex("a", "p", "h"), Vertex("b", "p", "h"), "likes")


# --- Bradley-Terry ---------------------------------------------------------

def test_bt_recovers_dominance_order():
    wins = {
        ("strong", "weak"): 9,
        ("strong", "mid"): 8,
        ("mid", "weak"): 8,
    }
    s = bradley_terry(wins)
    assert s["strong"] > s["mid"] > s["weak"]


def test_bt_handles_cycle_without_crashing():
    wins = {("a", "b"): 5, ("b", "c"): 5, ("c", "a"): 5}
    s = bradley_terry(wins)
    assert len(s) == 3
    assert all(math.isfinite(v) for v in s.values())


def test_bt_empty():
    assert bradley_terry({}) == {}
