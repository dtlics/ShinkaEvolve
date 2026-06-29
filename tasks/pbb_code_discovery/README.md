# `pbb_code_discovery` — non-CSS perturbed bivariate bicycle (PBB) code discovery

ShinkaEvolve task: evolve a generator of **non-CSS perturbed bivariate bicycle
(PBB) quantum LDPC codes** that maximize the figure of merit `FOM = k·d²/n`.

Faithful port of **Campaign 5** (the paper's true novelty contribution — 368
discovered non-CSS codes) of *"Evolutionary Discovery of Bivariate Bicycle Codes
with LLM-Guided Search"* (Cruz‑Benito, Cross, Kremer, Faro;
[arXiv:2606.02418](https://arxiv.org/abs/2606.02418)), source repo
[`qiskit-community/qcode-discovery`](https://github.com/qiskit-community/qcode-discovery)
(Apache‑2.0). Upstream drove the search with OpenEvolve (MAP‑Elites); here the
ShinkaEvolve orchestrator drives it. The construction + distance pipeline is
**byte‑faithful** to upstream (only import paths were rewritten); the scoring is
their Campaign‑5 trust‑adjusted FOM.

## What's optimized

The function `generate_candidates(ell, m)` inside the EVOLVE‑BLOCK of
[initial.py](initial.py). It returns a list of 4‑tuples
`(A_terms, B_terms, C_terms, D_terms)`, each a list of `(x_exp, y_exp)`
monomials over `F₂[x,y]/(xˡ−1, yᵐ−1)`:

- `A`, `B` — weight‑3 **base** polynomials (exactly 3 distinct monomials each).
- `C`, `D` — **perturbation** polynomials (≥ 1 monomial each, non‑empty).

## The PBB construction (paper convention, Eq. 2)

```
Block 1 (mixed):   x-part = [A | B],   z-part = [C | D]   (C left, D right)
Block 2 (pure Z):  x-part = [0 | 0],   z-part = [Bᵀ | Aᵀ]
```

`C = D = empty` reduces to a CSS bivariate bicycle code; non‑empty `C, D` make
the code **genuinely non‑CSS**. Code parameters: `n = 2·ℓ·m` physical qubits,
`k` logical qubits (exact GF(2) rank), `d` the symplectic‑weight distance.
**Within‑block‑1 commutativity** requires `(A Cᵀ + B Dᵀ) mod 2` to be symmetric —
the seed pre‑checks this to avoid wasting evaluation budget, and the evaluator
re‑checks it when constructing the code (`build_pbb_code` raises otherwise).

## Scoring

```
combined_score = Σ_lattice  max trust-adjusted FOM at that lattice          (higher is better)
FOM = k·d²/n        d ≤ 4 → contributes 0 (tiny k/n·0.1 floor keeps valid-but-low-d > nothing)
```

Scored lattices (7): `(6,6) n=72`, `(9,6) n=108`, `(12,6) n=144`,
`(15,6) n=180`, `(30,6) n=360`, `(6,3) n=36`, `(3,6) n=36`.

### Distance — the 3‑tier adaptive pipeline (faithful to Campaign 5)

Each certified candidate runs [qcode_eval/_noncss_distance_worker.py](qcode_eval/_noncss_distance_worker.py):

1. **Hash‑based EXACT low‑weight check** — searches *all* symplectic‑weight
   Pauli operators (X, Z, and mixed Y) for a non‑trivial logical of weight
   ≤ `max_weight`. `max_weight = 6` at `n ≤ 216` (exact `d ≤ 6`), `= 4` at
   `n > 216` (exact `d ≤ 4`). Ground truth; catches the low‑weight logicals BP‑OSD
   misses.
2. **Symplectic MILP** (HiGHS via `scipy.optimize.milp`) for codes beyond the
   hash range, with adaptive per‑logical timeouts (22–90 s). **Decomposed per
   LOGICAL**: each code's `2k` logical ILP solves are fanned out as individual
   jobs across a shared spawn pool (so cores stay busy throughout instead of one
   worker grinding a code's logicals serially), then aggregated in the driver —
   `d = min` over solved logicals (always a valid upper bound), `milp_exact` only
   when *every* logical is proven optimal. A per‑code **CPU‑time** budget of
   `min(2k × per‑logical, 1800 s)` (the *sum* of that code's solve‑times, so a code
   is never charged for time its logicals spend queued behind others) and a
   `weight ≤ 4` early‑reject cancel a code's still‑queued logical jobs. Each worker
   is pinned to 1 thread (`OMP_NUM_THREADS=1`) so the pool doesn't oversubscribe.
   This is an orchestration change only — the ILP formulation is byte‑faithful (see
   `qcode_eval/_parallel_distance.py`). Net win: more EXACT certifications at
   `n ≤ 216` (where MILP can prove optimality), and a tighter honest upper bound at
   `n = 360` (incumbent‑only) that undercuts the discounted BP‑OSD overestimate via
   `min(d_milp, d_bp)` — verified end‑to‑end to deflate BP‑OSD‑inflated FOM.
3. **BP‑OSD fallback** — only if MILP yields nothing. It **overestimates** non‑CSS
   distance (it reports `d ≤ 10` for codes that are truly `d = 6`), so its bounds
   are **heavily discounted** (see trust filter).

### Trust filter (reward‑hack guard)

A distance result is labeled `EXACT` (hash/MILP‑proven), `TRUSTED`
(`d/√n < 1.5`), `PARTIAL` (`< 2.5`), or `UNTRUSTED`. Its FOM is multiplied by
**1.0 / 1.0 / 0.25 / 0.0** respectively. So a speculative BP‑OSD overestimate
contributes **zero** to the score — a candidate cannot win by inflating distance.

### Why this is leak‑proof and reward‑hack‑proof by construction

The score is computed from **real** codes — `k` via exact GF(2) rank, `d` via
exact hash / MILP. There is **no held‑out answer key**: any candidate that scores
well genuinely discovered a good code. The `A = B` / `C = D` self‑dual traps
collapse to `d = 2`; degenerate high‑`k` codes are exposed as `d ≤ 4` by the exact
hash check; BP‑OSD phantoms are zeroed by the trust multiplier. Raw per‑code
distance internals (`d_bposd`, `d_milp`, untrusted bounds, solve times) live under
the evaluator's **`private`** metrics; only the trust‑adjusted view reaches the
inner loop via **`public`** + **`text_feedback`** (the candidate's own discovered
codes, reflected back). This is the leak‑proof‑at‑setup discipline this repo
requires — no inner‑loop "no‑spoil" machinery.

## Genuinely non-CSS — a post-evolution check (not scored)

The construction guarantees the codes are non-CSS in *form* (non-empty `C, D`),
but a non-CSS code can still be **LC-equivalent to a CSS code** — reducible to CSS
by per-qubit single-qubit Cliffords — and so not a genuinely new non-CSS code. The
evolution loop here does **not** test for this (faithful to Campaign 5, where the
LC-CSS filter was an explicit TODO in the non-CSS evaluator, never run in-loop). It
is a **post-evolution verification step**: upstream certifies genuineness *after* a
campaign by running `evaluation/clifford_equivalence.py` (via
`scripts/run_lc_analysis.py`) over the deduplicated discovered catalog — the source
of the paper's claim that **357 of 368** discovered PBB codes are CSS-inequivalent
within the tested Clifford families (§III.D / App. F).

**After a run — what to verify on the discoveries (pointers only; do NOT add scripts
to this folder).** Work against the upstream repo
[`qiskit-community/qcode-discovery`](https://github.com/qiskit-community/qcode-discovery)
(Apache‑2.0; a clone may already sit in the OS temp dir noted in the project memory —
re‑clone if gone). Pull each top `(ell, m, A, B, C, D)` discovery from the run's
archive (`<results_dir>/programs.sqlite`, or the eval's `private.all_results`), then,
in upstream order **evolve → dedup → deep‑MILP → LC‑CSS genuineness**:

1. **Genuinely non‑CSS (LC‑CSS) check** — `evaluation/clifford_equivalence.py`. A code
   is genuinely non‑CSS *within the tested Clifford families* iff it **fails every**
   reduction: `is_lc_equivalent_css_group(ell, m, A, B, C, D)` (fast algebraic, uniform
   per‑block S/H), `is_equivalently_css(code)` (Hadamard 2‑coloring), and
   `verify_uniform_reduction_exact(ell, m, A, B, C, D)` (`{I,S}`/`{H,HS}` GF(2));
   confirm with `verify_lc_bruteforce(ell, m, A, B, C, D)` (all 36 uniform assignments).
   If any returns `equivalent=True`, the code is secretly CSS — drop the novelty claim.
   `scripts/run_lc_analysis.py` runs exactly this over a JSONL of codes and reproduces
   the paper's 10 / 1 / 357 accounting (~2 min). Deps: `galois` + `qldpc` (already in env).
2. **Deep‑MILP distance re‑verification** (recommended for n ≥ 216) — the in‑loop MILP
   uses short timeouts, so a high‑d result there is an *upper bound*, not proven exact.
   `scripts/verify_deep_milp.py` re‑solves with long per‑logical budgets; upstream's pass
   corrected ~22% of distances. Run it before trusting a headline FOM at large n.
3. **BLISS Tanner‑graph dedup** (optional) — `tests/check_all_equivalences.py` removes
   isomorphic duplicates. Needs `python-igraph` (NOT installed — `pip install
   python-igraph` into the **shinka** env only if you need it).

The LC‑CSS module is deliberately **not vendored** (not needed to drive or score the
run); driving these checks belongs to a separate post‑run agent session, not this task.

## State of the art (the bar to beat)

The best published non‑CSS PBB FOM is **6.0** (`[[72,12,6]]`, two known `(6,6)`
bases: Base2 and Base7e — both in [initial.py](initial.py)'s `KNOWN_CODES`). The
open problem is discovering a **new `(A,B)` base with `d ≥ 5`** (Campaign 7d found
~230 bases with `d ≥ 3` but all collapsed to `d ≤ 4` on exact verification), or
reaching higher `d` at a larger lattice (e.g. `d = 10` at `n = 180, k = 12` →
FOM 6.7). The seed scores **≈ 6.0** by construction (it reproduces `[[72,12,6]]`);
the smoke test confirms this anchor.

## Files

| File | Role |
|---|---|
| [initial.py](initial.py) | Reference tables + EVOLVE‑BLOCK `generate_candidates(ell, m)` (byte‑faithful to upstream `seed_solution_noncss.py`) + `run_experiment`. |
| [evaluate.py](evaluate.py) | Shinka contract: build → top‑k select → parallel 3‑tier distance → trust‑adjusted score. Owns all scoring + leak‑proofing. |
| [qcode_eval/](qcode_eval/) | **Vendored, frozen** construction + distance backbone (Apache‑2.0). Not evolved; candidates never touch it. |
| [orchestrator_run.json](orchestrator_run.json) | Run‑config starter with an authored `task_sys_msg` + `objective_brief`. |

`qcode_eval/` modules — **vendored/frozen** science: `bb_code.py` (CSS base),
`pbb_code.py` (PBB construction + GF(2) symplectic logicals),
`distance_bposd_noncss.py` (hash‑exact check + achievable‑syndrome BP‑OSD),
`distance_milp.py` (symplectic MILP), `_noncss_distance_worker.py` (the per‑code
3‑tier `distance_worker`, kept as the sequential fallback; top‑level importable so
the spawn pool can pickle it). **Orchestration only (not vendored):**
`_parallel_distance.py` (top‑level, spawn‑picklable per‑stage workers +
result‑assembly helpers for the per‑logical MILP driver — byte‑compatible result
dicts, verified by `_test_parallel_distance.py`).

## Dependencies

Installed into the **`shinka` conda env only** (additive — kept numpy 2.4.4, so
the cnot/bb_syndrome tasks are undisturbed):

```bash
# C:/Users/dtlic/miniconda3/envs/shinka/python.exe -m pip install qldpc galois sympy
# pulls qLDPC 0.2.9, galois 0.4.11, sympy 1.14.0 (+ numba, cvxpy/highspy for MILP)
```

`ldpc`, `scipy`, `numpy` were already present. Verified API‑compatible with the
vendored code by reconstructing the paper's MILP‑verified `[[72,12,6]]` codes
(Base2/Base7e) and confirming the hash‑exact check returns `d = 6`.

## How to run

### Smoke test (fast — one lattice)

```bash
conda activate shinka   # or prefix python with: conda run -n shinka
cd "$(git rev-parse --show-toplevel)"
PYTHONPATH="$PWD" PBB_LATTICES="6,6" PBB_MAX_DIST_PER_LATTICE=4 PBB_NUM_WORKERS=4 \
  python tasks/pbb_code_discovery/evaluate.py \
    --program_path tasks/pbb_code_discovery/initial.py \
    --results_dir ./_pbb_smoke
```

Expected: `correct=true`, `combined_score≈6.0`, best code
`[[72,12,<=6]] trust=EXACT method=exact_w6`.

### Full evolution (as the orchestrator)

Point a run config (copy [orchestrator_run.json](orchestrator_run.json)) at this
task and drive windows — see
[../../.claude/skills/shinka-orchestrator/SKILL.md](../../.claude/skills/shinka-orchestrator/SKILL.md):

```bash
cd "$(git rev-parse --show-toplevel)"
python orchestrator/harness/run_window.py --config <run>/run.json --until-decision
```

The default Campaign‑5 evaluation (all 7 lattices, 10 distance tasks/lattice) is
~4× slower than the CSS path — the price of exact non‑CSS distance certification,
which the user accepts for the paper's true novelty. Each eval's **scoring verdicts
are deterministic** — the EXACT (hash / all‑logicals‑optimal MILP) and `d ≤ 4`‑reject
outcomes that carry the score are reproducible (fixed BP‑OSD seeds, deterministic
hash, deterministic ILP), so `num_runs=1`. The only scheduling‑dependent quantity is a
*partial* MILP upper bound under pool contention (which logicals finished before the
budget) — but it is always a valid upper bound and only ever feeds the trust‑discounted
BP‑OSD branch, so it cannot inflate the score.

### Env knobs (defaults match `config_noncss.yaml`)

| Env var | Default | Meaning |
|---|---|---|
| `PBB_LATTICES` | the 7 Campaign‑5 lattices | `"6,6;9,6;..."` — override for a quick run |
| `PBB_MAX_DIST_PER_LATTICE` | `10` | diverse candidates certified per lattice |
| `PBB_NUM_TRIALS` | `1000` | BP‑OSD trials (fallback path only) |
| `PBB_NUM_WORKERS` | `max(1, cpu−4)` | shared hash+MILP (and BP‑OSD) pool workers (≈20 on a 24‑core host); each pinned to 1 thread |
| `PBB_MILP_PER_CODE_CAP_S` | `1800` | hard cap on a code's MILP **CPU‑time** budget (`min(2k × per‑logical, cap)`, sum of solve‑times — not wall‑clock) |
| `PBB_EVAL_WALLCLOCK_BUDGET_S` | `2250` | whole‑eval budget (clean abort, no SIGKILL) |
| `PBB_DISTANCE_POOL_TIMEOUT_S` | `1500` | distance‑pool ceiling (collect‑and‑drop on timeout) |
| `PBB_GENERATE_TIMEOUT_S` | `120` | per‑call cap on `generate_candidates` (build backstop) |

Per‑logical caps (HiGHS `time_limit` per ILP solve): **22 / 45 / 90 s** for `n≤108 / ≤216 / >216`.

**Budget invariant** (cf. cnot's M8): `pool_timeout + max_task < EVAL_WALLCLOCK_BUDGET_S
< eval_time`. The unit of work is ONE per‑logical ILP solve (`max_task ≤ 90 s`, the n>216
per‑logical cap), not a whole code. A `ProcessPoolExecutor` cannot cancel an already‑running
worker, but the driver shuts the pool down with `cancel_futures=True`, so a cutoff break does
not await the whole queue — the overshoot past `PBB_DISTANCE_POOL_TIMEOUT_S` is bounded by the
`≤ nw` running solves (each `≤ 90 s`), plus a final BP‑OSD pass (its own pool, also
deadline‑guarded). With the defaults: `1500 + 90 = 1590 < 2250 < 2880`
(`eval_time = 00:48:00`), so the graceful abort always returns a clean score before the
harness SIGKILLs. The sequential fallback (used only if the shared spawn pool fails to start)
and the build loop are both wall‑clock guarded by the same deadline.

## Operational note (deviation from upstream, not the science)

On a distance‑pool **timeout**, this evaluator keeps the results that finished and
abandons the rest (collect‑and‑drop). Upstream re‑ran every task sequentially with
no timeout, which would blow the Shinka per‑candidate wall‑clock budget. The
construction, distance methods, trust filter, FOM, and scored lattices are
otherwise identical to Campaign 5.

## Provenance & license

Vendored backbone and seed are Apache‑2.0, © the qcode‑discovery authors. See
[qcode_eval/](qcode_eval/) headers and the upstream `LICENSE`. Cite the paper
(arXiv:2606.02418) for any results.
