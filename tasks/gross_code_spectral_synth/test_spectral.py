"""Regression + property tests for the v2 spectral evaluator. Runnable
standalone (conda run -n shinka python tasks/gross_code_spectral_synth/test_spectral.py)
or under pytest from this directory; NOT in the framework testpath.

The load-bearing test is the CERTIFIED-DOMINANCE fuzz property — the test
class that would have caught v1's star loophole (an uncertified E=11
port-star out-scoring the certified E=20 record):
  no valid uncertified graph may outscore ANY certified graph with E <= 24.
"""
import os
import sys

import numpy as np

_TASK_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(_TASK_DIR)))   # repo root
sys.path.insert(0, _TASK_DIR)


def _import():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "spectral_eval", os.path.join(_TASK_DIR, "evaluate.py"))
    ev = importlib.util.module_from_spec(spec); spec.loader.exec_module(ev)
    spec2 = importlib.util.spec_from_file_location(
        "spectral_init", os.path.join(_TASK_DIR, "initial.py"))
    ini = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(ini)
    return ev, ini


def _score(ev, edges):
    return ev.aggregate_fn([lambda: {"edges": [list(e) for e in edges]}])


def test_landmarks_and_star_regression():
    ev, ini = _import()

    # The seed IS the verified record: certified E=20 -> +10 - tiebreaks.
    r = _score(ev, ini.RECORD_E20)
    p = r["public"]
    assert p["certified"] == 1 and p["lam2"] >= 2.0 - 1e-6, p
    assert 9.7 <= r["combined_score"] <= 10.0, r["combined_score"]

    # v1's champion, the E=11 port-star: MUST be deeply negative now.
    star = [(0, i) for i in range(1, 12)]
    r = _score(ev, star)
    assert r["public"]["certified"] == 0
    assert abs(r["public"]["lam2"] - 1.0) < 1e-9      # K_{1,11}: lambda_2 = 1
    assert r["combined_score"] < -8.0, r["combined_score"]

    # ... and the E=12 dummy-hub star (hub = dummy label 12).
    dstar = [(12, i) for i in range(12)]
    r = _score(ev, dstar)
    assert r["public"]["certified"] == 0
    assert r["combined_score"] < -9.0, r["combined_score"]

    # WY hand-crafted 22-edge graph: uncertified, modest positive (~2.3).
    wy = list(ini.MATCHING_EDGES) + list(ini.PAPER_EXPANSION)
    r = _score(ev, wy)
    assert r["public"]["certified"] == 0
    assert 2.0 < r["combined_score"] < 2.5, r["combined_score"]

    # 12-cycle: uncertified, small positive, no leaves.
    ring = [(i, (i + 1) % 12) for i in range(12)]
    r = _score(ev, ring)
    assert r["public"]["leaves"] == 0
    assert 0.0 < r["combined_score"] < 1.0, r["combined_score"]

    # Parallel edges are invalid in v2 (simple-graph claim).
    r = _score(ev, ring + [ring[0]])
    assert r["public"]["valid"] == 0 and "parallel" in r["public"]["reason"]

    # Removing ANY single edge from the record drops lambda_2 below 2
    # (edge-minimality of the seed, cited in its docstring).
    for k in range(len(ini.RECORD_E20)):
        sub = [e for i, e in enumerate(ini.RECORD_E20) if i != k]
        lam = ini.graph_lambda2(sub)
        assert lam < 2.0 - 1e-9, (k, lam)

    # K_{2,10} two-hub: the known DEGENERATE certified E=20 (exploit-hunt
    # finding) — legitimately certified at 9.80, and it must stay strictly
    # below the genuine record via the congestion/degree tiebreaks.
    k210 = [(h, s) for h in (0, 1) for s in range(2, 12)]
    r = _score(ev, k210)
    assert r["public"]["certified"] == 1 and r["public"]["edges"] == 20
    assert abs(r["combined_score"] - 9.80) < 1e-6, r["combined_score"]
    assert r["combined_score"] < _score(ev, ini.RECORD_E20)["combined_score"]

    # Graph-level determinism: permuting the submitted edge list must not
    # move the score (canonical sort fix; exploit-hunt found +-0.02 drift).
    rng = np.random.default_rng(7)
    base = _score(ev, ini.RECORD_E20)["combined_score"]
    for _ in range(10):
        perm = [ini.RECORD_E20[i] for i in rng.permutation(len(ini.RECORD_E20))]
        perm = [(v, u) if rng.random() < 0.5 else (u, v) for (u, v) in perm]
        assert _score(ev, perm)["combined_score"] == base

    # Strict parsing: no silent coercion (exploit-hunt findings).
    for bad in ([(0.9, 1.7)], [(-0.5, 3)], [(0, 1, 5)], ["34"],
                [(0, 1), "23"]):
        r = _score(ev, bad + [(2, 3)])
        assert r["public"]["valid"] == 0, bad

    # Uncertified oversize penalty: E > 24 uncertified graphs are pushed
    # down so the lambda_2->2-from-below plateau cannot out-order oversized
    # certified graphs of the SAME size (exploit-hunt cliff finding).
    dense_unc = [(u, v) for u in range(12) for v in range(u + 1, 12)
                 if (u + v) % 3][:30]
    r = _score(ev, dense_unc)
    if r["public"]["valid"] and not r["public"]["certified"]:
        assert r["combined_score"] < 5.0 - 1.0 * (r["public"]["edges"] - 24) + 1e-9

    print("OK: landmarks (record +10, star < -8, dummy-star < -9, WY ~2.3, "
          "ring ~0.7, K_2,10 = 9.80 < record), parallel-edge invalid, record "
          "edge-minimal, graph-level determinism, strict parsing, oversize "
          "penalty")


def test_certified_dominance_fuzz():
    """PROPERTY: no valid uncertified graph outscores any certified graph
    with E <= E_REF. Fuzz random graphs of many shapes; also check the
    analytic bound: uncertified score < W_LAM*LAM2_MIN <= CERT_BASE."""
    ev, ini = _import()
    assert ev.W_LAM * ev.LAM2_MIN <= ev.CERT_BASE + 1e-9, \
        "analytic dominance bound violated by constants"

    rng = np.random.default_rng(0)
    worst_cert = float("inf")     # min certified score with E <= E_REF
    best_uncert = -float("inf")   # max uncertified score
    n_cert = n_uncert = 0
    for t in range(400):
        n_extra = int(rng.integers(0, 3))          # 0-2 dummies
        verts = list(range(12)) + [12 + i for i in range(n_extra)]
        E = int(rng.integers(11, 30))
        all_pairs = [(u, v) for i, u in enumerate(verts)
                     for v in verts[i + 1:]]
        rng.shuffle(all_pairs)
        edges = all_pairs[:E]
        r = _score(ev, edges)
        if not r["public"].get("valid"):
            continue
        s = r["combined_score"]
        if r["public"]["certified"] and r["public"]["edges"] <= ev.E_REF:
            worst_cert = min(worst_cert, s); n_cert += 1
        elif not r["public"]["certified"]:
            best_uncert = max(best_uncert, s); n_uncert += 1
    # seed the certified side with known certified graphs too
    for g in (ini.RECORD_E20,):
        s = _score(ev, g)["combined_score"]
        worst_cert = min(worst_cert, s); n_cert += 1
    assert n_cert >= 1 and n_uncert >= 50, (n_cert, n_uncert)
    assert best_uncert < worst_cert, (best_uncert, worst_cert)
    print(f"OK: certified-dominance fuzz ({n_cert} certified, {n_uncert} "
          f"uncertified samples; best uncertified {best_uncert:.2f} < worst "
          f"competitive certified {worst_cert:.2f})")


if __name__ == "__main__":
    test_landmarks_and_star_regression()
    test_certified_dominance_fuzz()
