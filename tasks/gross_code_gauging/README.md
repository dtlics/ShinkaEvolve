# `gross_code_gauging` — gauging-measurement edge search on the gross code

ShinkaEvolve task: minimize the ancilla-qubit overhead of *gauging-measuring* the
weight-12 logical operator X̄_α on the **[[144, 12, 12]] gross code**, by choosing
the edges of a graph G on the operator's support. A faithful encoding of the
gross-code worked example in **Williamson & Yoder, "Low-overhead fault-tolerant
quantum computation by gauging logical operators"** ([arXiv:2410.02213](https://arxiv.org/abs/2410.02213),
Appendix B; *Nature Physics* **22**, 598, 2026).

## The problem in one paragraph

To measure X̄_α you "gauge" it: put a graph G on the 12 qubits in its support
(the *vertices*), add **one ancilla qubit per edge**, and deform the code —
new X-checks `A_v = X_v ∏_{e∋v} X_e` (one per vertex), and new Z-checks =
each original Z-check routed through the matching ancillas + a cycle-basis of
flux checks `B_p`. The deformed code must keep the **full distance 12**. The 18
mandatory *matching* edges (connect support monomials that share a Z-check) leave
the distance at only **8** — extra edges restore the graph's *expansion*. The
paper restores distance 12 with **4 extra edges → 22 ancilla qubits**, found by
random edge addition filtered with BP+OSD and certified with integer programming.
That is **not proven minimal**: fewer may be possible.

## What's optimized

`propose_extra_edges()` inside the EVOLVE-BLOCK of [initial.py](initial.py). It
returns a list of **extra edges** — unordered pairs `(u, v)` of the 12 fixed
`VERTICES`. The evaluator always adds the 18 base edges; each extra edge costs
exactly one ancilla qubit. Everything else (the code, the deformation, the
distance, the score) is computed by [evaluate.py](evaluate.py) and is never evolved.

## This is a graph-expansion problem, not edge enumeration

The paper's structural insight (and the reason it's a beautiful result): the
deformed-code distance is governed by the **expansion** of `G`. A low-weight
logical lives on a *sparse vertex cut*, so the distance is limited by `G`'s worst
cut, and the way to raise it is to **reinforce that cut**. The 18 base edges leave
`G` with a sharp bottleneck — its sparsest cut is crossed by only **2 edges**,
which is exactly why the base-only distance is 8 — and **all 4 of the paper's edges
cross that bottleneck**. (The `y⁰`/`y³` monomial blocks are *not* the bottleneck;
the base graph already connects them with 12 of its 18 edges.)

So the task is set up to be *about that structure*: [initial.py](initial.py)
exposes graph primitives the evolved function can call —
`sparsest_cut(extra)`, `fiedler_value(extra)`, `vertex_degrees(extra)`,
`graph_adjacency(extra)` — and the evaluator's `text_feedback` reports, every step,
the current graph's weakest cut, its Fiedler value (expansion), and — on a failure —
which vertices the limiting low-weight logical sits on (where the distance is
pinched). **Conductance is only a proxy** for the true quantum distance (which
depends on the specific logical operators), so beating the paper means being subtler
than max-conductance greedy — which is the genuinely open part.

## Score

```
malformed edge list / crash (correct=False)  ->  -1000
valid build, deformed distance d < 12        ->  -100 + d        (≈ -92 … -89)
valid build, deformed distance d == 12       ->  BASELINE_QUBITS - qubits   (= 24 - qubits)
```

A distance-12 graph at 24 qubits scores **0**; the paper's 22 qubits → **+2**;
every further qubit saved is **+1**. Any distance-12 result outranks any distance
failure, which outranks a malformed graph. Minimizing ancilla qubits is the
paper's headline overhead metric and is basis-independent.

## The distance oracle is an UPPER bound — and that matters

Deformed-code distance is estimated by BP+OSD coset minimisation, which returns an
**upper bound** (`true_d ≤ reported_d`). The only error mode is a **false positive**
— reporting 12 when the true distance is lower. A search that maximises `24 - qubits`
subject to reported-`d == 12` is under direct pressure to find sparse graphs where
BP+OSD *over-reports*, i.e. to reward-hack the oracle. **This was not hypothetical:
the originally drafted seed (6 greedy edges, 24 qubits) reported distance 12 under a
weak oracle but its true X-distance is ≤ 10** (a genuine weight-10 logical exists).
The evaluator hardens against this in three layers:

1. **Asymmetric budget / raised floor.** The X-distance is the sole over-report
   source (the Z-distance is reliably 12); it gets `GAUGE_BUDGET_X_S` (default 10 s)
   vs the Z-side's 6 s, at `osd_order` 25 / `max_iter` 120. This floor alone already
   exposes the drafted seed as distance 11.
2. **Verify-before-accept.** When the default pass returns `d ≥ 12` **and**
   `qubits ≤ 24` (the whole non-negative-score region, the paper's 22 included), the
   X-side is re-decoded much harder (`15 s`, `osd_order 30`, `max_iter 200`, min over
   seeds 0,1,2) before the verdict is credited. `public.verified` records this.
3. **ILP certification (off-line, the run owner's job).** Even a passing verify is
   **not a proof** — some graphs over-report at every BP+OSD budget tested. Exactly as
   in the paper, **certify any sub-22-qubit winner's distance with integer programming**
   before believing or reporting it.

All budgets are env-overridable (`GAUGE_BUDGET_X_S`, `GAUGE_BUDGET_Z_S`,
`GAUGE_OSD_ORDER`, `GAUGE_MAX_ITER`, `GAUGE_VERIFY_BUDGET_X_S`, `GAUGE_VERIFY_OSD_ORDER`,
`GAUGE_VERIFY_MAX_ITER`, `GAUGE_VERIFY_SEEDS`, `GAUGE_VERIFY_MAX_QUBITS`).

## The seed

`propose_extra_edges()` is a **sparsest-cut greedy** (using the primitives above):
it repeatedly finds `G`'s weakest cut and adds the cheapest edge crossing it. At
`NUM_EXTRA = 6` it is a feasibility-**verified** distance-12 graph at **24 qubits →
score 0**. It is a clean anchor with a built-in gradient: the *same* greedy holds
distance 12 at **4 edges (22 qubits → +2)** — its 6-edge graph is a strict superset
of that 4-edge one — so dropping the 2 non-critical edges already matches the paper.
**Beating 22** (the open frontier — the paper did not prove it minimal) needs a
choice subtler than max-conductance greedy, because graph conductance is only a
*proxy* for the true quantum distance.

(For contrast, the originally drafted seed used *blind* lowest-degree greedy, whose
6 edges have true distance ≤ 10 — a false positive the weak default oracle reported
as 12. The structure-aware sparsest-cut greedy is genuinely distance 12 at the same
edge count, because it reinforces the actual bottleneck.)

## Reference answer (for the run owner — never shown to the LLM)

The paper's published distance-12 solution is **4 extra edges → 22 qubits → score +2**,
edges (as `x^a y^b` monomials):

```
(x², x⁵y³), (x², x⁶), (x⁵y³, x¹¹y³), (x⁷y³, x¹¹y³)
```

In this task's index convention `idx(a,b) = (a%12)*6 + (b%6)`:
`[(12, 33), (12, 36), (33, 69), (45, 69)]`. Fed to this evaluator it returns
`d=12, qubits=22, score=+2, verified=True` — the gold check, and the natural target
for ILP-certifying any candidate that claims to beat 22 qubits. (Leak-proofing: this
README is never fed to the inner loop; the held-out benchmark constants live under the
evaluator's `private` metrics, and the candidate can only return an edge list — the code
and the oracle are entirely the evaluator's, so any positively-scoring candidate is, by
construction, a genuine distance-12 graph, subject to ILP certification of records.)

## How to run

### Smoke test

```bash
conda activate shinka      # or prefix the python call with: conda run -n shinka
cd "$(git rev-parse --show-toplevel)"
python tasks/gross_code_gauging/evaluate.py \
    --program_path tasks/gross_code_gauging/initial.py \
    --results_dir /tmp/gauge_smoke
```

Expected: `correct=True`, `combined_score=0.0`, `distance=12`, `qubits=24`,
`verified=True`, plus structural fields (`cut_side`, `cut_conductance`, `fiedler`).
Runs in ~60 s (24 qubits ≤ 24, so the verify-before-accept gate fires).

### Full evolution (as the orchestrator)

Author a run config (copy `configs/orchestrator_run.default.json`), point
`task.eval_program_path` / `task.init_program_path` at this task's `evaluate.py` /
`initial.py`, set the Azure `evo.llm_models` + a `budget_usd`, and an `eval_time`
above the per-candidate worst case (~60 s; `00:05:00` is ample), then drive windows —
see [../../.claude/skills/shinka-orchestrator/SKILL.md](../../.claude/skills/shinka-orchestrator/SKILL.md):

```bash
cd "$(git rev-parse --show-toplevel)"
python orchestrator/harness/run_window.py --config <run>/run.json --until-decision
```

The budget is hard-capped in code at `budget_usd`. **Before reporting any
sub-22-qubit record, ILP-certify its distance** (BP+OSD is only an upper bound).

## Deps

`numpy`, `ldpc` (BP+OSD) — both already in the `shinka` conda env (used by
`bb_syndrome_sched` / `pbb_code_discovery`). No new installs.

## Files

| File | Role |
|---|---|
| [initial.py](initial.py) | Fixed problem data + graph primitives + EVOLVE-BLOCK (sparsest-cut-greedy seed). |
| [evaluate.py](evaluate.py) | Gross code, deformed-code build, hardened BP+OSD distance, structural diagnostics, scorer. |

(The run config — `task_sys_msg`, Azure pool, budget — is authored per run and not
checked in; copy `configs/orchestrator_run.default.json`.)

## Project context

See the project [CLAUDE.md](../../CLAUDE.md) for environment setup, Azure
credentials, and the rationale behind this Azure-only ShinkaEvolve fork.
