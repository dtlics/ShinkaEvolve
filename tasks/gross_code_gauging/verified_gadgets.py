"""ILP-certified gauging gadgets for the weight-12 logical X_alpha of the
[[144,12,12]] gross code — the verified frontier, 2026-07-18.

Every CERTIFIED entry below has PROVEN deformed-code X-distance 12: all 11
Z-logical witnesses solved to proven optimality by the Landahl-Anderson-Rice
ILP (qcode_eval.distance_milp.ilp_min_weight, HiGHS backend — the method
Bravyi et al. cite; the same standard WY App. B used via CPLEX for their
22-edge / 41-element reference). The Z-side distance is automatic >= 12
(Cross et al. arXiv:2407.18393 Lemma 10: the deformed code's Z-type
normalizer operators are the original code's) and was spot-checked.

Discovery provenance: the E=20 base graph was found by LLM-driven evolution
(run spectral_v1, task gross_code_spectral_synth) under a spectral-proxy
objective; the E=19/E=17 descendants by attack-guided prune-descent from it,
ILP-certified per level. HISTORICAL NOTE on the proxy: the spectral
certificate lambda_2 >= 2 turned out to point AWAY from the frontier — the
certified E=17 graph has lambda_2 ~ 1.2, and the hand-crafted WY graph holds
d=12 at lambda_2 = 0.925 — while the BP+OSD dressed-logical attack (any
number of trials tested) is blind to some weight-11 operators, so
attack-only acceptance below E=18 produced two refuted claims (E=18/Q=33
independent-SA graph and E=14/Q=27 endpoint, both proven d' <= 11). ILP is
the only trusted certifier for claims; the attack remains the right cheap
in-loop screen.

Q accounting: Q = E edge qubits + 12 A_v + B_p (after BB-redundancy
reduction), matching evaluate.py's build_gauged. The certified frontier,
with the floor BRACKETED by ILP on the same descent chain:

    Q=41  WY hand-crafted reference (22 edges)      [paper IP proof]
    Q=40  K_{2,10} two-hub (20 edges)               [proven here; degenerate]
    Q=38  RECORD_E20 (20 edges)                     [proven here]
    Q=36  CHAIN_E19  (19 edges)                     [proven here]
    Q=33  CHAIN_E17  (17 edges)                     [proven here]
    Q=30  CHAIN_E16  (16 edges)                     [proven here — THE RECORD]
    ----- floor bracket -----
    Q=28  CHAIN_E15  (15 edges)                     [REFUTED: d' <= 11]
    Q=27  E=14 endpoint                             [REFUTED: d' <= 11]

Measured v5 physics (gate point, R=12; margin = decades of LER better than
the WY reference curve, sampling sigma ~0.06 decades) — every certified
gadget is also measured-FEASIBLE:

    CHAIN_E16/Q=30  score +11.23  LER 4.67e-2  margin +0.08  depths 6/10
    CHAIN_E17/Q=33  score  +8.34  LER 4.29e-2  margin +0.11  depths 6/9
    CHAIN_E19/Q=36  score  +5.00  LER 5.89e-2  margin -0.03  depths 6/9
    RECORD_E20/Q=38 score  +3.33  LER 4.33e-2  margin +0.11  depths 6/9

Certification is CODE distance; the evaluator additionally prices schedule
depth / routing (sparser graphs route longer — E=16 needs Z-depth 10), so
smallest-Q is not automatically lowest-LER — quote measured numbers with
the certification, not instead of it.
"""

# The evolved record (run spectral_v1; degrees 3^8 4^4, integral Laplacian
# spectrum {0, 2^5, 4^3, 6^3}, lambda_2 = 2 exactly). Q=38. PROVEN d'_X=12.
RECORD_E20 = [
    (0, 1), (0, 8), (0, 11), (1, 2), (1, 5), (2, 3), (2, 6), (2, 10),
    (3, 4), (3, 11), (4, 5), (4, 8), (5, 6), (5, 9), (6, 7), (7, 8),
    (7, 11), (8, 9), (9, 10), (10, 11),
]

# Prune-descent chain from RECORD_E20 (removal order); each listed level
# ILP-certified independently. The next removal, (3, 11) -> E=15/Q=28, is
# REFUTED (d' <= 11): the chain floor is exactly bracketed at E=16.
CHAIN_REMOVALS = [(0, 8), (1, 2), (2, 3), (2, 6)]

# Q=36. PROVEN d'_X=12.
CHAIN_E19 = sorted(e for e in RECORD_E20 if e not in {(0, 8)})

# Q=33. PROVEN d'_X=12. lambda_2 ~ 1.2 (far below the spectral
# certificate — expansion was never necessary).
CHAIN_E17 = sorted(e for e in RECORD_E20 if e not in set(CHAIN_REMOVALS[:3]))

# Q=30 — the CERTIFIED RECORD (11 elements below the WY 41, ~27% saved).
# PROVEN d'_X=12 at 16 edges + 12 A_v + 2 B_p.
CHAIN_E16 = sorted(e for e in RECORD_E20 if e not in set(CHAIN_REMOVALS))

# Q=40. PROVEN d'_X=12. Maximally degenerate certified graph (vertex
# connectivity 2, two degree-10 hubs) — the control showing certification
# alone does not imply a GOOD gadget.
K2_10 = [(h, s) for h in (0, 1) for s in range(2, 12)]

# REFUTED claims (kept so nobody re-believes them): proven d'_X <= 11.
REFUTED_SA_E18_Q33 = [
    (0, 8), (0, 11), (1, 2), (1, 8), (2, 3), (2, 10), (3, 4), (3, 5),
    (3, 11), (4, 5), (4, 7), (4, 8), (5, 6), (5, 7), (5, 9), (6, 7),
    (7, 8), (9, 10),
]
REFUTED_ENDPOINT_E14_Q27 = [
    (0, 11), (1, 5), (2, 10), (3, 4), (4, 5), (4, 8), (5, 6), (5, 9),
    (6, 7), (7, 8), (7, 11), (8, 9), (9, 10), (10, 11),
]
REFUTED_CHAIN_E15_Q28 = sorted(
    e for e in RECORD_E20 if e not in set(CHAIN_REMOVALS) | {(3, 11)})

CERTIFIED = {
    "RECORD_E20": {"edges": RECORD_E20, "Q": 38, "d_proven": 12},
    "CHAIN_E19": {"edges": CHAIN_E19, "Q": 36, "d_proven": 12},
    "CHAIN_E17": {"edges": CHAIN_E17, "Q": 33, "d_proven": 12},
    "CHAIN_E16": {"edges": CHAIN_E16, "Q": 30, "d_proven": 12},
    "K2_10": {"edges": K2_10, "Q": 40, "d_proven": 12},
}
