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

RUNTIME (measured, osd_order=3, ~0.19 ms / decoded shot): a full evaluate samples each observable
circuit until it collects ~TARGET_ERRORS logical errors (or hits MAX_SHOTS) -- so it is VARIABLE:
~2.5 min for the seed and ~2.5-5 min for a SOTA-band schedule (a LOWER-LER schedule needs MORE shots
to reach the error target -> SLOWER; a worse one fewer -> faster). Worst case ~2*MAX_SHOTS decoded
shots (~5 min) on a near-perfect schedule. Set the harness eval_time generously (>= 00:08:00).
Lower TARGET_ERRORS (e.g. 1000) to ~halve eval time at the cost of a bit more noise.

NOISE (the point of the error-budget sampler): the score std is ~0.434/sqrt(F) with F = total
logical errors observed across the two circuits. Targeting a fixed error COUNT (F ~= 2*TARGET_ERRORS
= 4000 -> std ~0.0069) holds the noise ~CONSTANT across schedules of very different LER -- a fixed
shot count instead reads NOISIER on exactly the low-LER schedules we care about, so the greedy
archive-max "chases noise" (winner's curse: the reported best overstates the true best by
~std*sqrt(2 ln N_candidates)). With a constant ~0.007 std, selection can trust a new best. Raise
TARGET_ERRORS to tighten further (std ~ 1/sqrt(TARGET_ERRORS); eval cost ~ linear). A fresh random
seed is drawn per eval so the search cannot lock onto one lucky noise realization (see _ler_count).

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

# --- LER estimation: ERROR-BUDGET (sinter-style) sampling ------------------------------------
# We sample each observable circuit until it has collected TARGET_ERRORS logical errors (or hits
# MAX_SHOTS), instead of using a fixed shot count. This holds the SCORE'S SAMPLING NOISE ~CONSTANT
# across schedules: a lower-LER schedule yields fewer logical errors per shot, so a fixed shot count
# would be noisiest on exactly the good schedules we care about (and the greedy archive-max then
# "chases noise" — the winner's curse). With F = total logical errors observed across the two
# observable circuits, the score std is ~0.434/sqrt(F); TARGET_ERRORS=2000 PER circuit -> F~4000 ->
# std ~0.0069 (vs ~0.023 at the old fixed 50k shots). This mirrors how AlphaSyndrome estimates LER
# ("based on the number of logical flipping events"). MAX_SHOTS bounds eval wall-clock (a near-perfect
# schedule would otherwise sample forever); raise it (and MIN_SHOTS) to resolve a sub-1/MAX_SHOTS LER.
TARGET_ERRORS = 2000     # logical errors to collect PER observable circuit (x- and z-)
MIN_SHOTS     = 50_000   # never stop before this many shots (resolution floor)
MAX_SHOTS     = 800_000  # per-circuit shot ceiling (bounds eval wall-clock; ~2.5 min/circuit worst case)
BATCH_SHOTS   = 50_000   # sample + decode in batches of this size

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


def _ler_count(circuit, n, seed):
    """Adaptive ERROR-BUDGET LER estimate for ONE observable circuit. Builds the BP-OSD decoder
    once, then samples + decodes in batches of BATCH_SHOTS until TARGET_ERRORS logical errors have
    been observed (and at least MIN_SHOTS shots) or MAX_SHOTS is reached. Returns (n_logical_errors,
    n_shots). Each batch is FRESHLY seeded so the shots are independent; the decoder is deterministic
    given the syndromes, so reusing it across batches is correct. Targeting a fixed error COUNT (not
    a fixed shot count) holds the estimate's relative error ~1/sqrt(errors) ~constant across schedules
    of very different LER -- the property that lets selection trust a new 'best' instead of noise."""
    dem = circuit.detector_error_model(decompose_errors=True, ignore_decomposition_failures=True)
    decoder = BPOSD(dem, max_bp_iters=n, osd_order=OSD_ORDER)
    # Build the sampler ONCE; Stim's compiled sampler advances its PRNG across .sample() calls, so
    # successive batches are independent shots. ADAPTIVELY SIZE each batch: BP-OSD's decode_batch has
    # a meaningful per-CALL overhead (many small decodes are ~2x slower than one big one), so after a
    # probe batch we estimate the remaining shots needed and take the rest in ~1 more big batch -- this
    # hits the error target in ~2 decode calls with minimal over-sampling.
    sampler = circuit.compile_detector_sampler(seed=int(seed) & 0x7FFFFFFF)
    shots = 0
    fails = 0
    batch = BATCH_SHOTS
    while shots < MAX_SHOTS:
        b = int(min(batch, MAX_SHOTS - shots))
        det, obs = sampler.sample(b, separate_observables=True)
        pred = decoder.decode_batch(det)
        fails += int(np.sum(np.any(pred != obs, axis=1)))
        shots += b
        if shots >= MIN_SHOTS and fails >= TARGET_ERRORS:
            break
        if fails > 0:                          # size the next batch to land on TARGET_ERRORS
            need = (TARGET_ERRORS - fails) * shots / fails
            batch = max(int(need * 1.10), BATCH_SHOTS)
        else:                                  # no errors yet -> quadruple the probe
            batch = shots * 4
    return fails, shots


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
    # ERROR-BUDGET sampling: each circuit is sampled until it has collected ~TARGET_ERRORS logical
    # errors (or hits MAX_SHOTS), so the score's sampling noise is ~constant (~0.434/sqrt(total errors))
    # regardless of the schedule's LER -- the key change that lets selection trust a new best.
    s = int(np.random.SeedSequence().generate_state(1)[0])
    zf, z_shots = _ler_count(z_circuit, n, s)
    xf, x_shots = _ler_count(x_circuit, n, s ^ 0x9E3779B9)
    zrate, xrate = zf / z_shots, xf / x_shots
    overall = 1.0 - (1.0 - xrate) * (1.0 - zrate)
    n_err = zf + xf
    est_std = (0.434 / math.sqrt(n_err)) if n_err > 0 else None

    if overall <= 0.0:
        # 0 logical errors even at the shot ceiling: genuinely excellent but under-resolved.
        resolved = 1.0 / max(z_shots, x_shots)
        score = math.log10(SEED_LER / resolved)
        fb = (f"valid; depth={depth}, distance={dist}. 0 logical errors in {z_shots}+{x_shots} shots "
              f"(LER < {resolved:.1e}); raise MAX_SHOTS to resolve. score capped ~={score:.3f}")
        return {"combined_score": float(score),
                "public": {"valid": True, "depth": depth, "distance": dist, "overall_ler": 0.0,
                           "x_ler": xrate, "z_ler": zrate},
                "private": {"shots": [z_shots, x_shots], "errors": [zf, xf], "seed_ler": SEED_LER},
                "text_feedback": fb}

    score = math.log10(SEED_LER / overall)
    fb = (f"valid; depth={depth}, distance={dist}, overall_LER={overall:.3e} "
          f"(X={xrate:.2e}, Z={zrate:.2e}); score=log10(seed/LER)={score:+.3f} +-{est_std:.3f} "
          f"({n_err} logical errors over {z_shots}+{x_shots} shots; seed IBM=0.00, AlphaSyndrome~=+0.15). "
          f"Lower depth and better hook ordering both help.")
    return {
        "combined_score": float(score),
        "public": {"valid": True, "depth": depth, "distance": dist,
                   "overall_ler": overall, "x_ler": xrate, "z_ler": zrate},
        "private": {"shots": [z_shots, x_shots], "errors": [zf, xf], "score_std": est_std,
                    "seed_ler": SEED_LER},
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
