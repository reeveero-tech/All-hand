#!/usr/bin/env python3
"""R-170 batch 18 (لاحق 80): deterministic power/sensitivity calculation for the
v10 language judgment package (PREREGISTRATION_V10 §3).

Why this exists (v9 post-mortem, log 316): the B1 margin criterion
(``v4r_only − v4_only ≥ 3`` on paired classifiable Arabic cases) was
STRUCTURALLY UNTESTABLE in v9 — the control arm (v4-revised prompt, no retry
policy) failed Arabic only 1/16, so a margin of ≥3 could not occur no matter
how good the candidate was. The v7 judgment put the same control at 4/17
(24%). The control's Arabic failure rate is the free variable this
calculation sizes for.

Model (documented approximation, registered BEFORE any v10 run):
- per-case paired outcomes, control fails with probability p_c, candidate
  (v4r) fails with probability p_r, arms independent given the case
  (conservative for discordance: shared case difficulty would concentrate
  discordants, making B1 EASIER than modeled here);
- cells per case: both-pass, control-only-fail (b_only), candidate-only-fail
  (a_only), both-fail — multinomial;
- B1 satisfiable ⟺ (b_only − a_only ≥ 3) ∧ (a_only ≤ 1) ∧ (b > a, implied
  by the margin);
- seeded Monte Carlo (seed 20261029, 200,000 trials per scenario) — fully
  deterministic given the committed seed.

Observed inputs (v7 log 314, v9 log 316):
- control Arabic failure rate: 1/16 (6.3%, v9) and 4/17 (23.5%, v7) —
  scenarios span 6%..25%;
- candidate Arabic failure rate: 0/16 (v9) — scenarios 0%..3%;
- Arabic classifiable share: 16/23 and 17/23 (~70%); English ~17%.

Usage:
    python scripts/r169_power_v10.py            # print the sensitivity table
"""
from __future__ import annotations

import argparse

SEED = 20261029
TRIALS = 200_000

# n = paired classifiable Arabic cases; scenarios span the sizing decision.
N_VALUES = (45, 55, 63, 70)
P_C = (0.06, 0.10, 0.15, 0.25)   # control (v4) Arabic failure rate
P_R = (0.00, 0.03)               # candidate (v4r) Arabic failure rate


def b1_probability(n: int, p_c: float, p_r: float) -> float:
    """P(B1 satisfiable) under the registered model — seeded, deterministic."""
    import random
    rng = random.Random(f"{SEED}-{n}-{round(p_c * 1000)}-{round(p_r * 1000)}")
    ok = 0
    for _ in range(TRIALS):
        b_only = 0  # control fails, candidate passes
        a_only = 0  # candidate fails, control passes
        for _ in range(n):
            cf = rng.random() < p_c
            rf = rng.random() < p_r
            if cf and not rf:
                b_only += 1
            elif rf and not cf:
                a_only += 1
        if b_only - a_only >= 3 and a_only <= 1:
            ok += 1
    return ok / TRIALS


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    args = ap.parse_args(argv)
    print(f"seed={SEED} trials={TRIALS} (deterministic)")
    print("B1 = (v4r_only − v4_only ≥ 3) ∧ (candidate-only failures ≤ 1) on paired classifiable Arabic")
    print()
    header = "n_ar_class | " + " | ".join(
        f"pc={pc:.0%} pr={pr:.0%}" for pc in P_C for pr in P_R)
    print(header)
    print("-" * len(header))
    for n in N_VALUES:
        row = [b1_probability(n, pc, pr) for pc in P_C for pr in P_R]
        print(f"{n:>9} | " + " | ".join(f"{p:.2f}" for p in row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
