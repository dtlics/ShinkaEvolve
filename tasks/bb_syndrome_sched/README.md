# `bb_syndrome_sched` — syndrome-measurement scheduling for Bivariate Bicycle codes

ShinkaEvolve task: discover a syndrome-extraction **schedule** for a Bivariate
Bicycle (BB) / "gross" code that achieves a lower logical error rate (LER) than
IBM's published gross-code schedule — the problem studied by **AlphaSyndrome**
(Liu et al., [arXiv:2601.12509](https://arxiv.org/abs/2601.12509), ASPLOS '26).

The harness is a faithful re-implementation of
`asyndrome.scheduler.evaluate_circuit` from the authors' artifact
([Zenodo 18291927](https://zenodo.org/records/18291927), MIT) — it does **not**
import the `asyndrome` package, only its code-definition JSONs.

## What's optimized

The function `build_schedule(xnbs, znbs, xanc, zanc, logical_xs, logical_zs)` inside
the EVOLVE-BLOCK of [initial.py](initial.py). It returns the full tick assignment of the
syndrome-extraction circuit: a list of **ticks**, where each tick is a list of
`(data_qubit, ancilla_qubit, pauli)` Pauli checks executed in that time step. Besides the
fixed Tanner neighbours it is handed `logical_xs[i]` / `logical_zs[i]` — the data-qubit
supports of the code's logical operators (the SAME operators the score measures) — so it can
steer hook errors away from low-weight logicals (the AlphaSyndrome lever); the IBM seed ignores them.

This representation can express **any** interleaving of X- and Z-checks
(including deliberate idles), so the search is not restricted to
"X-block then Z-block". The seed is IBM's gross-code schedule (Bravyi et al.
2024), which interleaves X and Z over 7 ticks.

## Topology and gate set

- The code is fixed by a `qecc/bbcode-*.json` file (default `bbcode-72.json` =
  the `[[72,12,6]]` gross code). `bb_neighbors` (fixed scaffolding, **not**
  evolved) derives, for each X- and Z-check, the ordered list of its 6
  data-qubit neighbours and the ancilla index that measures it, straight from
  the BB Tanner structure. This matches the qubit/ancilla indexing of the JSON.
- A check is implemented in `evaluate.py` as `H, CNOT(anc,data), H` (X-checks) or
  `CNOT(data,anc)` (Z-checks) per tick, with a single MZ ancilla readout per
  round, bracketed by ideal MPP stabilizer + logical measurements.
- Noise: IBM **Brisbane** depolarizing model on ancillas per tick —
  `DEPOLARIZE1(p)` with `p_CNOT = 0.007432674432642006`,
  `p_idle = 0.005243978963702009` (copied exactly from `asyndrome.Brisbane`).

## Four hard constraints (failure → score −2.0 + text feedback)

Enforced in [evaluate.py](evaluate.py); the evaluator is authoritative.

1. **Complete** — every `(data, ancilla, pauli)` check implied by the code's
   stabilizers appears exactly once; checks may be reordered in time but not
   dropped, duplicated, added, or rewired.
2. **Non-conflict** — within a tick, no data qubit and no ancilla is used twice
   (one 2-qubit gate per qubit per tick).
3. **Distance** — must not drop the circuit distance below `d − 1`, checked on
   **both** the X- and Z-observable circuits (Stim graphlike search; the `min`
   must stay `≥ d−1`), matching `asyndrome.Schedule.distance`.

A complete, non-conflicting schedule **always** measures the code correctly in the
noiseless sense, *regardless of CNOT order* — each ancilla accumulates exactly its
own stabilizer and is measured out. So there is no separate "wrong-ordering"
rejection; the evaluator runs a cheap Stim noiseless-determinism **sanity** check
(belt-and-suspenders against a pathological circuit), but CNOT **order** is not a
correctness gate. Order instead shapes **hook errors**, which are *rewarded through
the LER* (the score) and, if catastrophic, surface as a distance drop in gate (3).

Invalid candidates are kept in the archive (`validate_fn` returns
`(True, None)`) with `combined_score = −2.0` and a text feedback string naming
the broken rule, so they teach the LLM rather than being discarded. A malformed
return (non-numeric index, wrong-arity tick entry) is also scored `−2.0` (not a
silent crash).

## Score

```
combined_score = log10(SEED_LER / overall_LER)        # valid schedule
overall_LER    = 1 − (1 − x_LER)(1 − z_LER)           # Stim + BP-OSD, Brisbane noise
```

`SEED_LER` is the IBM seed's LER under this harness, calibrated so the **seed
anchors at 0**. Two levers trade off: **depth** (fewer ticks → less ancilla idle
noise; the seed is shallow at 7 ticks) and **hook-error shaping** (the relative
CNOT order decides where a mid-circuit ancilla fault lands). IBM is
shallow-but-rigid; AlphaSyndrome is deep-but-hook-optimal — beating both is the
goal.

**Sampling noise — ERROR-BUDGET (not fixed shots).** `overall_LER` is estimated by sampling
each observable circuit until it has collected ~`TARGET_ERRORS` (default 2000) logical errors
(or hits `MAX_SHOTS`). The score std is then ~`0.434/√(total errors)` ≈ **±0.007**, held
~constant *regardless of the schedule's LER* — a fixed shot count instead reads noisiest on
exactly the low-LER schedules you care about, so the greedy archive-max "chases noise" (winner's
curse: the reported best overstates the true best by ~`std·√(2 ln N_candidates)`). Eval is now
VARIABLE-time (~2.5–5 min for a SOTA-band schedule, ~2.5 min for the seed): a LOWER-LER schedule
needs MORE shots to reach the error target → SLOWER (a near-perfect one hits the ~5-min `MAX_SHOTS`
cap), a worse one fewer → faster. Set the harness `eval_time` >= `00:08:00`. Lower `TARGET_ERRORS`
(e.g. 1000 → std ~0.0094, ~half the eval time) if a multi-day run's wall-clock is a concern; raise
it to tighten (std ~ 1/√TARGET_ERRORS; cost ~ linear). Even so,
**de-noise the very top candidates** (re-eval a few times) before claiming a beyond-SOTA result,
and confirm head-to-head vs AlphaSyndrome's published schedule at a high error budget.

## Leak-proofing

There is **no held-out gate number** to leak: the task is pure LER minimization,
and the score is fully determined by the Stim-measured LER, which a candidate
cannot fake (the observed logicals are pinned from the code JSON, never from the
evolved schedule). The `~+0.15` AlphaSyndrome figure is a public SOTA anchor
(from the paper), not a secret target. The logical/stabilizer operators and
`SEED_LER` live under the evaluator's `private` metrics; only `public` metrics +
`text_feedback` reach the inner-loop prompt.

## Verification (this task vs. the AlphaSyndrome artifact)

The drafted files were cross-checked against the reference artifact and verified
empirically on `bbcode-72` (BP-OSD at `osd_order=3` — a deliberate faster-but-weaker
choice; the reference ran the stimbposd default order 60, so **absolute** LERs are
not paper-comparable, but the relative score is self-consistent since `SEED_LER` is
calibrated to this decoder):

| Check | Result |
|---|---|
| `bb_neighbors` ↔ JSON `x/z_stabilizers` support + ancilla map (bbcode-72) | 36/36 X, 36/36 Z **consistent**; all weight-6 |
| `_BB_PARAMS` (live: 72, 288; commented refs 90/108/144/784 keep exact ℓ,m,a,b) | values match `asyndrome.bbcodeibm` |
| IBM seed `sX/sZ`, Brisbane `p_CNOT`/`p_idle` | exact match to reference |
| Block-convention labeling ↔ shipped JSON | **matches only for ℓ==m** (72 ✅, 288 ✅; 90/108/144 ✗ — see "Codes") |
| Seed valid under generic harness | ✅ depth 7, distance 5 (= d−1), measures correctly |
| Seed LER (error-budget, ~4000 errors) | score ≈ **0.00 ±0.007** vs `SEED_LER = 1.05e-2` (anchors at 0) |
| AlphaSyndrome's published schedule, re-scored here | LER **7.37e-3**, score **+0.151** (single 50k eval — de-noise for a true comparison), depth 18 |
| Reduction vs seed (artifact's `(IBM−α)/α` metric) | **41.6%** ≈ paper's **~44%** BP-OSD on `[[72,12,6]]` |

So the harness reproduces the paper's headline improvement, and the published
AlphaSyndrome schedule scores **~+0.15** under it (the original draft's "+0.11"
annotation was corrected to +0.15).

## Codes / data

`qecc/bbcode-{72,288}.json` — code definitions (`n,k,d`, `x/z_stabilizers`,
`logical_xs/zs`) from the artifact (Bravyi et al. 2024 gross codes).
`qecc/bbcode-72/alpha-*.json` are AlphaSyndrome's **published** schedules for
`[[72,12,6]]`, kept only as reference / regression data — they are never shown to
the inner-loop LLM.

**Only ℓ==m codes are shipped (bbcode-72, bbcode-288).** `bb_neighbors` derives
each check's neighbours in the BB *block* convention (the one IBM's seed schedule
is written in). The artifact's published JSONs use that same data-qubit labeling
**only when ℓ==m**; for the ℓ≠m codes (90, 108, 144 — including the flagship
`[[144,12,12]]`) the JSON uses a different labeling, so the block seed would be
silently invalid against it. `run_experiment._check_labeling` **fails closed** if
a loaded code's JSON doesn't match the block convention. To add an ℓ≠m code,
regenerate its JSON in the block convention (e.g. via `asyndrome.bbcodeibm`'s
construction) so its stabilizer/logical labeling matches `bb_neighbors`.

To switch to bbcode-288, change `get_kwargs` in `evaluate.py` and **recalibrate
`SEED_LER`** (run the unmutated seed once); it is slower than bbcode-72.

## How to run

### Dependencies (one-time, into the `shinka` env only)

```bash
python -m pip install stim stimbposd ldpc        # numpy already present
```

### Smoke test

```bash
cd "$(git rev-parse --show-toplevel)"
# Named flags --program_path/--results_dir mirror how the Shinka harness invokes the evaluator.
python tasks/bb_syndrome_sched/evaluate.py \
    --program_path tasks/bb_syndrome_sched/initial.py --results_dir /tmp/bb_smoke
cat /tmp/bb_smoke/correct.json /tmp/bb_smoke/metrics.json
```

Expected (~2.5 min; the error-budget sampler collects ~2000 logical errors/circuit, ~800k
shots total): `correct=true`, `valid=true`, `depth=7`, `distance=5`, `overall_ler ≈ 1.05e-2`,
`combined_score ≈ 0.0` (seed anchors at 0; now only **±0.007** from sampling noise —
see `private.score_std` and `private.shots`/`errors`).

### Full evolution (as the orchestrator)

Author a run config (copy `configs/orchestrator_run.default.json`), point
`task.eval_program_path` / `task.init_program_path` at this task's
`evaluate.py` / `initial.py`, set the Azure `evo.llm_models` + a `budget_usd`,
then drive windows — see
[../../.claude/skills/shinka-orchestrator/SKILL.md](../../.claude/skills/shinka-orchestrator/SKILL.md):

```bash
python orchestrator/harness/run_window.py --config <run>/run.json --until-decision
```

Each eval is **variable-time** (~2.5 min for the seed, ~2.5–5 min for SOTA-band schedules, ≈ all
BP-OSD decode): the error-budget sampler collects ~`TARGET_ERRORS` logical errors per circuit, so a
lower-LER schedule needs more shots (slower, capped at `MAX_SHOTS`) and a worse one fewer (faster).
The orchestrator harness evaluates **sequentially** (one candidate at a time — there is no
`run_workers` parallelism in `orchestrator/harness/run_window.py`; wall-clock is paced by this eval
plus the Azure mutate latency). The score std is ~`0.434/√(total errors)` ≈ ±0.007 at the default
2000-error target; raise/lower `TARGET_ERRORS` in `evaluate.py` to trade precision vs eval time.

## Files

| File | Role |
|---|---|
| [initial.py](initial.py) | `bb_neighbors` scaffolding + EVOLVE-BLOCK seeded with IBM's gross-code schedule. |
| [evaluate.py](evaluate.py) | Stim circuit build, four validity gates, BP-OSD LER, baseline-relative scorer. |
| `qecc/bbcode-*.json` | BB code definitions (committed task data). |
| `qecc/bbcode-72/alpha-*.json` | AlphaSyndrome's published schedules (reference/regression only). |

## Project context

See the project [CLAUDE.md](../../CLAUDE.md) for environment setup, Azure
credentials, and the Azure-only ShinkaEvolve fork. The smaller
[../cnot_grid_synth/](../cnot_grid_synth/) task is a good structural reference.
