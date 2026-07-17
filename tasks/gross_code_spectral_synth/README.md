# `gross_code_spectral_synth` — minimum-edge spectral certification (v2)

ShinkaEvolve task: find the **smallest simple graph** on the 12 ports (the
qubits of the weight-12 logical X̄_α of the [[144,12,12]] gross code, plus
optional dummies) with algebraic connectivity **λ₂ ≥ 2.0** — certified
Cheeger constant ≥ 1, the Williamson–Yoder Theorem 2 expansion bar for
gauging graphs. A clean, deterministic, sub-second combinatorial problem.

**Provenance, stated honestly** (corrected after post-run verification of
arXiv:2605.21746): this criterion is *stricter than* the GeneCS compiler's
real acceptance (λ₂ ≥ 2β with β ≈ 0.34, i.e. λ₂ ≥ 0.68), and GeneCS's
gross-code output (24 qubits/25 checks, degrees 7/8) is
degree-augmentation-driven with **no minimality claim**. So this task is
**not** a head-to-head against the paper's published numbers; it is a
self-contained spectral minimization grounded in the WY expansion
desideratum, with the GeneCS-style constructor's measured E=24 and the
hand-crafted WY 22-edge graph (λ₂ = 0.925 — *uncertified*, though its
distance-12 is IP-proven; the certificate cannot see distance) as context
baselines.

## The v1 star loophole (why v2 exists)

Run `spectral_v1` ($29.25, 90 programs) produced **two** results:

- a genuine discovery: a **certified E=20 graph** (λ₂ = 2 exactly, degrees
  3⁸4⁴, integral Laplacian spectrum {0, 2⁵, 4³, 6³}) — independently
  re-verified, unreachable by 300-restart simulated annealing, beating the
  documented SA record (21), all circulants (24) and the constructor (24);
- a champion that was junk: an **E=11 port-star** (λ₂ = 1.0, uncertified,
  the worst possible expander) scoring **+11.67** and out-ranking the real
  discovery (+6.96).

The hole: v1 paid "frontier credit" for reaching intermediate λ₂ levels
with few edges, interpolating the constructor's measured curve — but that
curve lives on G0-containing graphs (E ≥ 18) while sparse hubs reach
mid-band λ₂ trivially (a star has λ₂ = 1 *exactly*), so the credit at low
λ₂ was wildly over-generous and the degenerate corner became the global
maximum. Same failure class as the gcg1 spanning tree: **any region where
score improves while the certifying quantity degrades will become the
champion.** v2 deletes the mechanism:

1. **Edges are only rewarded after certification.** Below λ₂ = 2, edge
   count earns nothing; the only way up is λ₂.
2. **Certified always dominates**: uncertified scores are capped below 5.0
   (analytically: 2.5·λ₂ < 5); every certified graph with E ≤ 24 scores
   ≥ ~6.
3. **Leaves are priced as certification-blockers** (Fiedler: λ₂ ≤ vertex
   connectivity ≤ min degree, so any degree-1 vertex caps λ₂ at 1). The
   port-star now scores ≈ **−8.6**.
4. **Simple graphs only** (parallel edges invalid): the minimum-edge claim
   stays a clean simple-graph statement.
5. [test_spectral.py](test_spectral.py) locks it in — landmark
   regressions (star, dummy-star, WY, ring, record) **plus a
   certified-dominance fuzz property test**, the test class that would
   have caught v1.
6. **Independently red-teamed before shipping**: a 3-agent adversarial
   exploit-hunt (every claim numerically verified) found **no
   breaks-intent exploit** — the uncertified cap (< 5.0) and the certified
   E ≤ 24 floor (≥ ~5.7) hold analytically and empirically; the 1e-9
   certification epsilon is load-bearing (the record's λ₂ computes 1.3e-15
   below 2) but uninhabited by genuinely sub-2 graphs (closest found:
   1.7e-4 below); dummies, hubs and parse tricks all fail. Its four minor
   findings are fixed: strict integral parsing (no silent float/string
   coercion), canonical edge sort (graph-level determinism of the
   congestion tiebreak), an uncertified oversize penalty (removes the
   λ₂→2⁻ plateau above E=24), and disclosure of **K₂,₁₀** — the *second*
   known certified E=20 graph, maximally degenerate (vertex connectivity
   2, congestion 9), scoring **9.80**, correctly ranked below the record's
   9.96 by the tiebreaks. Don't waste budget rediscovering it.

## Score (v2)

```
CERTIFIED  (λ₂ ≥ 2.0 − 1e-9):  6.0 + (24 − E) − 0.02·max(0,ρ−2) − 0.01·max(0,Δmax−4)
UNCERTIFIED:                   2.5·λ₂ − 1.0·(#degree-1 vertices) − tiebreaks
invalid spec: −100;  crash: −1000
```

Landmarks: constructor E=24 → **+6** · SA record E=21 → **+9** · the known
record E=20 (**the seed**) → **+10** · certified E ≤ 19 → **+11 and up**
(the open jackpot). Uncertified: WY 22-edge ≈ +2.3, 12-cycle ≈ +0.7,
port-star ≈ −8.6.

## State of knowledge (verified — a re-run must go *beyond* this)

| fact | status |
|---|---|
| certified **E=20** exists (integral graph, seed of this task) | verified from raw edge list; edge-minimal (removing any edge drops λ₂ < 2) |
| SA (300 restarts × 6000 steps) ceiling | certified E=21; cannot reproduce E=20 |
| circulants C₁₂(S); dummy/bipartite (K₂,₁₂, cones) | E=24; dummies **dilute** λ₂ |
| λ₂ ceilings found: E=19 → ~1.59, E=18 → ~1.47 | no certified E ≤ 19 known |
| 12-vertex 3-regular | provably cannot certify (trace argument) |
| Fiedler floor | E ≥ 12 |

**Honest expectations for a re-run:** the headline question left is
*certified E ≤ 19* — strong evidence says it does not exist, so treat a
re-run as a long-shot structured hunt (integral graphs, algebraic
constructions over the port labels — blind swap-SA is known to stall at
21), plus E=20-diversity (is the record unique?) and tiebreak refinements.
The higher-value next step for the *physics* program is cross-scoring the
E=20 record on the real protocol in
[../gross_code_gauging/](../gross_code_gauging/) — does λ₂ = 2 correspond
to real dressed distance? That is a `calibrate.py --compare`-style run,
not an evolution run.

## How to run

```bash
conda activate shinka
cd "$(git rev-parse --show-toplevel)"
python tasks/gross_code_spectral_synth/evaluate.py \
    --program_path tasks/gross_code_spectral_synth/initial.py \
    --results_dir /tmp/spectral_smoke        # seed = the record: +9.96
python tasks/gross_code_spectral_synth/test_spectral.py
```

For evolution: `eval_time ~ 00:02:00`, favor cheap models (throughput is
the constraint; the previous run measured codex ≈ $0.19/call as the best
value, with a stronger model worth pulling in only to break stalls).
`SPECTRAL_LAM2_MIN` moves the certification bar.

## Files

| File | Role |
|---|---|
| [initial.py](initial.py) | Fixed data + λ₂/Fiedler/preview tools + EVOLVE-BLOCK seeded with the verified E=20 record. |
| [evaluate.py](evaluate.py) | Deterministic v2 certifier/scorer (lexicographic: certification, then edges; leaf penalty; simple graphs). |
| [test_spectral.py](test_spectral.py) | Landmark regressions + certified-dominance fuzz property. |

## Provenance of the numbers

- v1 run + postmortem: `spectral_v1` results (run archive; RUN_SUMMARY
  includes the independent verification appendix).
- GeneCS facts (λ₂ ≥ 2β gate, β ≈ 0.34; degree-driven 24/25; no minimality
  claim): full-text verification of arXiv:2605.21746 post-run.
- WY expansion bar (Cheeger ≥ 1): arXiv:2410.02213 Theorem 2.
- E=20 record: independently recomputed from the raw edge list (λ₂ =
  2.000000, spectrum {0, 2⁵, 4³, 6³}).
