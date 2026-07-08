"""Property test for the gross_code_gauging scheduler (König minimum edge
coloring) and the protocol-circuit determinism. Runnable standalone or under
pytest; NOT under the framework's orchestrator/tests testpath (it imports this
task's evaluate.py, which pulls stim/sinter/stimbposd).

    conda run -n shinka python tasks/gross_code_gauging/test_coloring.py

Checks:
  * min_edge_coloring is a PROPER edge coloring using EXACTLY Δ = max Tanner
    degree colors, on 400 random bipartite matrices + the real base/deformed
    Tanner matrices;
  * color_schedule ticks are qubit- and ancilla-disjoint with depth == Δ;
  * both protocol circuits (paper reference gadget) are noiseless-deterministic;
  * preview_gadget's structural fields match the evaluator's on the reference
    gadget AND on the matching-only (prune-all-expansion) gadget.
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
        "gauge_eval", os.path.join(_TASK_DIR, "evaluate.py"))
    ev = importlib.util.module_from_spec(spec); spec.loader.exec_module(ev)
    spec2 = importlib.util.spec_from_file_location(
        "gauge_init", os.path.join(_TASK_DIR, "initial.py"))
    ini = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(ini)
    return ev, ini


def _check_coloring(ev, H, label):
    H = np.asarray(H) % 2
    if H.sum() == 0:
        return 0
    coloring, delta = ev.min_edge_coloring(H)
    exp = int(max(H.sum(1).max(), H.sum(0).max()))
    assert delta == exp, (label, "delta", delta, exp)
    edges = {(r, int(q)) for r in range(H.shape[0]) for q in np.flatnonzero(H[r])}
    assert set(coloring) == edges, (label, "edge set")
    assert all(0 <= c < delta for c in coloring.values()), (label, "range")
    for keyf in (lambda e: e[0], lambda e: e[1]):     # proper on both sides
        seen = set()
        for e, c in coloring.items():
            k = (keyf(e), c)
            assert k not in seen, (label, "conflict", e)
            seen.add(k)
    ticks = ev.color_schedule(H, list(range(1000, 1000 + H.shape[0])))
    assert len(ticks) == delta, (label, "depth", len(ticks), delta)
    for tk in ticks:
        qs = [q for q, a in tk]; as_ = [a for q, a in tk]
        assert len(set(qs)) == len(qs) and len(set(as_)) == len(as_), (label, "tick conflict")
    return delta


def _matching_edges(ev):
    fpos = {t: i for i, t in enumerate(ev.F_TERMS)}
    conn = {((ci + cj) % ev.L, (di + dj) % ev.M)
            for ci, di in ev._BT for cj, dj in ev._B} - {(0, 0)}
    return sorted({(min(fpos[(a, b)], fpos[nb]), max(fpos[(a, b)], fpos[nb]))
                   for (a, b) in ev.F_TERMS for (cc, dd) in conn
                   for nb in [((a + cc) % ev.L, (b + dd) % ev.M)]
                   if nb in fpos and nb != (a, b)})


def test_coloring_and_preview():
    ev, ini = _import()
    rng = np.random.default_rng(0)
    for t in range(400):
        rows = int(rng.integers(1, 25)); cols = int(rng.integers(1, 25))
        H = (rng.random((rows, cols)) < rng.uniform(0.05, 0.8)).astype(np.int8)
        _check_coloring(ev, H, f"rand{t}")

    matching = _matching_edges(ev)
    pad = lambda H, E: np.concatenate([H, np.zeros((H.shape[0], E), np.int8)], 1)
    for extra, name in (([(2, 9), (2, 4), (9, 11), (10, 11)], "paper"), ([], "matching-only")):
        edges, dummies, rounds = ev.parse_spec({"edges": matching + extra, "rounds": 12})
        g = ev.build_gauged(edges, dummies)
        _check_coloring(ev, np.concatenate([pad(ev.HX0, g["E"]), g["Av"]], 0), name + "-X")
        _check_coloring(ev, np.concatenate([g["HZroute"], g["Bp"]], 0), name + "-Z")
        cx, meta = ev.build_protocol_circuit(g, rounds, "X", ev.P_GATE)
        cz, _ = ev.build_protocol_circuit(g, rounds, "Z", ev.P_GATE)
        assert ev._noiseless_ok(cx) and ev._noiseless_ok(cz), (name, "noiseless")
        # preview must agree with the evaluator's derived structural fields
        pv = ini.preview_gadget(matching + extra, rounds)
        assert pv["bp_checks"] == g["n_bp"], (name, "n_bp", pv["bp_checks"], g["n_bp"])
        assert pv["elements"] == g["overhead"], (name, "Q")
        assert pv["depth_x"] == meta["depth_def"][0], (name, "depth_x", pv["depth_x"], meta["depth_def"][0])
        assert pv["depth_z"] == meta["depth_def"][1], (name, "depth_z", pv["depth_z"], meta["depth_def"][1])
        assert pv["cycle_w_max"] == g["cycle_w_max"], (name, "cycle_w_max")
        assert pv["wz_max_est"] == g["wz_max"], (name, "wz_max", pv["wz_max_est"], g["wz_max"])
    print("OK: 400 random colorings proper + Delta-optimal; paper & matching-only "
          "gadgets schedule-consistent, noiseless-deterministic, preview matches evaluator")


if __name__ == "__main__":
    test_coloring_and_preview()
