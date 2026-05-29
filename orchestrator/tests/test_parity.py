"""test_parity.py — assert the ported strategy policies match shinka's math.

Each mutable cell-A file ports a shinka policy. These tests pin the port to the
reference so a future edit (or a confused rewrite-then-restore) can't silently
drift from Shinka's behavior:

  * sample_parent  -> WeightedSamplingStrategy probability formula
                      (shinka.database.parents.stable_sigmoid + median/MAD/h_i)
  * novelty_check  -> direct cosine-similarity + threshold decision
  * select_llm     -> faithfully exposes AsymmetricUCB's posterior after updates
  * island_policy  -> ProgramDatabase.is_stagnant spawn rule

Run:  pytest orchestrator/tests/test_parity.py
      python orchestrator/tests/test_parity.py
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ORCH = _HERE.parent
_REPO_ROOT = _ORCH.parent
for _p in (str(_REPO_ROOT), str(_ORCH / "scripts"), str(_ORCH / "harness")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import archive_record  # noqa: E402
import archive_query  # noqa: E402
import sample_parent  # noqa: E402
import novelty_check  # noqa: E402
import select_llm  # noqa: E402
import island_policy  # noqa: E402


def _seed_archive(db_path, db_config, specs):
    """specs: list of (score, embedding|None). Records each as a correct program."""
    for i, (score, emb) in enumerate(specs):
        prog = {
            "code": f"# EVOLVE-BLOCK-START\nx={i}\n# EVOLVE-BLOCK-END\n",
            "generation": i,
            "combined_score": score,
            "correct": True,
            "public_metrics": {},
            "private_metrics": {},
        }
        if emb is not None:
            prog["embedding"] = emb
        archive_record.main({"db_path": db_path, "db_config": db_config, "program": prog})


def test_sample_parent_weighted_parity():
    from shinka.database.parents import stable_sigmoid

    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "programs.sqlite")
        db_config = {"num_islands": 1, "archive_size": 20, "parent_selection_lambda": 10.0}
        _seed_archive(db_path, db_config, [(1.0, None), (2.0, None), (1.5, None)])

        out = sample_parent.main({"db_path": db_path, "db_config": db_config, "seed": 0})
        probs = out["selection_probs"]

        # Reference: replicate sample_parent's pool read (get_all_programs order,
        # in_archive + correct) and recompute with shinka's stable_sigmoid.
        progs = archive_query.main(
            {"db_path": db_path, "db_config": db_config, "query_type": "all"}
        )["result"]
        pool = [p for p in progs if p.get("in_archive") and p.get("correct")]
        scores = [p["combined_score"] for p in pool]
        children = [p.get("children_count", 0) or 0 for p in pool]
        s = sorted(scores)
        n = len(s)
        alpha_0 = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0
        devs = sorted(abs(x - alpha_0) for x in scores)
        mad = devs[n // 2] if n % 2 else (devs[n // 2 - 1] + devs[n // 2]) / 2.0
        scale = max(mad, 1e-6)
        w = [stable_sigmoid(10.0 * (a - alpha_0) / scale) * (1.0 / (1.0 + c))
             for a, c in zip(scores, children)]
        tot = sum(w)
        ref = [x / tot for x in w]

        assert len(probs) == len(ref), (len(probs), len(ref))
        assert all(abs(a - b) < 1e-9 for a, b in zip(probs, ref)), (probs, ref)
        return None


def test_novelty_check_cosine_parity():
    import numpy as np

    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "programs.sqlite")
        db_config = {"num_islands": 1, "archive_size": 20}
        embs = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.6, 0.8, 0.0]]
        _seed_archive(db_path, db_config, [(1.0, embs[0]), (1.1, embs[1]), (1.2, embs[2])])

        cand = [0.9, 0.1, 0.0]

        def cos(a, b):
            a, b = np.asarray(a), np.asarray(b)
            return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))

        ref_max = max(cos(cand, e) for e in embs)

        out = novelty_check.main(
            {"db_path": db_path, "db_config": db_config,
             "candidate_embedding": cand, "code_embed_sim_threshold": 0.99}
        )
        assert abs(out["max_similarity"] - ref_max) < 1e-9, (out["max_similarity"], ref_max)
        assert out["accept"] == (ref_max < 0.99)
        # And a near-duplicate is rejected.
        dup = novelty_check.main(
            {"db_path": db_path, "db_config": db_config,
             "candidate_embedding": embs[0], "code_embed_sim_threshold": 0.99}
        )
        assert dup["accept"] is False and dup["max_similarity"] > 0.999
        return None


def test_select_llm_bandit_parity():
    from shinka.llm import AsymmetricUCB
    import numpy as np

    models = ["m1", "m2", "m3"]
    updates = [("m1", 1.0), ("m2", 0.2), ("m1", 0.9)]

    # Reference bandit directly.
    np.random.seed(0)
    ref = AsymmetricUCB(arm_names=list(models))
    for arm, r in updates:
        ref.update(arm=arm, reward=r, baseline=0.0)
    _, ref_probs = ref.select_llm()
    ref_probs = list(np.asarray(ref_probs, dtype=float))

    # Via select_llm wrapper with a persisted state file.
    with tempfile.TemporaryDirectory() as td:
        state = os.path.join(td, "bandit.pkl")
        for arm, r in updates:
            select_llm.main({"mode": "update", "models": models, "state_path": state,
                             "arm": arm, "reward": r, "baseline": 0.0})
        np.random.seed(0)
        out = select_llm.main({"mode": "select", "models": models, "state_path": state})
        probs = list(np.asarray(out["probs"], dtype=float))

    assert len(probs) == len(ref_probs)
    assert all(abs(a - b) < 1e-9 for a, b in zip(probs, ref_probs)), (probs, ref_probs)
    return None


def test_island_policy_stagnation_parity():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "programs.sqlite")
        # best at gen 0, latest at gen 5 -> gens_since_best = 5
        db_config = {"num_islands": 1, "archive_size": 20,
                     "enable_dynamic_islands": True, "stagnation_threshold": 3}
        _seed_archive(db_path, db_config,
                      [(5.0, None), (1.0, None), (1.0, None), (1.0, None), (1.0, None), (1.0, None)])
        out = island_policy.main({"db_path": db_path, "db_config": db_config})
        # is_stagnant: enable AND gens_since_best >= threshold
        ref_spawn = True and out["gens_since_best"] >= 3
        assert out["actions"]["spawn"] == ref_spawn, out
        assert out["gens_since_best"] == 5, out["gens_since_best"]

        # Below threshold -> no spawn.
        db_config2 = dict(db_config, stagnation_threshold=100)
        out2 = island_policy.main({"db_path": db_path, "db_config": db_config2})
        assert out2["actions"]["spawn"] is False
        return None


if __name__ == "__main__":
    tests = [
        ("sample_parent weighted", test_sample_parent_weighted_parity),
        ("novelty_check cosine", test_novelty_check_cosine_parity),
        ("select_llm bandit", test_select_llm_bandit_parity),
        ("island_policy stagnation", test_island_policy_stagnation_parity),
    ]
    ok = True
    for name, fn in tests:
        try:
            fn()
            print(f"  [PASS] {name}")
        except Exception as exc:
            ok = False
            print(f"  [FAIL] {name}: {type(exc).__name__}: {exc}")
    print("ALL PARITY PASSED" if ok else "PARITY FAILURES")
    sys.exit(0 if ok else 1)
