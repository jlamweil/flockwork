"""Calibration toolkit for swarmo verdicts and leaderboards.

Implements the statistical half of spec pillar 4/5 (architecture.md):
Wilson score gates for the verification ladder, Reference-Anchored Elo
Estimation (RAEE) with Jeffreys smoothing and finite-population
correction, a directed correction graph over agent-configuration
vertices, and Bradley-Terry preference fitting for arena-style
pairwise comparisons.

Pure stdlib; float ops only. Deterministic (no RNG).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

# --- Verification ladder (survey B) -------------------------------------

LADDER = ("refuted", "conjectured", "heuristic", "measured", "verified", "proven")
LADDER_RANK = {rung: i for i, rung in enumerate(LADDER)}

# Production gate from tools/verdict_scoring.py (HQ): Wilson LCB90
# >= 0.70 accept / 0.75 agree, MIN_N = 5.
MIN_N = 5
ACCEPT_LCB = 0.70
AGREE_LCB = 0.75

# z for a 90% two-sided Wilson interval (= one-sided 95%).
# NOTE: HQ's exact z convention is unconfirmed; see design.md "gate z
# ambiguity". Pass z explicitly to reproduce a specific convention.
Z90 = 1.6449


def wilson_lcb(successes: int, n: int, z: float = Z90) -> float:
    """Wilson score lower bound for a binomial proportion.

    Returns 0.0 when n == 0 (nothing observed, nothing credited).
    """
    if n <= 0:
        return 0.0
    if successes < 0 or successes > n:
        raise ValueError(f"successes={successes} out of range for n={n}")
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, center - half)


def gate_rung(successes: int, n: int, z: float = Z90) -> str:
    """Map a (successes, n) record onto the ladder.

    Production semantics (survey B): a lane may only pass the accept
    gate with at least MIN_N observations; a 4/4 lane stays unproven
    no matter how high its point estimate.
    """
    lcb = wilson_lcb(successes, n, z)
    if n < MIN_N:
        return "measured" if n > 0 else "conjectured"
    if lcb >= AGREE_LCB:
        return "proven"
    if lcb >= ACCEPT_LCB:
        return "verified"
    return "measured"


# --- RAEE: Reference-Anchored Elo Estimation (survey B / spec 5.3) -------

def jeffreys_win_prob(wins: int, n: int) -> float:
    """Jeffreys-smoothed P(config beats reference): (W + 0.5) / (N + 1)."""
    if n < 0 or wins < 0 or wins > n:
        raise ValueError(f"wins={wins} out of range for n={n}")
    return (wins + 0.5) / (n + 1.0)


def raee_elo(wins: int, n: int) -> float:
    """Logit-map a smoothed win probability onto the Elo scale."""
    p = jeffreys_win_prob(wins, n)
    return 400.0 * math.log10(p / (1.0 - p))


def raee_elo_ci(wins: int, n: int, ref_size: int, z: float = Z90) -> tuple[float, float]:
    """Analytic Elo CI with finite-population correction (FPC).

    ref_size is the size of the fixed reference task suite; when the
    sample n approaches it, uncertainty shrinks by sqrt((N-n)/(N-1))
    per spec 5.3. Delta-method transform of the binomial SE.
    """
    if ref_size < 1:
        raise ValueError("ref_size must be >= 1")
    if n > ref_size:
        raise ValueError("n cannot exceed the reference suite size")
    p = jeffreys_win_prob(wins, n)
    elo = raee_elo(wins, n)
    fpc = math.sqrt((ref_size - n) / max(1, ref_size - 1))
    # Delta method: d(elo)/dp = 400 / (ln(10) * p * (1-p))
    se_p = math.sqrt(p * (1 - p) / max(1, n + 1)) * fpc
    d = 400.0 / (math.log(10.0) * p * (1.0 - p))
    half = z * d * se_p
    return (elo - half, elo + half)


# --- Directed correction graph (spec 5.1) --------------------------------

@dataclass(frozen=True)
class Vertex:
    """Agent configuration tuple v = <Model, Prompt template, Harness version>."""

    model: str
    prompt: str
    harness: str

    def key(self) -> str:
        return f"{self.model}|{self.prompt}|{self.harness}"


@dataclass
class CorrectionGraph:
    """Edges e_ij: vertex i corrects or refutes vertex j's output.

    weight: +1.0 per correction, -1.0 per refutation of the source's
    claim about the target, accumulated (multi-reviews compound).
    """

    edges: dict = field(default_factory=lambda: defaultdict(float))
    edge_counts: dict = field(default_factory=lambda: defaultdict(int))

    def add_edge(self, src: Vertex, dst: Vertex, kind: str, weight: float = 1.0):
        if kind not in ("corrects", "refutes"):
            raise ValueError(f"unknown edge kind: {kind}")
        signed = weight if kind == "corrects" else -weight
        self.edges[(src.key(), dst.key())] += signed
        self.edge_counts[(src.key(), dst.key())] += 1

    def critics_of(self, v: Vertex) -> list[str]:
        """Vertices that flagged v's output, strongest signal first."""
        k = v.key()
        return sorted(
            (s for (s, t) in self.edges if t == k),
            key=lambda s: -abs(self.edges[(s, k)]),
        )

    def repairs_by(self, v: Vertex) -> list[str]:
        """Vertices whose output v corrected, strongest signal first."""
        k = v.key()
        return sorted(
            (t for (s, t) in self.edges if s == k),
            key=lambda t: -abs(self.edges[(k, t)]),
        )

    def chronic_targets(self, min_edges: int = 2) -> list[tuple[str, float]]:
        """Vertices repeatedly flagged across independent critics.

        These are the graph's dispatch hints for repair tasks (spec 5.2:
        refutation dispatches a follow-up repair task to the queue).
        """
        tally = defaultdict(float)
        count = defaultdict(int)
        for (s, t), w in self.edges.items():
            tally[t] += w
            count[t] += 1
        return sorted(
            ((t, tally[t]) for t in tally if count[t] >= min_edges),
            key=lambda kv: kv[1],
        )


# --- Bradley-Terry arena fitting (spec 5.3 Arena-Lite) --------------------

def bradley_terry(wins: dict[tuple[str, str], int], iters: int = 200) -> dict[str, float]:
    """Fit latent strengths from pairwise win counts via MM iteration.

    wins[(i, j)] = number of times i beat j. Returns strengths with
    logs normalized to mean zero (identifiability fix); ties are
    represented by putting half a win in each direction.
    """
    players = {p for pair in wins for p in pair}
    if not players:
        return {}
    rate = {p: 1.0 for p in players}
    for _ in range(iters):
        new = {}
        for p in players:
            w_sum = 0.0
            exp_sum = 0.0
            for (i, j), w in wins.items():
                if i == p:
                    w_sum += w
                    exp_sum += w * rate[p] / (rate[p] + rate[j])
                elif j == p:
                    w_sum += 0.0
                    exp_sum += w * rate[p] / (rate[i] + rate[p])
            new[p] = w_sum / exp_sum if exp_sum > 0 else rate[p]
        # Winless players can hit rate 0; clamp so log() stays finite.
        rate = {p: max(r, 1e-9) for p, r in new.items()}
    logs = {p: math.log(r) for p, r in rate.items()}
    mean = sum(logs.values()) / len(logs)
    return {p: logs[p] - mean for p in logs}
