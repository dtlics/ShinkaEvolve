> **ARCHIVED — historical reference only.** APPLIED + verified and committed (da85d0d, 2026-06-19); SUPERSEDED by the current `CLAUDE.md` + `.claude/skills/shinka-orchestrator/SKILL.md`. Do NOT use as current guidance.

# FIX PLAN — Discovery→Ground gap (locked design + bundled change plan)

> **STATUS: APPLIED to the working tree (uncommitted) on 2026-06-19.** DEC-1..DEC-7 are live in
> code + teaching docs (`pytest orchestrator/tests` = 97 passed). The foundation halves
> (`interventions.jsonl` field rename `work_dr`→`work_discovery`+`work_grounding`, the
> `termination_streak` derivation, the `spawn_island` stdin schema) were applied directly per the
> owner's authorization. Once committed, move this file to `docs/archive/<date>/`. **Part 0 records
> the final reconciliation; where Parts 4–8 below describe the earlier *plan*, Part 0 governs.**
>
> **Scope (owner):** the *entire* doc + code-comment surface is in-scope for rewrite — every
> teaching doc, README, stale fix-plan doc, and `shinka/` comment — not just the orchestrator
> playbook. **The docs in this repo are not instructions; they are the target artifacts.**

Two multi-agent workflows produced this (22 + 12 agents). Forensics is ledger-confirmed; the
design is coherence-checked (one vocabulary, docs+code agree) and adversarially stress-tested.

---

## Part 0 — APPLIED state + final owner reconciliation (2026-06-19, governs Parts 4–8)

The DEC-1..DEC-7 design is live in the working tree. **A two-pass adversarial verification
(2026-06-19) confirmed all 16 requirements (DEC-1..7 + 5 refinements + extras) satisfied, 97 tests
green.** The first pass caught and the second pass confirmed fixed: a half-applied DEC-2 edit (a
"prefer R2 up front" license that survived in `CLAUDE.md:52-53` + `:147` after `SKILL.md` was
reformed — a reversion artifact); two stale "SECONDARY run_window gate" docstrings left after the
gate's removal (`journal.py`, `test_improvements.py`); and a latent DEC-7 gap — `deep_research.py`
wrote the `usable` bool only to its return envelope, not the *logged* stub `response` the gate reads,
so an empty-brief DR could slip the `usable` screen (now fixed + end-to-end smoke-confirmed).

Five owner refinements resolved:

1. **Recency check stays STRICT (`>`).** The boundary is the *previous* `control_return` row's
   timestamp; a discovery done in the current interval is always strictly later, so strict-greater
   correctly includes it, and a borderline tie fails CLOSED (safer for a gate). One discovery round
   licenses *every* grounding in the same interval (the "ground each direction, up to 3" pattern),
   because the boundary doesn't advance until the orchestrator writes its `control_return` row
   AFTER acting. Safe — no fragility; kept as applied (`journal._control_return_boundary`).
2. **Ground EACH (direction, citation), max 3; "discovery round == DR round"; LENIENT adversary.**
   Already live: SKILL.md:535 ("GROUND each triaged direction, up to a MAX of 3"), SKILL.md:544-550
   (the verification step is "**LENIENT — its job is provenance-authentication + path-assignment,
   NOT rejecting directions … Authenticated directions go to grounding anyway**"), archive-analyst.md
   (trust-and-ground, never kill by name). Terminology unified (R1=Azure DR, R2=archive-analyst,
   both are a "discovery round / DR round").
3. **Foundation halves applied now + boot setup-check.** Done directly. `run_window.main` carries an
   **O5 setup-check** that green-lights the gate contract at boot (asserts `discovery_in_interval`
   and the three-axis `recent_work_axes` are wired) before spending anything.
4. **The `run_window` "secondary gate" / `is_grounding_window` were REMOVED.** They rested on a false
   premise: canonical grounding is a STANDALONE `mutate.py` call between clusters, which never flows
   through `run_window`'s per-candidate loop — so a `mutation_web_search`/`is_grounding_window`
   "grounding window" guard protected a path grounding never uses. The real protections are the
   **unconditional `spawn_island` PRIMARY gate** (every novel new-island grounding — the actual
   incident) + the **`grounding-engineer` refusal** + the **`work_discovery`/`work_grounding` split**
   (a grounding alone can't pad the termination streak). `evo.mutation_web_search` is now documented
   as a plain, unused inner-loop web-search toggle, nothing more. `is_grounding_window` was never
   introduced.
5. **Combine-path residual (accepted).** A COMBINE grounding into an *existing* island is ordinary
   archive insertion (low harm — it's what a normal mutation does) and is not separately gated; the
   `work_discovery`/`work_grounding` split keeps it from padding the streak. The high-value path (a
   protected new island) is unconditionally gated. This is consistent with the owner-accepted
   Option-A residual (Part 6).

---

## Part 1 — The incident (ledger-confirmed)

The sanctioned flow is **DISCOVERY** (Azure `deep_research.py`, or the Claude-native
`subagents/archive-analyst.md`) → **TRIAGE** (three paths) → **GROUND** (hand-authored prompt to
Azure `mutate.py`, or `subagents/grounding-engineer.md`). The run `cnot_grid_synth_run01`:

| Deviation | Evidence |
|---|---|
| **D1** — zero sanctioned discovery | `calls.jsonl` = 12 rows, all `kind=="meta"`; **0** `deep_research`, **0** `archive-analyst`. |
| **D2** — triage skipped | no discovered idea ever existed to triage. |
| **D3** — undocumented grounding | ad-hoc Claude **tournaments over the orchestrator's own hypotheses** (a generic Workflow script `grounding3/parallel_steiner_workflow.js`), sanctioned nowhere. |
| **D4** — `work_dr` misrecording | `work_dr=3` at windows 6 & 10 with zero discovery rows; w6 grounded nothing yet still logged `work_dr=3`. |
| **D5** — teaching present, unobeyed | run started 2026-06-16 20:24 UTC, **53 min after** `d54c615` — the commit that first introduced the three-paths teaching + `grounding-engineer.md`. |
| **D6** — likely prize cost | named the real de Brugière construction, declared it "out of reach" **without running a DR to confirm**; banked a constant-factor trick (score 0.0608) instead of the structural win (est. ~0.85). |

**What the "tournament" actually was (verified):** `grep -i tournament` over the whole repo = **0
hits** in any doc/script/subagent. It lived only as run-time improvisation: the orchestrator used
the generic Workflow tool to run a 5-strategy bracket (`block_divide_conquer` [winner, slope
4.7918], `parallel_disjoint_steiner` [= the de Brugière construction, re-attempted and judged
inferior], `parallel_frontier_elim`, `twoD_native_kms`, `open_best`), then funneled the winner
through the normal `embed → archive_record → spawn_island` path. Its de Brugière "negative" was the
agent's own from-scratch implementation under time pressure — **not authoritative**; a real DR was
never run.

---

## Part 2 — Root cause (is it a teaching problem?)

**Largely yes — leading cause is the teaching (~45%), with a missing code-gate (~25%) and a
residual agent-judgment share (~30%).** Three adversarial lenses (disobedience 55/33/12, code-gate
30/25/45, ultracode-judgment 30/50/20) reconcile to that split. The mechanisms:

1. **No hard gate in the top-level flow** — `SKILL.md:111-114` lists only the framework-audit + a
   *discretionary* DR check; grounding is never a gated step. The one real precondition is buried in
   `grounding-engineer.md:3`, unreachable from the default `mutate.py` grounding path.
2. **`work_dr=3` bakes in the conflation** — `SKILL.md:136` defines `work_dr` as "DR magnitude" but
   keys its top value on the *grounding* outcome; the prior post-mortem's rename to "DISCOVERY
   magnitude" was never applied.
3. **The tournament-on-own-hypotheses path is unnamed** — neither sanctioned nor forbidden.
4. **Nothing fails closed in code** — `spawn_island.py` takes a bare `program_id`; `deep_research`'s
   `calls.jsonl` is read by no decision; `recent_work_axes` is a dead hook.
5. **The timeline proves prose alone is insufficient** — the teaching authored from a prior identical
   failure was on disk and ignored 53 minutes later.

Honest residual (~15–25%): a provenance gate forces a discovery *row* to exist, not that its
*findings* bind the grounding.

---

## Part 3 — Locked design decisions (owner)

- **DEC-1 — Two routes only.** Discovery is valid only via **R1** = Azure `deep_research.py` or
  **R2** = `subagents/archive-analyst.md`. Ad-hoc/self-invented "discovery" (a tournament over own
  hypotheses) is not discovery. Grounding may act only on an R1/R2 technique.
- **DEC-2 — Azure DR is the near-always default.** R2 is a **narrow fallback**, permitted only
  when, for the *same question*: an R1 DR already ran, the orchestrator has strong confidence a good
  answer exists, yet all R1 directions aren't helping.
- **DEC-3 — Trust-and-ground (rationale written into the docs).** Because the discovery/triage step
  has an observed inclination to **deny ideas** (refusing to even try grounding, dismissing on sight
  "by reading the name"), route to external R1 by default (fresh, web-cited, harder to dismiss) and
  teach the orchestrator to **incline to trust** discovery and initiate grounding; bias triage toward
  novel→ground / similar→combine, use useless→ignore sparingly, never kill an idea by its name.
- **DEC-4 — Tournament folds into archive-analyst, optional + unspecified.** Not a new subagent;
  at most a final SORT/RANK pass over *already-discovered* (R1/R2) ideas. Whether/how left open.
- **DEC-5 — Fix the mis-routing doc** (`SKILL.md:849`): any Claude-native discovery routes through R2.
- **DEC-6 — Split `work_dr`** → `work_discovery` + `work_grounding` so grounding-without-discovery
  is detectable and can't masquerade as discovery.
- **DEC-7 — Gate = Option A (row-existence) + recency.** Fail-closed: a grounding/`spawn_island` is
  allowed only if a discovery **stub** exists for the **current control-return interval** (not a
  stale one). Both routes must leave a machine-readable stub. Owner accepts Option-A's residual; the
  stronger content-match gate is **not** built.

---

## Part 4 — Bundled change plan (docs + code land together)

One concept threads through every layer: a **"discovery stub"** = a `calls.jsonl` pointer of
`kind ∈ {dr, archive_analyst}` with a fresh `timestamp` + a `usable` bool, written by R1/R2, scored
as `work_discovery`, and checked by the recency gate within the current control-return interval.
`foundation_flag` marks owner/setup-time edits (the journal JSON contract, termination derivation,
`spawn_island` stdin schema); everything else is deployable now.

### B1 — Stub vocabulary spine (P0) · `deep_research.py`, `journal.py`, `SKILL.md`, `archive-analyst.md`
- **Decision:** keep two sibling kinds `{dr, archive_analyst}` (no rename of `dr`, avoids rippling
  through `read_calls`/tests/meta); add a `usable` bool so a refused/empty-brief DR is distinguishable.
- `deep_research.py:189-214` — add `usable: bool(brief)` (success) / `usable: False` (refused) to the
  response dicts; comment that this `kind=dr` row **is the R1 discovery stub** the gate reads.
- `journal.py:501-505` — `read_calls` docstring: kinds `meta / dr / archive_analyst`; discovery kinds
  the gate recognizes = `{dr, archive_analyst}`. (No logic change.)
- `SKILL.md:451-452` — the DR self-log line now describes the machine-readable discovery stub
  (`{query, brief, timestamp, usable}`); a `usable:false` DR does not unlock grounding.

### B2 — Discovery-routing + trust teaching (P0, DEC-1/2/3/5) · `SKILL.md`, `CLAUDE.md`, `archive-analyst.md` — hand-merged prose
- `SKILL.md:846-851` (the DEC-5 fix) — "Do less" applies to *interventions*, **not discovery**;
  discovery is exactly R1 (default) or R2 (narrow fallback); never an ad-hoc tournament over own
  hypotheses; introspection can't surface a technique absent from the archive — that needs R1.
- `SKILL.md:439-443` — reframe DR as the **near-always-default** route; state the two-route rule and
  the DEC-3 why (observed denial-inclination → external-by-default + trust-and-ground).
- `SKILL.md:469-479` — R2 is the **narrow fallback** with the exact DEC-2 condition; **remove** the
  "prefer up front" license; R2 must leave a `kind=archive_analyst` stub.
- `SKILL.md:481-494` — triage: only R1/R2 ideas are triageable (a self-invented hypothesis fails
  provenance and is dropped); lean toward acting; verification must (1) **authenticate provenance**
  and (2) map to one path; never kill by name / for being "similar/renamed".
- `SKILL.md:40-45` + `CLAUDE.md:13-15, 40-48` — the two-exceptions / standing-role / EXCEPTION bullets
  rewritten so R2 is a post-R1 fallback and grounding requires **both** an in-interval triaged R1/R2
  discovery **and** an Azure refusal.
- `archive-analyst.md:3, 35-43` — frontmatter + Recommendation: R2 = narrow fallback to R1; promote
  `deep_research` escalation to a **leading** branch (if external citations are needed, that needs R1,
  not introspection); must emit a discovery stub; trust-and-ground bias.

### B3 — `work_dr` → `work_discovery` + `work_grounding` (P0, DEC-6) · `SKILL.md`, `CLAUDE.md`, `journal.py`⚠, `run_window.py`, tests⚠
- `SKILL.md:128-147` ⚠ — new canonical row; `work_discovery` set only by a logged R1/R2 stub (a
  brainstormed technique scores 0); `work_grounding` settable only when `work_discovery>0` supplied
  the technique this interval; `work_score = work_audit + work_discovery + work_grounding`;
  **`intervened = work_audit>0 or work_discovery>0`** (grounding alone never flips it).
- `SKILL.md:611-617` + `CLAUDE.md:68-79` — termination: a hand-authored grounding does **not** count
  on its own — it counts *with* the discovery it grounded.
- `journal.py:451-457` ⚠ — `recent_work_axes` returns the three axes (makes a grounding-without-
  discovery stretch detectable).
- `journal.py:484-493` ⚠ (FOUNDATION / S1) — `termination_streak` docstring + the `intervened`
  fallback derivation key on `work_discovery`, not `work_grounding`.
- `journal.py:432-434` — `recent_work_score` docstring only (cadence taper reads the scalar →
  `cadence_policy.py` needs **zero** change, verified).
- `run_window.py:425-429` — reword the `fix_budget=3` docstring: "grounding a **triaged discovery**
  direction".
- `test_improvements.py:225-229, 2469-2473` ⚠ — update to three axes; **add a required negative**:
  a `{stagnation_flag:True, work_grounding:2}` row → `termination_streak == 0`.

### B4 — Fail-closed recency gate + both routes' stubs (P0, DEC-7) · `journal.py`, `spawn_island.py`⚠, `run_window.py`, `archive-analyst.md`, `grounding-engineer.md`, `SKILL.md`
- `journal.py` (new read-only helper) — `discovery_in_interval(results_dir)`: boundary = timestamp of
  the most-recent `control_return` intervention row (the only timestamped interval anchor — windows
  carry none); return in-interval **usable** stubs of kind `{dr, archive_analyst}`; empty ⇒ gate fails
  closed. **Single source of truth** for the recency rule.
- `spawn_island.py:40-54` ⚠ (PRIMARY gate) — before opening the DB, require
  `discovery_in_interval(results_dir)` non-empty (via the sanctioned `_common._lazy_journal` read-only
  bridge); else return `ok:false` / non-zero, no island seeded.
- `spawn_island.py:18-27` ⚠ (CONTRACT half = owner/setup) — add `results_dir` + optional
  `discovery_provenance` to the stdin schema.
- `run_window.py:765-775` (SECONDARY gate) — refuse to treat a window as grounding when
  `evo.mutation_web_search` is set but no in-interval stub exists. **Drop the dead `grounding_parent_id`
  reference** (verified absent from live code).
- `archive-analyst.md:45-49` — REQUIRED step: emit the `kind=archive_analyst` stub via
  `journal.py log_call` (CLI verified to accept arbitrary kind, no extension needed); cost 0.0
  (Claude-native, no double-count); empty/unusable → `usable:false`.
- `grounding-engineer.md:16-18` — input validation: **refuse** if the spawn prompt carries no
  in-interval R1/R2 provenance reference.
- `SKILL.md:111-115` — the control-return step gains the **HARD GATE** sentence (grounding invalid
  without an in-interval triaged R1/R2 stub; a stale stub doesn't satisfy it).
- `SKILL.md:496-503` — prepend the precondition to the grounding recipe (both executors); **fix the
  `FULL_SYS_FORMAT_DIFFERENT` import** to `shinka.prompts.prompts_full` (current import raises
  ImportError — SK-1).
- `SKILL.md:518` ⚠ + an ordering note — document the new `spawn_island` stdin fields; **run discovery
  + grounding BEFORE writing the `control_return` row** (else the row becomes the boundary and your own
  in-interval stub fails the strictly-greater recency check).

### B5 — Close the no-web-search COMBINE bypass (P1, adversarial must-fix) · `run_window.py`, `SKILL.md`
- A combine grounding that enables no web search and seeds no island escapes both gates. Add a new
  `evo.is_grounding_window: true` run-config lever the orchestrator sets on **every** grounding run;
  the secondary gate fires on `mutation_web_search OR is_grounding_window`. Accepted residual: relies
  on the orchestrator honestly setting the flag (not a chokepoint at `archive_record.py`), but B3
  ensures an unflagged combine still can't advance the termination streak.

### B7 — Bright-line prohibitions + tournament fold-in (P1, FP-4/DEC-1/DEC-4) · `SKILL.md`, `CLAUDE.md`, `archive-analyst.md`
- `SKILL.md:825-844` + `CLAUDE.md` never-do list — two bullets: never ground a technique not from an
  in-interval triaged R1/R2 discovery; never treat a tournament over your own ideas as discovery (the
  only sanctioned Claude-native discovery is archive-analyst; the only sanctioned multi-agent grounding
  is grounding-engineer).
- `archive-analyst.md:45-49` — the DEC-4 optional final SORT/RANK pass over already-discovered ideas:
  ranks only R1/R2 ideas, sorts-not-culls, never a substitute for discovery; mechanism unspecified.

### B6 — Intersecting staleness fixes (P2) · `README.md`, `sampler.py`, `dr_client.py`, `dbase.py`, this doc
- README:22 add `grounding-engineer`; `sampler.py:95-99` drop "Phase 2 of research-grounding"/"DR
  brief" (it's the per-island meta direction); `dr_client.py:1` + `dbase.py:630-633` drop the "phase
  1/2 of research-grounding" labels; on landing, flip this doc's banner to APPLIED + archive it.

---

## Part 5 — Synergy (why the bundles interlock)

The single "discovery stub" concept flows: **teaching** (B2/B7) says only R1/R2 produce discovery and
grounding needs an in-interval stub → each **route writes** that stub (B1 R1; B4 R2) → **B3** scores it
as `work_discovery` so the termination streak can tell discovery from grounding → **B4** is the recency
authority both gates and grounding-engineer read. What breaks if a bundle lands alone: B4 gate without
B1/B4 stubs ⇒ legitimate discovery has no stub and the gate fails closed on real work; B3 doc-rename
without the code derivation ⇒ the gate names a field nobody writes; B2 teaching without B4 ⇒ unenforced
prose (the prior failed state); B5's lever without the SKILL.md requirement to set it ⇒ the gate checks
a flag never set. The ~10 doubly-targeted doc passages must be **hand-merged** into one text per
location (sequential application would clobber).

---

## Part 6 — Residual risk (honest)

- **Owner-accepted Option-A residual (~15–25%, not closed by decision):** the gate proves a discovery
  *row* exists, recent + usable — not that its *findings* bind the grounded technique. An agent can run
  one real DR, ignore its brief, and ground its own hypothesis. Reuse-within-interval widens this: one
  stub satisfies unlimited groundings for the whole (taper-widened) interval — no one-stub-one-grounding
  nonce (the content-match gate was declined).
- **DEC-2 fallback is prose-only (escape hatch — needs an owner call, see Part 8):** the gate accepts
  *any* in-interval usable stub of either kind; it does not require a prior same-question R1 before an
  R2 stub satisfies grounding. So an agent can spawn R2 first and never run R1. Only the trust/bias
  teaching discourages it.
- **R2 self-laundering:** R2's read is itself Claude reasoning with no external web-citation anchor, so
  the provenance authenticator can be satisfied by the very route that is the laundering vector.
- **`recent_work_axes`** now makes a grounding-only stretch *detectable*, but no caller acts on it yet.

---

## Part 7 — Test plan

- `conda run -n shinka python -m pytest orchestrator/tests` stays green after the rename.
- **Required negative:** a `{stagnation_flag:True, work_grounding:2}` control_return row →
  `termination_streak == 0`.
- New unit tests for `discovery_in_interval`: no row/no calls → empty (fail closed); usable `dr` after
  last control_return → returned; at/before boundary → excluded (strictly-greater); refused/`usable:false`
  → excluded; usable `archive_analyst` → returned (route parity).
- Smoke the PRIMARY gate (`spawn_island.main`): no in-interval stub → `ok:false`/non-zero, no island;
  with a usable stub after a control_return → island seeded.
- Smoke the SECONDARY gate (`run_window`): `mutation_web_search` / `is_grounding_window` true without a
  stub → refused; with a stub → honored. Use the circle_packing smoke config.
- No live Azure/DR call or real cluster for verification.
- Doc lint: `work_dr` appears in no live surface (only `docs/archive` + historical plans);
  `grounding_parent_id` nowhere; SKILL.md imports `FULL_SYS_FORMAT_DIFFERENT` from `shinka.prompts.prompts_full`.

---

## Part 8 — Open decisions for the owner

1. **DEC-2 escape hatch — enforce R1-before-R2 in code, or leave it prose-only?** The adversarial pass
   confirmed an agent can spawn R2 first and skip Azure DR entirely; only teaching discourages it.
   Code options: (a) require a same-interval `kind=dr` stub before any `archive_analyst` stub can
   satisfy the gate; (b) a route-ordering check in `discovery_in_interval`. Neither is in the locked
   set. **Accept prose-only (matches "narrow fallback, trust the agent"), or add the code check?**
2. **FOUNDATION application timing** — confirm the foundation halves are applied **at setup, not
   mid-run**: the `interventions.jsonl` field rename (B3), the `termination_streak` docstring+derivation
   (B3, journal.py:484-493), and the `spawn_island` stdin schema field (B4). Everything else
   (gate body, `discovery_in_interval`, the asserts, all prose) is deployable now.
3. **`spawn_island.py` gate-body reclassification** — inserting the gate body edits a file the docs
   label "No (foundation)". The added *logic* (read-only recency check) is strategy-layer; the
   *stdin-schema field* is owner/setup. Confirm you accept that split (flagged ⚠ on the schema edit).
4. **New `evo.is_grounding_window` config key (B5)** — OK to introduce this run-config lever as the
   secondary-gate trigger that closes the no-web-search combine bypass?

---

## Part 9 — Deferred (non-intersecting) staleness batch

A separate cleanup pass (off this fix's discovery/grounding path): SLC-01 (`openai.py` legacy-agentic
docstring), TLD-1 (`README:29` cadence_policy as mutable), TLD-3 (`configs/README:24`), SK-2
(idle-sleep macOS-only vs cross-platform), TLD-4 (`cnot README` 5 s timeout), OCC-2/OCC-3
(`validate_strategy` cadence_policy), SLC-02 (`wrap_eval` agentic comment), SLC-06 (`configs/__init__`
CLI), SLC-07 (`embedding.py` Gemini), SLC-08 (`kwargs.py` openrouter+typo), SLC-09 (`llm.py` CUDA
docstring), TLD-5 (`README:34` omits rollback_decision), SK-3 (skills `/tmp/` on Windows), OCC-1
(`mutate.py` non-Azure fallback). Plus the **stale "PLAN ONLY" / dead-worktree-path banners** on
`FIX_PLAN_RUN_POSTMORTEM_20260616.md` (incl. its `:709` same `FULL_SYS_FORMAT_DIFFERENT` import bug)
and `FIX_PLAN_AUDIT_20260610.md`, and `ROOT_CAUSE_AUDIT_20260603.md:9`'s dead macOS path — flip/archive
in one banner pass. **Optional future tightening (needs an owner decision):** the content-bound
provenance check + one-stub-one-grounding nonce (closes the Part 6 residual; exceeds Option-A).

---

*v2 generated 2026-06-19 by a 12-agent design workflow (after a 22-agent investigation). Coherence-checked
and adversarially verified. Proposes edits to teaching docs + strategy/harness code; the foundation halves
are owner/setup edits. Plan-only until greenlit.*
