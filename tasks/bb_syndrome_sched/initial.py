"""
ShinkaEvolve initial program: syndrome-measurement SCHEDULE for a Bivariate Bicycle (BB) code.

What evolves: `build_schedule(...)` below (inside the EVOLVE-BLOCK). It returns the full
tick assignment of the syndrome-extraction circuit as a list of ticks, where each tick is a
list of (data_qubit, ancilla_qubit, pauli) Pauli checks executed in that time step.

Why this representation: it can express ANY interleaving of X- and Z-checks (including
schedules with deliberate idles), so the search is not restricted to "X-block then Z-block".
The seed below is IBM's gross-code schedule (Bravyi et al. 2024), which interleaves X and Z.

What the evaluator enforces (see evaluate.py). A schedule is INVALID and scores a sentinel
(-2.0, well below the baseline) with text feedback if it:
  (1) is incomplete / malformed: every Pauli check of every stabilizer must appear exactly once,
      each with the correct ancilla and pauli (you may reorder checks in time but NOT rewire
      which ancilla measures which stabilizer), OR
  (2) double-uses a qubit in a tick (one 2-qubit gate per qubit per tick), OR
  (3) reduces the circuit distance below d-1 (Stim graphlike search on BOTH the X- and Z-observable
      circuits; the min must stay >= d-1).
(A complete, non-conflicting schedule ALWAYS measures the code correctly in the noiseless sense
regardless of CNOT order -- a Stim noiseless-determinism sanity check confirms this -- so there is no
separate "wrong ordering" rejection. The ORDER instead shapes HOOK ERRORS: it is rewarded, not gated,
through the logical error rate below, and a catastrophic ordering shows up as a distance drop in (3).)
A VALID schedule scores log10(LER_seed / LER), i.e. how much it beats the IBM seed:
  seed -> ~0, AlphaSyndrome's published schedule -> ~+0.15, beating both -> larger positive.
Objective: MINIMIZE the overall logical error rate (equivalently, maximize the score).

The evaluator is the oracle and reports *why* a schedule failed, so evolution can learn the rules.
The cheap mechanical packing (non-conflict) is also checked there; you do not need to satisfy
it by construction, but staying valid earns a real score instead of the -2.0 sentinel.

This file is specialized to BB codes (it uses the bivariate-bicycle Tanner-graph structure to
order each weight-6 check's six neighbors). `bb_neighbors` is fixed scaffolding and matches the
qubit/ancilla indexing of the shipped ell==m qecc/bbcode JSONs (bbcode-72, bbcode-288); do not
edit it. Only build_schedule inside the EVOLVE-BLOCK is mutated. (See the _BB_PARAMS note for why
only ell==m codes are shipped, and _check_labeling, which fails closed on a mismatched code.)
"""

import json
import numpy as np


# ---------------------------------------------------------------------------
# Fixed scaffolding (NOT evolved): BB-code Tanner-graph neighbor structure.
# Returns, for every X- and Z-check, the ordered list of its 6 data-qubit
# neighbors (in the fixed BB *block* convention: data qubits 0..n-1 = left block
# 0..m*l-1 then right block m*l..n-1; X-ancillas n..n+m*l-1; Z-ancillas
# n+m*l..n+2*m*l-1), plus each check's ancilla index. Params are the gross-code
# family (verbatim from asyndrome.bbcodeibm).
#
# IMPORTANT (labeling): this block convention coincides with the shipped
# qecc/bbcode-*.json data labeling ONLY for ell == m codes (bbcode-72, bbcode-288).
# For ell != m the published JSON uses a different data-qubit labeling, so the IBM
# block seed would not be valid against it -- run_experiment fails closed in that
# case (see _check_labeling). To add an ell != m code, regenerate its JSON in this
# block convention. Codes WITHOUT a shipped, verified JSON are commented out.
# ---------------------------------------------------------------------------
_BB_PARAMS = {
    #  n  : (ell, m, a1, a2, a3, b1, b2, b3)        # supported (JSON shipped + verified):
    72:  (6,  6,  3, 1, 2,  3, 1, 2),               # [[72,12,6]]   ell==m  (DEFAULT)
    288: (12, 12, 3, 2, 7,  3, 1, 2),               # [[288,12,18]] ell==m
    # ell != m -- JSON labeling differs from the block convention, not shipped:
    # 90:  (15, 3,  9, 1, 2,  0, 2, 7),             # [[90,8,10]]
    # 108: (9,  6,  3, 1, 2,  3, 1, 2),             # [[108,8,10]]
    # 144: (12, 6,  3, 1, 2,  3, 1, 2),             # [[144,12,12]] (the flagship gross code)
    # 784: (28, 14, 26, 6, 8, 7, 9, 20),            # [[784,24,24]] (no JSON in the artifact)
}


def bb_neighbors(code):
    n = code["n"]
    ell, m, a1, a2, a3, b1, b2, b3 = _BB_PARAMS[n]
    n2 = m * ell
    I_ell = np.identity(ell, dtype=int)
    I_m = np.identity(m, dtype=int)
    x = {i: np.kron(np.roll(I_ell, i, axis=1), I_m) for i in range(ell)}
    y = {i: np.kron(I_ell, np.roll(I_m, i, axis=1)) for i in range(m)}
    A1, A2, A3 = x[a1], y[a2], y[a3]
    B1, B2, B3 = y[b1], x[b2], x[b3]

    def nz(v):
        return int(np.nonzero(v)[0][0])

    def left(idx):
        return idx

    def right(idx):
        return idx + n2

    xnbs, znbs = [], []
    for i in range(n2):
        xnbs.append([
            left(nz(A1[i, :])), left(nz(A2[i, :])), left(nz(A3[i, :])),
            right(nz(B1[i, :])), right(nz(B2[i, :])), right(nz(B3[i, :])),
        ])
    for i in range(n2):
        znbs.append([
            left(nz(B1[:, i])), left(nz(B2[:, i])), left(nz(B3[:, i])),
            right(nz(A1[:, i])), right(nz(A2[:, i])), right(nz(A3[:, i])),
        ])
    xanc = [n + i for i in range(n2)]
    zanc = [n + n2 + i for i in range(n2)]
    return xnbs, znbs, xanc, zanc


# EVOLVE-BLOCK-START
def build_schedule(xnbs, znbs, xanc, zanc):
    """Build the syndrome-measurement schedule.

    Inputs (BB Tanner structure, already computed -- you have FULL visibility of which data
    qubits each check touches, so you can reason about conflicts and hook-error propagation):
      xnbs[i] : list of the 6 data-qubit indices that X-check i acts on, in a fixed order
      znbs[i] : list of the 6 data-qubit indices that Z-check i acts on, in a fixed order
      xanc[i] : ancilla index measuring X-check i      (use exactly these ancilla indices)
      zanc[i] : ancilla index measuring Z-check i

    Returns: list of ticks; each tick is a list of (data, ancilla, pauli) tuples, pauli in {"X","Z"}.
    Empty ticks are dropped. X- and Z-checks MAY share ticks (interleaving) -- that is the point.

    Hard constraints (the evaluator REJECTS the schedule with score -2.0 and tells you which one
    failed, so treat these as the rules of the game):
      * COMPLETE: every (data, ancilla, pauli) check implied by the code's stabilizers appears
        exactly once. Each check i must engage all of its neighbors xnbs[i] (or znbs[i]) exactly
        once, using ancilla xanc[i] (or zanc[i]). Do not invent, drop, duplicate, or rewire checks.
      * NON-CONFLICT: within a single tick, no data qubit and no ancilla is used twice (one gate
        per qubit per tick). Two checks that share a data qubit must engage it in DIFFERENT ticks.
      * DISTANCE: must not drop the circuit distance below d-1 (checked on BOTH the X- and
        Z-observable circuits). A catastrophic interleaving shows up here as a distance drop.
        (Completeness already guarantees the code is measured; CNOT ORDER is not a correctness
        gate -- it shapes hook errors, rewarded through the LER below.)

    Objective: MINIMIZE the logical error rate. Score = log10(LER_seed / LER); seed -> 0, beating
    the seed -> positive. Two levers trade off: (i) DEPTH -- fewer ticks means less idle noise on
    ancillas (this seed is shallow, 7 ticks); (ii) HOOK-ERROR SHAPING -- the relative order of the
    CNOTs decides where a mid-circuit ancilla fault lands, and a good order keeps residual errors
    away from logical operators and inside the decoder's reach. The published schedules each win
    on only one lever (IBM shallow-but-rigid, AlphaSyndrome deep-but-hook-optimal); a schedule that
    is both is how you beat both.

    The seed below is IBM's gross-code schedule: at each of 7 ticks every X-check engages one
    neighbor-direction (sX[t]) and every Z-check engages one (sZ[t]); "idle" = engage none this
    tick. It is UNIFORM across checks. The biggest source of improvement is to break that
    uniformity -- e.g. make a check's per-neighbor timing depend on its index i, or on which data
    qubit a neighbor is -- to shape hooks per check the way AlphaSyndrome does, while keeping the
    interleaving shallow. You have the full xnbs/znbs structure to do this; just keep the four
    hard constraints above satisfied.
    """
    # IBM gross-code schedule: neighbor-direction engaged at each tick (X and Z fire together).
    sX = ["idle", 1, 4, 3, 5, 0, 2]
    sZ = [3, 5, 0, 1, 2, 4, "idle"]

    n_ticks = max(len(sX), len(sZ))
    ticks = [[] for _ in range(n_ticks)]
    for t in range(len(sX)):
        d = sX[t]
        if d != "idle":
            for i in range(len(xnbs)):
                ticks[t].append((xnbs[i][d], xanc[i], "X"))
    for t in range(len(sZ)):
        d = sZ[t]
        if d != "idle":
            for i in range(len(znbs)):
                ticks[t].append((znbs[i][d], zanc[i], "Z"))

    return [tick for tick in ticks if tick]
# EVOLVE-BLOCK-END


def _check_labeling(code, xnbs, znbs, xanc, zanc):
    """Fail closed if bb_neighbors' BB block-convention labeling does not match this code's JSON
    stabilizer supports. They coincide only for ell == m codes (bbcode-72, bbcode-288); an ell != m
    code's published JSON uses a different data-qubit labeling, against which the IBM block seed
    would be silently invalid. Raising here turns that into a clear, early error."""
    n = code["n"]
    nx = len(code["x_stabilizers"])
    for i, s in enumerate(code["x_stabilizers"]):
        if {j for j, c in enumerate(s) if c == "X"} != set(xnbs[i]) or xanc[i] != n + i:
            raise ValueError(
                f"bb_neighbors block labeling does not match this code's JSON (X-check {i}). "
                f"The shipped JSON uses an incompatible data-qubit convention -- supported codes are "
                f"ell == m: bbcode-72, bbcode-288. To use another code, regenerate its JSON in the "
                f"BB block convention (see initial.py _BB_PARAMS note)."
            )
    for i, s in enumerate(code["z_stabilizers"]):
        if {j for j, c in enumerate(s) if c == "Z"} != set(znbs[i]) or zanc[i] != n + nx + i:
            raise ValueError(
                f"bb_neighbors block labeling does not match this code's JSON (Z-check {i}); "
                f"supported codes are ell == m: bbcode-72, bbcode-288."
            )


def run_experiment(code_path: str = "qecc/bbcode-72.json", **kwargs):
    """Entry point called by the Shinka evaluator. Returns the candidate schedule (ticks)
    plus the code path; evaluate.py builds the circuit, checks validity, and scores it."""
    code = json.load(open(code_path))
    xnbs, znbs, xanc, zanc = bb_neighbors(code)
    _check_labeling(code, xnbs, znbs, xanc, zanc)  # fail closed on an unsupported code's JSON
    ticks = build_schedule(xnbs, znbs, xanc, zanc)
    # tuples -> lists so the result serializes cleanly across the Shinka boundary
    ticks = [[[int(d), int(a), str(p)] for (d, a, p) in tick] for tick in ticks]
    return {"ticks": ticks, "code_path": code_path}
