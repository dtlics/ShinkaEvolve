# `gross_code_spectral_synth` — the GeneCS-criteria optimizer race

ShinkaEvolve task: **is LLM-driven evolution a better optimizer than the
GeneCS compiler heuristic at GeneCS's own acceptance criteria, on its own
benchmark instance?** The instance is the gauging-measurement graph for the
weight-12 logical X̄_α of the [[144,12,12]] gross code — the same design
space as [../gross_code_gauging/](../gross_code_gauging/), but scored by
**their certificate, not by physics**. This is a *search* claim
(optimizer-vs-optimizer, same rules), deliberately complementary to the
end-to-end task, which owns the physics claim.

## The criteria (theirs, reverse-engineered)

GeneCS (Zhou, Javadi-Abhari, Li, [arXiv:2605.21746](https://arxiv.org/abs/2605.21746))
never publishes its per-instance acceptance settings. `../gross_code_gauging/genecs.py
--fit-published` pins them from the published outcome: in mono-layer gauging
accounting, generic checks = E+1, so their gross-code Full-Opt result
(24 ancilla qubits, 25 checks, degrees 7/8) forces **E = 24 exactly** — and
the β-scan reproduces E=24 on every seed precisely when the acceptance is

```
λ₂(graph Laplacian) ≥ 2.0        (spectral proxy: certified Cheeger ≥ 1 —
                                  the full Williamson–Yoder expansion bar)
```

Their qubits+checks objective equals **2E+1 regardless of dummies**, so the
race objective is purely: **minimize the edge count E subject to λ₂ ≥ 2**
(degree ≤ 12, their stated bound; congestion as a gentle tiebreak).

## The baseline to beat

Their Algorithm 1 (add-only deficit-weighted random edge addition over the
fixed 18-edge path-matching graph, first-passage acceptance, 100 restarts —
all verified from the paper) reaches **E = 24 on every measured seed**, with
λ₂ landing at exactly 2.000; this matches their published 24/25. Certified
E = 24 scores ~0 here; **any certified E ≤ 23 beats the published compiler
at its own game** (+1 per edge below 24).

## Why there is room (evolution's structural moves)

1. **Their search is add-only with first-passage acceptance** — it never
   removes a redundant earlier addition, never swaps, never re-checks.
2. **Their graphs must contain the path-matching motif** — but the theory
   only needs *paths* (T-join routing), so dropping matching edges is fair
   game and outside their reachable set.
3. **No dummy vertices** — free in their accounting (+1 A_v, −1 cycle
   check), and they change which topologies exist.
4. **No parallel edges** — the gauging double-gross motif.
5. Measured wall in their family: E=23 caps at λ₂ ≈ 1.72, jumping to
   exactly 2.000 at E=24 — a property of their reachable set, not of all
   12-port multigraphs (degree ≥ 2 only forces E ≥ 12, so E ∈ [12, 23] is
   formally open). Structured graphs over the BB monomial labels may have
   better λ₂-per-edge than random matchings.

## The seed — and a fact worth knowing

The seed is the **hand-crafted WY/IBM 22-edge graph** (18 matching + 4
expansion edges; the graph of the 41-element paper gadget). **The GeneCS
certificate rejects it**: λ₂ = 0.925, certified Cheeger 0.46 < 1 — even
though its deformed distance 12 is proven by integer programming
(WY App. B). The spectral criterion cannot see actual distance; the
certificate-driven compiler would discard the hand-crafted optimum. From
this seed, evolution must first *cross* the certificate boundary (the
evaluator reports the weakest Fiedler cut and its crossing count every
eval — edges across it buy the most λ₂; their compiler needs 24 edges to
get there), then *shrink* below 24 with the moves above.

## Score

```
certified   (λ₂ ≥ 2.0):  (24 − E) − 0.02·max(0, ρ − 2) − 0.01·max(0, Δmax − 4)
uncertified (λ₂ < 2.0):  −8 − 5·(2.0 − λ₂) − 0.05·E
invalid spec: −100;  crash: −1000
```

Deterministic, < 1 s per candidate (12–36-vertex eigensolve + Horton cycle
basis) — this task is built for very high candidate throughput; the race is
about search moves, not evaluation cost. `SPECTRAL_LAM2_MIN` moves the bar
(e.g. 0.925 = the level the hand-crafted gadget certifies, for a secondary
race at the WY operating point).

Any certified winner should be cross-scored on the real protocol by
`../gross_code_gauging/` (its `calibrate.py --compare` machinery) — the
certificate is loose in both directions there (measured: λ₂-certified 5.6
can mean real dressed distance 10, and certified 4.2 can hide a weight-9
operator), which is exactly why the two tasks are kept separate.

## How to run

```bash
conda activate shinka
cd "$(git rev-parse --show-toplevel)"
python tasks/gross_code_spectral_synth/evaluate.py \
    --program_path tasks/gross_code_spectral_synth/initial.py \
    --results_dir /tmp/spectral_smoke
```

Expected seed result: `valid=1`, `certified=0`, `lam2=0.9248`,
`combined_score ≈ −14.5`, weak cut `[0,1,2,3,8,9]` with 6 crossing edges.
For evolution, set `eval_time ~ 00:02:00` and a high parallel-eval count —
throughput is the whole point.

## Files

| File | Role |
|---|---|
| [initial.py](initial.py) | Fixed data + λ₂/Fiedler/preview tools + EVOLVE-BLOCK (the certificate-rejected WY/IBM 22-edge seed). |
| [evaluate.py](evaluate.py) | Deterministic certifier/scorer: multigraph λ₂, Fiedler weak cut, exact congestion, GeneCS accounting. |

## Provenance

- Criteria fit + baseline measurement: `../gross_code_gauging/genecs.py
  --fit-published` (β ∈ [0.9, 1.0] all reproduce 24/25; λ₂ = 2.000 exactly).
- GeneCS algorithm details (Algorithm 1, degree bound 12, 100 restarts,
  congestion ρ): arXiv:2605.21746, verified extraction.
- WY seed graph + IP-proven distance: arXiv:2410.02213 App. B.
- Scheduler/decoder ambiguity (why LER is NOT scored here): GeneCS publishes
  no protocol details; see `../gross_code_gauging/calibrate.py --ablate`.
