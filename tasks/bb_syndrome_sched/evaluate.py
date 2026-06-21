"""
ShinkaEvolve evaluation harness for BB-code syndrome-measurement schedules.

This is the ORACLE and it is NOT evolved. It takes the candidate schedule (ticks) returned by
the evolved `run_experiment` in initial.py and scores it. Design principle:

  * mechanical / tradeoff-free constraints (completeness, non-conflict packing) -> checked here
    cheaply and exactly. Completeness already guarantees every stabilizer is measured, and a
    noiseless-determinism sanity check confirms it;
  * the constraint that IS the search problem once interleaving is allowed -- HOOK-ERROR SHAPING
    (the CNOT order decides where a mid-circuit ancilla fault lands) -- is captured by two Stim
    measurements: the circuit-distance floor (a hard gate) and, above it, the logical error rate
    itself (the score). A bad interleaving does NOT fail "correctness" (a complete schedule always
    measures the code in the noiseless sense); it raises the LER and may drop the distance.

SCORING (baseline-relative so the IBM seed anchors at 0):
  * VALID schedule  -> combined_score = log10(SEED_LER / overall_LER)
        seed (IBM) ~0   |   AlphaSyndrome's published schedule ~+0.15   |   beating both -> larger.
        (The ~+0.15 anchor was measured under THIS harness -- the IBM schedule re-encoded into the
        generic evaluate_circuit, vs AlphaSyndrome's published bbcode-72 schedule -- and matches the
        paper's ~44% BP-OSD LER reduction on [[72,12,6]]; see README "Verification".)
  * INVALID schedule -> combined_score = INVALID_SCORE (-2.0), below ANY valid schedule, plus a
        text_feedback string naming exactly which rule it broke. validate_fn returns (True, None) so
        these stay in the archive as negative examples (they teach the LLM) instead of being discarded.
  overall_LER = 1 - (1 - p_X)(1 - p_Z), estimated with Stim + BP-OSD under IBM Brisbane noise
  (the same model AlphaSyndrome used). The circuit build is a faithful re-implementation of
  asyndrome.scheduler.evaluate_circuit (MPP-bracketed single round, ancilla-only depolarizing),
  so it does not import the asyndrome package.

RUNTIME (measured): on bbcode-72 at NSHOTS=50000 one full evaluate is ~34 s, essentially all of
it the two BP-OSD decodes (~34 s); structural+sanity+distance checks are <0.5 s combined. Cost is
~linear in NSHOTS (~14 s at 20k, ~68 s at 100k) and the larger bbcode-288 is several x slower. Shinka
parallelizes evals (run_workers / async proposals), so wall-clock scales down with workers.

NOISE: at LER ~1e-2 the score std is ~0.43/sqrt(LER*NSHOTS): ~0.03 at 20k, ~0.02 at 50k, ~0.015
at 100k. Since beating IBM means ~0.1-point gains, 50k gives ~3:1 signal:noise; raise NSHOTS (or
num_runs) if selection looks noisy. A fresh random sampling seed is drawn per eval so the search
cannot lock onto one lucky noise realization (see _ler_one).

Deps: stim, numpy, stimbposd  (pip install stim numpy stimbposd ldpc).
"""

import json
import math
import os
import numpy as np
import stim
from stimbposd import BPOSD

from shinka.core import run_shinka_eval


# --- Brisbane noise model (depolarizing on ancillas per tick; values from asyndrome) ---
CNOT_P = 0.007432674432642006
IDLE_P = 0.005243978963702009

NSHOTS = 50000          # shots for the LER estimate (see RUNTIME / NOISE above)
OSD_ORDER = 3           # BP-OSD combination-sweep order. asyndrome left osd_order commented out, so
                        # its decoder ran at the stimbposd DEFAULT (60, osd_cs); 3 is a deliberate
                        # faster-but-weaker choice. It still reproduces the paper's ~44% BP-OSD LER
                        # reduction on bbcode-72, and SEED_LER below is calibrated to THIS decoder, so
                        # the relative score is self-consistent (absolute LERs are not paper-comparable).
INVALID_SCORE = -2.0    # score for any invalid schedule; strictly below any valid score
                        # (a valid schedule's worst possible score is > -2)

# Calibrated logical error rate of the IBM seed schedule under THIS harness + Brisbane noise.
# The score is log10(SEED_LER / overall_LER), so the seed lands at ~0. THIS CONSTANT IS SPECIFIC
# TO THE CODE + NOISE MODEL. For bbcode-72 it was VERIFIED at ~1.05e-2 (measured 1.04e-2 at 50k
# shots; see README "Verification"). If you switch get_kwargs to the other shipped code (bbcode-288),
# recalibrate: run the unmutated initial.py schedule through aggregate_fn once with absolute scoring
# and set SEED_LER to that overall_LER.
SEED_LER = 1.05e-2


def distance_floor(code):
    # Circuit distance of a GOOD schedule under this single-round/hook-error harness is typically
    # d-1 (verified: both IBM and AlphaSyndrome schedules give d-1 on bbcode-72). Floor at d-1 to
    # catch catastrophic interleavings without false-rejecting normal schedules.
    return code["d"] - 1


# ---------------------------------------------------------------------------
# Circuit construction (faithful copy of asyndrome.scheduler.evaluate_circuit).
# The observable is fixed here from the code's JSON logicals (NOT from the evolved schedule), so
# the schedule cannot redefine, weaken, or trivialize what is being measured -- it can only
# reorder the syndrome-extraction CNOTs in between the two ideal logical measurements.
# ---------------------------------------------------------------------------
def _build_circuit(code, ticks, stabilizers, logicals):
    n = code["n"]
    nanc = len(code["x_stabilizers"]) + len(code["z_stabilizers"])
    c = stim.Circuit()
    nmeas = 0

    def mpp(pauli_string):
        nonlocal nmeas
        c.append("MPP", stim.target_combined_paulis(stim.PauliString(pauli_string)))
        nmeas += 1
        return nmeas - 1

    first_stab = [mpp(s) for s in stabilizers]
    first_log = [mpp(s) for s in logicals]

    for tick in ticks:
        idle = [True] * nanc
        for (data, anc, pauli) in tick:
            idle[anc - n] = False
            if pauli == "X":
                c.append("H", anc)
                c.append("CNOT", [anc, data])
                c.append("H", anc)
            else:
                c.append("CNOT", [data, anc])
        for i, is_idle in enumerate(idle):
            c.append("DEPOLARIZE1", i + n, IDLE_P if is_idle else CNOT_P)

    c.append("MZ", [a + n for a in range(nanc)])
    nmeas += nanc

    second_stab = [mpp(s) for s in stabilizers]
    second_log = [mpp(s) for s in logicals]

    for i, (a, b) in enumerate(zip(first_stab, second_stab)):
        c.append("DETECTOR", [stim.target_rec(a - nmeas), stim.target_rec(b - nmeas)], i)
    for i, (a, b) in enumerate(zip(first_log, second_log)):
        c.append("OBSERVABLE_INCLUDE", [stim.target_rec(a - nmeas), stim.target_rec(b - nmeas)], i)
    return c


# ---------------------------------------------------------------------------
# Validity checks.
# ---------------------------------------------------------------------------
def _expected_checks(code):
    """The exact multiset of (data, ancilla, pauli) checks implied by the code's stabilizers.
    Ancilla numbering: X-ancillas n..n+nx-1, Z-ancillas n+nx..n+nx+nz-1 (matches the BB JSON and
    initial.py's xanc/zanc). Pinning this set means the schedule may reorder checks in time but
    cannot drop, duplicate, add, or rewire them."""
    n = code["n"]
    nx = len(code["x_stabilizers"])
    expected = set()
    for idx, s in enumerate(code["x_stabilizers"]):
        for data, p in enumerate(s):
            if p == "X":
                expected.add((data, n + idx, "X"))
    for idx, s in enumerate(code["z_stabilizers"]):
        for data, p in enumerate(s):
            if p == "Z":
                expected.add((data, n + nx + idx, "Z"))
    return expected


def _structural_check(code, ticks):
    """Completeness + non-conflict + well-formedness. Returns (ok, reason)."""
    n = code["n"]
    nanc = len(code["x_stabilizers"]) + len(code["z_stabilizers"])

    seen = []
    for t, tick in enumerate(ticks):
        datas, ancs = [], []
        for item in tick:
            # well-formedness / range (defensive: prevents out-of-range qubits or bad tuples)
            if len(item) != 3:
                return False, f"tick {t} has a malformed check entry {item}"
            data, anc, p = item
            if not (isinstance(data, int) and isinstance(anc, int)):
                return False, f"tick {t} check {item} has non-integer qubit index"
            if p not in ("X", "Z"):
                return False, f"tick {t} check {item} has pauli {p!r}, must be 'X' or 'Z'"
            if not (0 <= data < n):
                return False, f"tick {t} data qubit {data} out of range [0,{n})"
            if not (n <= anc < n + nanc):
                return False, f"tick {t} ancilla {anc} out of range [{n},{n+nanc})"
            seen.append((data, anc, p))
            datas.append(data)
            ancs.append(anc)
        if len(set(datas)) != len(datas) or len(set(ancs)) != len(ancs):
            return False, f"tick {t} uses a qubit in two gates at once (non-conflict violated)"

    seen_set = set(seen)
    if len(seen) != len(seen_set):
        return False, "a Pauli check is scheduled more than once"
    expected = _expected_checks(code)
    missing = expected - seen_set
    extra = seen_set - expected
    if missing:
        return False, (f"{len(missing)} Pauli check(s) missing, e.g. {sorted(missing)[0]} "
                       f"(a stabilizer is not fully measured / a check was dropped or rewired)")
    if extra:
        return False, (f"{len(extra)} check(s) do not belong to any stabilizer, e.g. "
                       f"{sorted(extra)[0]} (wrong data qubit, ancilla, or pauli)")
    return True, ""


def _measures_correctly(circuit):
    """Noiseless-determinism SANITY check: a valid syndrome-extraction circuit has deterministic
    detectors and no logical flip with no noise. NOTE: for a structurally COMPLETE schedule (gate 1)
    this passes regardless of CNOT order -- each ancilla accumulates exactly its own stabilizer and
    is measured out, so the codespace is undisturbed. So this is a belt-and-suspenders guard against
    pathological / rewired circuits that a completeness check might miss; it does NOT distinguish
    good from bad interleavings (the distance floor + the LER do that). Stim ground truth, unfakeable."""
    nl = circuit.without_noise()
    det, obs = nl.compile_detector_sampler().sample(128, separate_observables=True)
    return (not det.any()) and (not obs.any())


def _circuit_distance(circuit, d):
    """Best-effort circuit distance via Stim graphlike search. Returns int, or None if it can't
    be computed. (Graphlike-only; for BB it can miss pure-hyperedge mechanisms, but those would
    raise the LER anyway, so a missed reduction cannot inflate the score.)"""
    try:
        errs = circuit.search_for_undetectable_logical_errors(
            dont_explore_detection_event_sets_with_size_above=d + 2,
            dont_explore_edges_with_degree_above=d + 2,
            dont_explore_edges_increasing_symptom_degree=True,
        )
        return len(errs)
    except Exception:
        return None


def _ler_one(circuit, n, nshots, seed):
    dem = circuit.detector_error_model(decompose_errors=True, ignore_decomposition_failures=True)
    det, obs = circuit.compile_detector_sampler(seed=seed).sample(nshots, separate_observables=True)
    pred = BPOSD(dem, max_bp_iters=n, osd_order=OSD_ORDER).decode_batch(det)
    return int(np.sum(np.any(pred != obs, axis=1)))


# ---------------------------------------------------------------------------
# Shinka hooks.
# ---------------------------------------------------------------------------
# Resolve the code JSON relative to THIS evaluator (which always lives in the task dir), so the
# path is robust to whatever CWD the harness launches the eval subprocess from. The evolved
# initial.py receives this absolute path via run_experiment(**get_kwargs(...)) and just opens it.
_TASK_DIR = os.path.dirname(os.path.abspath(__file__))


def get_kwargs(run_idx: int) -> dict:
    # The code to schedule. Iterate on bbcode-72 (fast, [[72,12,6]]). The other shipped, verified
    # code is bbcode-288 ([[288,12,18]], slower) -- switching to it requires recalibrating SEED_LER
    # (see above). Only these two (ell == m) are shipped; see initial.py _BB_PARAMS for why.
    return {"code_path": os.path.join(_TASK_DIR, "qecc", "bbcode-72.json")}


def validate_fn(result):
    # run_shinka_eval unpacks this as (is_valid, error_msg), so it MUST return a 2-tuple.
    # "Valid" here only means the evolved program ran and returned a schedule (i.e. it did not
    # crash). Validity of the SCHEDULE itself is scored in aggregate (INVALID_SCORE + feedback) so
    # invalid candidates remain archived as learning signal rather than being thrown away.
    if isinstance(result, dict) and "ticks" in result:
        return True, None
    return False, "run_experiment did not return a dict containing 'ticks'"


def _invalid(reason, depth=None):
    return {
        "combined_score": INVALID_SCORE,
        "public": {"valid": False, "depth": depth, "reason": reason},
        "text_feedback": f"INVALID (score {INVALID_SCORE}): {reason}.",
    }


def aggregate_fn(results: list) -> dict:
    res = results[0]
    code = json.load(open(res["code_path"]))
    n, d = code["n"], code["d"]
    # Coerce defensively: a malformed candidate (non-numeric index, wrong-arity tuple) becomes a
    # clean INVALID (-2.0 + feedback) instead of crashing the eval to the default 0.0 error metric.
    try:
        ticks = [[(int(da), int(an), str(p)) for (da, an, p) in tick] for tick in res["ticks"]]
    except (TypeError, ValueError) as e:
        return _invalid(f"malformed schedule -- each tick entry must be (data, ancilla, pauli): {e}")
    depth = len(ticks)

    # (1) structural: completeness + non-conflict + well-formedness
    ok, msg = _structural_check(code, ticks)
    if not ok:
        return _invalid(msg, depth)

    all_stabs = code["x_stabilizers"] + code["z_stabilizers"]
    # z_circuit observes the logical X operators (=> logical-X error rate); x_circuit observes Z.
    z_circuit = _build_circuit(code, ticks, all_stabs, code["logical_xs"])
    x_circuit = _build_circuit(code, ticks, all_stabs, code["logical_zs"])

    # (2) noiseless-determinism sanity check (see _measures_correctly: passes for any complete
    # schedule; a failure here means a pathological circuit, not merely a sub-optimal interleaving).
    if not (_measures_correctly(z_circuit) and _measures_correctly(x_circuit)):
        return _invalid(
            "schedule produces a non-deterministic noiseless detector/observable (pathological "
            "syndrome-extraction circuit -- this should not happen for a structurally complete schedule)",
            depth,
        )

    # (3) distance guard (best-effort; floor d-1). Check BOTH circuits and take the min, matching
    # asyndrome.scheduler.Schedule.distance: z_circuit (observes logical-X) and x_circuit (observes
    # logical-Z) have distinct undetectable-error structure, so a one-sided check would miss an
    # X-side (or Z-side) distance reduction whose LER signature stays under sampling resolution.
    zd = _circuit_distance(z_circuit, d)
    xd = _circuit_distance(x_circuit, d)
    floor = distance_floor(code)
    _both = [v for v in (zd, xd) if v is not None]
    dist = min(_both) if _both else None
    if dist is not None and dist < floor:
        return _invalid(f"reduces circuit distance to {dist} (must be >= {floor})", depth)

    # (4) logical error rate -> baseline-relative score. Fresh random seed per eval (anti-overfit).
    s = int(np.random.SeedSequence().generate_state(1)[0])
    zf = _ler_one(z_circuit, n, NSHOTS, s)
    xf = _ler_one(x_circuit, n, NSHOTS, s ^ 0x9E3779B9)
    zrate, xrate = zf / NSHOTS, xf / NSHOTS
    overall = 1.0 - (1.0 - xrate) * (1.0 - zrate)

    if overall <= 0.0:
        # 0 logical errors observed: genuinely strong but under-resolved. Cap and ask for shots.
        score = math.log10(SEED_LER * NSHOTS)
        fb = (f"valid; depth={depth}, distance={dist}. 0 logical errors in {NSHOTS} shots "
              f"(LER < {1.0/NSHOTS:.1e}); raise NSHOTS to resolve. score capped ~={score:.3f}")
        return {"combined_score": float(score),
                "public": {"valid": True, "depth": depth, "distance": dist, "overall_ler": 0.0,
                           "x_ler": xrate, "z_ler": zrate},
                "text_feedback": fb}

    score = math.log10(SEED_LER / overall)
    fb = (f"valid; depth={depth}, distance={dist}, overall_LER={overall:.3e} "
          f"(X={xrate:.2e}, Z={zrate:.2e}); score=log10(seed/LER)={score:+.3f} "
          f"(seed IBM=0.00, AlphaSyndrome~=+0.15). Lower depth and better hook ordering both help.")
    return {
        "combined_score": float(score),
        "public": {"valid": True, "depth": depth, "distance": dist,
                   "overall_ler": overall, "x_ler": xrate, "z_ler": zrate},
        "private": {"shots": NSHOTS, "seed_ler": SEED_LER},
        "text_feedback": fb,
    }


def main(program_path: str, results_dir: str):
    metrics, correct, error_msg = run_shinka_eval(
        program_path=program_path,
        results_dir=results_dir,
        experiment_fn_name="run_experiment",
        num_runs=1,
        get_experiment_kwargs=get_kwargs,
        validate_fn=validate_fn,
        aggregate_metrics_fn=aggregate_fn,
    )
    return metrics, correct, error_msg


if __name__ == "__main__":
    import argparse

    # The Shinka harness invokes this as: evaluate.py --program_path <prog> --results_dir <dir>
    # (see shinka/launch/scheduler.py _build_command). These MUST be named flags, not positional.
    ap = argparse.ArgumentParser(description="bb_syndrome_sched evaluator")
    ap.add_argument("--program_path", type=str, default="initial.py")
    ap.add_argument("--results_dir", type=str, required=True)
    args = ap.parse_args()
    main(args.program_path, args.results_dir)
