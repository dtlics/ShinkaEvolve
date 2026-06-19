> **ARCHIVED — historical reference only.** This plan was APPLIED in commit d54c615 (2026-06-16) and is SUPERSEDED by the current `CLAUDE.md` + `.claude/skills/shinka-orchestrator/SKILL.md`. Do NOT use it as current guidance; the "PLAN ONLY — nothing applied" note below is stale.

<!-- v2 comprehensive fix plan. PLAN ONLY — nothing applied. Supersedes the v1 draft (same path). Generated 2026-06-16 by a 14-agent post-mortem workflow over run cnot_grid_synth_20260614 (worktree adoring-wing-d5ffe6). -->

# Fix Plan v2 — post-mortem of run `cnot_grid_synth_20260614` (supersedes the 2026-06-16 v1)

> **➕ Point 4 added 2026-06-16** (Addendum at the end of this doc): a **prompt-construction reform** (mutation + grounding) covering the owner's 7 prompt desiderata + related fixes. **It SUPERSEDES item 1h** — grounding is decoupled from the diff/full/cross mutation path into an orchestrator-authored grounding prompt that never enters the sampler. See `## Point 4` and `### Revision to v2 item 1h`.

> **⚠️ GOVERNING AMENDMENT (2026-06-16) — read first.** The **Addendum v2** at the very end of this doc (revised **Point 4** + new **Point 5 — spoil removal**) is the FINAL, decisive layer and **governs wherever it differs from the v2 body below** (Points 1–3, 1h, the embedded grounding-engineer draft, the files/open-questions lists). Its **"Reconciliation patch-list for the v2 body"** enumerates every earlier line it amends. Net effect to keep in mind while reading: **grounding is a docs-only hand-authored recipe** (no `construct_grounding_prompt.py`, no sampler levers; default `azure-gpt-5.5@medium`); the **entire spoil/no-spoil apparatus is removed** (leak-proofing moves to evaluator design at task SETUP — held-out numbers under `private` metrics); and there are **no open questions** — see the Addendum's "Decisions and rationale."

> **Provenance.** Synthesized 2026-06-16 from a multi-agent post-mortem over the finished run `cnot_grid_synth_20260614` (worktree `adoring-wing-d5ffe6`). PLAN ONLY — nothing applied yet. All line anchors and code facts re-verified against the live files in `wizardly-booth-ad6a2d` and the run artifacts at synthesis time. This is the **v2 revision written in place** at `docs/FIX_PLAN_RUN_POSTMORTEM_20260616.md`, superseding the earlier v1 draft; where the owner reversed a v1 decision, the supersession is called out inline.
>
> **Meta-rule honored:** CLAUDE.md / SKILL.md / the subagent `.md` files are treated as ARTIFACTS TO FIX, not as instructions. Their current contents carry the stale teachings we are repairing.

---

## Owner directives folded in (the three points, refined — these are LAW)

- **POINT 1a — DR de-warning (REMOVE, don't demote).** Delete the `too_many_requests` quota-death paragraph AND the cost-on-failure honesty paragraph from CLAUDE.md:109 and SKILL.md:464–476 entirely. Quota raised to **30,000,000 TPM / 30,000 RPM**; owner is confident the failure won't recur. Teach DR exists + how to use it; NO pre-warning. `deep_research.py`'s `search_surcharge_usd` floor CODE stays as-is. **This supersedes v1 edits 1A/1B**, which only *demoted* the warning to "possible" and *kept* the cost-on-failure honesty.
- **POINT 1b — Archive-analyst as the Claude DISCOVERY alternative.** Teach the `archive-analyst.md` subagent as a Claude-native discovery alternative to Azure DR, usable especially if DR keeps failing.
- **POINT 1c/1d — NEW grounding subagent + parity.** Draft `grounding-engineer.md`: it turns a technique + reference into WORKING CODE for THIS task (what the Azure grounding call does), as a Claude-native alternative. The framework + orchestrator treat its result **identically** to an Azure grounding output: evaluate → archive_record → spawn_island into a new structural family.
- **POINT 1e/1f — Three paths + per-idea triage.** Every discovered idea maps to exactly one of THREE PATHS: (i) NOVEL → ground + new island; (ii) SIMILAR-TO-EXISTING → still valuable, COMBINE via grounding into the closest program; (iii) USELESS → ignore. An adversarial step MUST NOT kill an idea just for being "similar" — "similar" is path (ii), not rejection. A discovery pass returns MULTIPLE ideas; triage EACH one-by-one, source-agnostic (DR call vs Claude subagent).
- **POINT 1g — Refused pivot → grounding subagent.** When the inner-loop Azure model refuses a verified pivot, use the grounding SUBAGENT (Claude authors the code) INSTEAD of hammering Azure with more mutate calls.
- **POINT 1h — Grounding PROMPT-MODE fix.** Pin the pivot-friendly full variant, frame the direction as MANDATORY (a required replacement, not a "potential recommendation"), and de-anchor from the seed parent. `shinka/core/sampler.py` + `shinka/prompts/*` are framework code editable BETWEEN runs (NOT locked foundation); `construct_mutation_prompt.py` is a mutable strategy file.
- **POINT 2 — Never kill an external Azure call before its timeout.** Cost books only on a TERMINAL status, so a kill leaks unlogged-but-BILLED spend (cnot hand-rolled +$7). DO NOT prescribe `@medium` — the agent decides for itself with the available knobs. Scope: the Azure bg-poll mutate/meta/DR CALL only; the sanctioned `run_window`/measure-window kill + `--resume` is unaffected.
- **POINT 3 — Literal user-stop only (DOC-ONLY).** A "user stop" is valid ONLY from a literal message the user actually typed in the live conversation — never inferred/remembered/assumed/summarized/"feels done". Exactly THREE termination criteria: `budget_exhausted` [harness], `stagnation_intervention_exhausted` [harness], literal user stop message [agent, only with a quotable user turn]. NO code gate (owner declined v1's 3E–3I).

---

## Summary table (theme / root cause or goal / fix / DOC↔CODE balance)

| # | Theme | Root cause or goal | Fix | DOC | CODE |
|---|---|---|---|---|---|
| **1a** | DR de-warning | Stale 2026-06-10 one-time observation written as a permanent invariant; quota since raised ~120×. | Delete the quota-death + cost-on-failure paragraphs from both docs; teach DR positively. | CLAUDE.md, SKILL.md | none (`deep_research.py` untouched) |
| **1b** | Claude DISCOVERY alt | Both docs model Azure DR as the only discovery path. | Teach `archive-analyst.md` as the Claude-native discovery alternative; reframe its output to feed grounding. | CLAUDE.md, SKILL.md, archive-analyst.md | none |
| **1c/1d** | Grounding subagent + parity | No Claude-native grounding vehicle; refused-pivot work was hand-rolled. | New `grounding-engineer.md`; document the embed→archive_record→spawn_island parity (proven by `manual_ground.py`). | new subagent, SKILL.md, CLAUDE.md, archive-analyst.md | none |
| **1e/1f** | Three paths + per-idea | An adversarial pass killed the real Steiner pivot as "the dead 2D-mesh renamed"; only one "best" idea reached grounding. | Canonical THREE-PATHS block; per-idea, source-agnostic triage; anti-rejection rule binding on the adversarial step. | SKILL.md (canonical), archive-analyst.md, CLAUDE.md | none |
| **1g** | Refused pivot → subagent | gpt-5.5 KMS prior reverted every text-direction grounding; agent hammered Azure (~6 attempts, +$7). | Teach: switch to `grounding-engineer.md`, do not re-fire Azure mutate. | SKILL.md, CLAUDE.md | none |
| **1h** | Grounding prompt-mode | **CONFIRMED:** prompt scaffolding fought the pivot on 4 axes (random variant, improvement-framed iter msg, weak rec header, island_brief overwrite). | Optional `full_variant` + `grounding_mode` kwargs on `sampler.sample()`; suppress the island_brief overwrite under grounding_mode; threaded as `evo.*` levers. | CLAUDE.md, SKILL.md, construct docstring | **sampler.py, prompts_full.py, prompts/__init__.py** (framework between-runs); **construct_mutation_prompt.py** (mutable-strategy); **run_window.py** (plumbing/between-runs) |
| **2** | No-kill external call | Cost captured only on TERMINAL status (`_azure.py`); a mid-flight kill logs $0 while Azure bills. | Teach: never kill the in-flight Azure bg call; let it ride the 3600s wall; decide handling with the knobs (no `@medium` prescription). | CLAUDE.md, SKILL.md | none |
| **3** | Literal user-stop | `finalize_run` writes any status with no provenance; "user says stop" was an undefined doc peer; agent confabulated a stop. | Doc-only: exactly three criteria; literal-quote requirement; "no code gate — discipline is yours." | CLAUDE.md, SKILL.md (+ conditional archive-analyst, which proved a no-op) | none |

**DOC vs CODE balance (owner stressed docs are EQUALLY important):** 1a, 1b, 1e, 1f, 1g, 2, 3 are **DOC-only**. 1c/1d add a **new DOC subagent** + DOC wiring. Only **1h** carries code, and it is the one sanctioned framework/strategy edit set (default-preserving, between-runs). No FOUNDATION file changes anywhere.

---

## Point 1 — DR + grounding (the big one)

### 1a — DR de-warning (remove, don't demote)

**Root cause.** A one-time 2026-06-10 observation under the OLD quota (250K TPM / 250 RPM) was written into both docs as a permanent CONFIRMED invariant ("the deployment's quota cannot sustain a REAL deep-research job"). The owner raised the quota to 30,000,000 TPM / 30,000 RPM (~120×). The v1 plan demoted the warning to "possible" and kept the cost-on-failure honesty; **the owner now reverses that: remove the whole pre-warning, including the cost-on-failure paragraph.**

**Scope boundary.** Remove ONLY the failure pre-warning. Keep the FINE-AS-IS factual mechanics:
- CLAUDE.md:109 lead — `o3-deep-research` deployment / Foundry project / `web_search_preview`-is-correct sentences.
- SKILL.md:462–464 first sentence — "A server-side terminal `failed` is NOT a content_filter … `reason`/`error_code` now carry the real cause."
- SKILL.md:455–460 — the content_filter / reproduce-paper reshape teaching.
- `test_dr.py` as a plain endpoint probe (CLAUDE.md:109, 121; SKILL.md:468) — strip only the "success proves the endpoint, not job-scale headroom" caveat, which only made sense under the quota-death framing.

**Surviving advice that must NOT be lost when the block is deleted.** "Never loop-retry a heavy failed DR" is sound independent of the quota story; it currently lives *inside* the deleted span. Re-home it in the new positive teaching (see edits below) so it does not vanish. (CODE comment rationale at `deep_research.py:149` for the still-present surcharge floor is flagged as an Open Question, not edited here.)

### 1b — Archive-analyst as the Claude DISCOVERY alternative

**Goal.** DR's DISCOVERY judgment (survey what the archive already tried; decide what technique is genuinely missing) can be done natively in Claude over the archive — and is the right fallback if DR keeps failing. The vehicle already exists (`archive-analyst.md`, referenced at SKILL.md:589–590). Teach it as the Claude-native discovery alternative; do not invent a parallel recipe. Pair it with the discovery-source-agnostic THREE-PATHS triage (1e/1f) so the analyst's output feeds grounding directly.

### 1c + 1d — NEW grounding subagent (`grounding-engineer.md`) + result parity

**Goal.** A Claude-native GROUNDING vehicle that is to the Azure grounding CALL what `archive-analyst.md` is to the Azure DR CALL. It turns a verified-missing technique + reference into WORKING CODE for THIS task, self-evaluates via `evaluate.py`, and hands back a scratch path + correctness. The orchestrator then runs the parity steps.

**Parity contract (verified against `manual_ground.py:61–78`, the worked example).** A correct Claude-authored program is handled IDENTICALLY to a successful Azure grounding mutation:
1. **Embed:** `emb, cost = EmbeddingClient("azure-text-embedding-3-small").get_embedding(code)` (`get_embedding` returns `(vector, cost)`); ledger `cost` via `journal.add_cost(results_dir, cost)` (≈$0.00002 — the ONLY Azure spend). `embedding=[]` is the fallback ONLY when the endpoint is down (silently disables the novelty gate for the seed).
2. **archive_record:** pipe `{db_path, db_config, embedding_model, program:{code, language, generation, parent_id, combined_score, correct, public_metrics, private_metrics, error_traceback:null, code_diff:null, embedding:emb, metadata:{grounding:"claude_authored"}}}` to `archive_record.py` (constructs a LOCAL `EmbeddingClient` + local sklearn PCA/GMM maintenance, fires NO embedding API call → net Azure spend $0).
3. **spawn_island:** pipe `{db_path, db_config, embedding_model, program_id:newid}` to `spawn_island.py` → its OWN island (new structural family). Set `max_islands:0` (default, unbounded) or pin so the score-0 seed isn't retired before it matures.
4. **Log:** ONE `append_intervention` (work score; $0 authoring cost — Claude tokens are off-ledger; only the step-1 embedding is ledgered).

**CRITICAL EVIDENCE CORRECTION (adversarial verdict A2 upheld — and the same error in v1 edit 2B must be fixed).** The run's archived/spawned program **`id=5b404498` was NOT a Steiner seed.** `manual_ground.log` shows `steiner_tokens=0`, and ENDING.md:36 confirms "The one correct grounding output (gen 600) was a bare KMS rewrite scoring 0.0." It is the **motivating FAILURE** (Azure refused Steiner even with `patch_types:['full']`), not proof that a Steiner injection works. Therefore:
- The grounding-engineer file and SKILL.md must cite `manual_ground.py` only as proof of the **mechanical parity path** (embed → archive_record → spawn_island runs and spawns island 4), NOT as a successful Steiner grounding.
- The "score-0 is EXPECTED on a first structural injection" teaching is still correct and valuable, but it must be justified generically ("a first structural family often scores 0.0/below baseline; the value is seeding it for the inner loop to refine") — **not** by falsely claiming "the run's Steiner seed scored 0.0." v1 edit 2B's sentence "the run's Steiner seed scored 0.0 / slope 4.8758" is **factually wrong** and is corrected in this plan.

### 1e + 1f — Three-paths triage + the adversarial step must not kill "similar" ideas + per-idea handling

**Root cause (cause vs symptom).** The symptom: the run's adversarial pass killed the real 4n block-KMS / Steiner-tree-GE pivot as "the dead 2D-mesh renamed." The cause: (a) no authoritative THREE-PATHS rule defining "similar → COMBINE, never kill"; (b) triage taught as single-best-idea (SKILL.md:478–486 picks one technique) instead of per-idea; (c) the adversarial step was never told the three paths; (d) `archive-analyst.md` has no triage and emits a single "Recommendation," so it cannot triage multiple ideas and has no anti-rejection guard.

**Fix.** Author **one canonical THREE-PATHS block** at SKILL.md:478–486 (the hub every other file cross-references by name), make triage per-idea and source-agnostic, and bind the anti-rejection rule on any adversarial step. Resemblance to a DEAD/failed island is still path (ii) unless you can name the SAME concrete failure cause that will recur.

**Vocabulary lock (synergy).** Use exactly **NOVEL / SIMILAR-TO-EXISTING / USELESS** (paths i/ii/iii) in every file — SKILL.md triage block, SKILL.md discovery subsection, `archive-analyst.md`, `grounding-engineer.md`, CLAUDE.md:14. Do not let "History-similar" survive anywhere. Keep aligned with the `work_dr` scale (SKILL.md:130–131): best idea path (ii) → `work_dr:2`; path (i) → `work_dr:3`; all path (iii) → `work_dr:1`.

**One similarity-based drop (resolves A3 open question, flagged for owner).** An EXACT duplicate of an already-elite program is path (iii) USELESS (a combine run would be a no-op). This is the ONLY similarity-based drop; it slightly softens the absolute "similar is never a kill" rule and is surfaced as an Open Question.

**Scope distinction (do NOT cross-contaminate).** `debug-agent.md` triages a stuck CANDIDATE into prompt/parent/one-off — a DIFFERENT taxonomy. Do NOT retrofit the three discovery paths onto it.

### 1g — Use the grounding subagent when the inner loop refuses a verified pivot

Teach the explicit trigger: once the Azure inner-loop model has **demonstrably refused a *verified* pivot** (it reverts to the seed family despite a "replace X with Y entirely" direction — the KMS-vs-Steiner wall), STOP re-firing Azure mutations and switch to `grounding-engineer.md`. This trigger phrase must read identically in CLAUDE.md, SKILL.md, the grounding subsection, and the subagent. (Trigger-floor open question — 1 vs 2 prior Azure refusals — is surfaced for the owner; the no-kill rule (Point 2) means each wasted Azure attempt is real spend, arguing for a low floor.)

### 1h — Grounding PROMPT-MODE fix (pin the pivot variant, mandatory framing, de-anchor)

> **⚠️ SUPERSEDED by Point 4.5** (Addendum at end of doc). The sampler `grounding_mode`/`full_variant` retrofit described below is **DROPPED** in favor of an orchestrator-authored grounding prompt that never enters the sampler (`construct_grounding_prompt.py`). 1h's four-axis diagnosis stays valid as *motivation*; its code edits (1h-CODE-1…5) do **not** land. Retained below for context only.

**CONFIRMED root cause (all four scaffolding pulls verified in live source + corroborated by the run).** Every text-direction grounding attempt produced another KMS variant (steiner_tokens=0, score 0.0), never the verified-missing Steiner pivot, because the prompt scaffolding fought the pivot on four axes the orchestrator cannot override:

1. **Random full-variant selection** — `sampler.py:170`: `full_variant_idx = np.random.randint(0, len(FULL_SYS_FORMATS))`. Of the 5 variants, only `FULL_SYS_FORMAT_DIFFERENT` (`prompts_full.py:35–59`, index 1) says "Ignore the current implementation." `FULL_SYS_FORMAT_STRUCTURAL` (index 3) is the next-most pivot-friendly. The other 3 are improvement/keep-core-concepts framed. No parameter pins it.
2. **Improvement-framed iter msg** — `FULL_ITER_MSG` (`prompts_full.py:164–182`) is applied every full gen (`sampler.py:207`): "the current program we are trying to improve … improved internal implementation."
3. **Weak direction framing** — `sampler.py:135–141` renders the direction under "# Potential Recommendations / The following are potential recommendations," demoting even a maximal "Replace … ENTIRELY" direction.
4. **island_brief overwrite** — `sampler.py:96–97`: `if island_brief: meta_recommendations = island_brief`. **(See the load-bearing finding below.)**

`manual_ground.py` forced `patch_types:['full'],[1.0]` but did **not** pin a variant and routed the direction through `meta_recommendations` under the weak header → `steiner_tokens=0`, score 0.0. `grounding_direction.txt` itself was maximally strong ("Replace … ENTIRELY"), proving the dilution was the scaffolding, not the text.

**LOAD-BEARING FINDING (adversarial verdict A1 upheld — promoted from open question to MANDATORY).** The island_brief overwrite is not optional to fix. Verified data path: a grounding run pins `evo.grounding_parent_id` (`run_window.py:544–547`) to a parent on an EXISTING island. `sample_parent` returns `sampled_direction`; `run_window.py:642–644` sets `brief_text = _sampled_dir`; it is passed as `island_brief` (`run_window.py:673`). Default `brief_compose_mode="replace"` leaves `island_brief` set (`construct_mutation_prompt.py:125–131` only merges under `"augment"`). Then `sampler.py:96–97` **overwrites** the orchestrator-authored grounding direction (which arrives via `evo.meta_directions` → `_compose_meta_for_gen` → `meta_recommendations`) with the island's auto-generated brief. Because the per-window meta round runs every window BY DEFAULT, the pinned island almost always has a brief, so **the grounding direction is silently discarded** — and under the new `grounding_mode`, the mandate header would wrap the WRONG text. **The fix is inert unless this overwrite is suppressed under grounding_mode.**

**Fix (grounding path only, default-preserving).** Add optional `full_variant` + `grounding_mode` to `sampler.sample()`, thread them through `construct_mutation_prompt.py` and `run_window.py` as `evo.grounding_full_variant` + `evo.grounding_mode`, and suppress the island_brief overwrite under grounding_mode. Mirrors the existing `grounding_parent_id` / `mutation_web_search` lever pattern. Pair with the grounding subagent (1c/1g), which sidesteps the model prior + parent anchor entirely.

**Precedence (identical in `sampler.py` and `construct_mutation_prompt.py`):** explicit `full_variant` wins; else `grounding_mode=True` defaults `full_variant` to `"different_algorithm"`; else random (byte-identical to today). `grounding_full_variant` warn-rejects the 3 non-pivot variants (accepts only `different_algorithm` / `structural_redesign`, fail-soft to `different_algorithm`).

**Mutability classification (adversarial verdict A1 upheld — `run_window.py` reclassified).**
- `shinka/core/sampler.py`, `shinka/prompts/prompts_full.py`, `shinka/prompts/__init__.py` → **framework (between-runs)**. CLAUDE.md:188 "Edit `shinka/...` directly" sanctions this; they are NOT in the locked foundation set.
- `orchestrator/scripts/construct_mutation_prompt.py` → **mutable-strategy** (its own docstring: "MUTABLE STRATEGY (cell C)"). Additive optional INPUT keys keep the FOUNDATION JSON OUTPUT contract (`ok/patch_sys/patch_msg/patch_type`) byte-compatible.
- `orchestrator/harness/run_window.py` → **harness plumbing, between-runs framework patch** — NOT a "mutable-lever" the orchestrator flips mid-run (its docstring l19–20 and SKILL.md:800 forbid that). The MUTABLE LEVER is the `evo.grounding_full_variant` / `evo.grounding_mode` **config knob**; the file edit that threads it is a developer between-runs patch.

**Synergy reconciliation (must accompany 1h).** SKILL.md:798–801 (never edit `scripts/` without the rewrite cycle; `harness` is FOUNDATION) would, unreconciled, contradict the 1h edits to `construct_mutation_prompt.py` and `run_window.py`. The plan frames these as **between-runs framework/strategy patches applied by a developer**, distinct from forbidden orchestrator mid-run edits. A one-line note is added near SKILL.md:798–801 to that effect.

**[REMOVED per Addendum v2 / Point 5.]** _(This paragraph reasoned about the no-spoil `_EVAL_TEXT_KEYS` gate and the dropped `grounding_mode`; both are gone — the spoil apparatus is deleted (Point 5) and grounding no longer enters the sampler (1h dropped). Leak-proofing is the evaluator's job at task setup.)_

---

## Point 2 — Never kill an external call before timeout (no `@medium` prescription) + cost-asymmetry exception

**Root cause (verified in `_azure.py`).** Cost is computed by `_usage_cost` ONLY after the poll loop reaches a terminal status — `_azure.py:165` (incomplete/cap-hit), `:171` (`err.cost` on terminal failure), `:173` (completed). The loop is `while status not in _TERMINAL` (`:144`). A kill before terminal hits **none** of those branches, so the framework logs $0 while Azure has already billed the server-side work. The 3600s monotonic wall (`_POLL_TIMEOUT_SEC`, `:29`) is the only sanctioned end and where cost is captured — it is FOUNDATION and correct. The cnot run's slow `@high` calls (25–35 min) were normal output-bound behavior; the agent's manual kills leaked spend and forced a +$7 hand-rolled ledger recovery (ENDING.md:42, 47).

**Teaching (DOC-only).** Never manually kill a slow in-flight Azure bg-poll call (mutate/meta/DR) before the 3600s wall — it is pure wasted money. If a call is slow, decide for yourself how to handle it with the available knobs **on the NEXT launch** — never kill the in-flight call. **Do NOT prescribe `@medium`** (owner: the agent already knows the knobs and decides for itself). A single call is already cost-bounded by the per-model `max_output_tokens` cap (`azure-gpt-5.5` ≈ $6 max; pro ≈ $9 max — `_azure.py:47–50`), so letting it ride is safe. Strike every `@medium` prescription the v1 plan carried (2A/2C/2E).

**Scope clause (must appear at every no-kill mention).** This is the **Azure bg-poll CALL only** — the sanctioned `run_window`/measure-window kill + `--resume` recovery (CLAUDE.md:138–150; SKILL.md:541) is a different thing and stays allowed. Without this clause the no-kill rule collides with the resume pattern the docs depend on.

**Pair with 1g.** Also teach: never hammer Azure with more mutate calls on a refused verified pivot — switch to the grounding subagent.

### The single cost-asymmetry exception (both Claude powers), in lockstep

The two new RARE Claude powers contradict the absolute rule at CLAUDE.md:34–37, SKILL.md:35–39, SKILL.md:803, SKILL.md:812. **ONE carve-out lands identically at all four anchors**, scoped so it is NOT license for per-window Claude mutations, citing the boundary `mutate.py:8–10` ("All LLM usage here goes to Azure"):

- **(a) DISCOVERY** via the `archive-analyst.md` subagent in place of an Azure DR call.
- **(b) GROUNDING** via the `grounding-engineer.md` subagent (Claude authors the code) on a refused verified pivot, injected via evaluate → archive_record → spawn_island.

Both are ONE intervention class for the stagnation-intervention-exhaustion streak (same as an Azure DR/grounding); the AUTOMATIC per-window meta round still does NOT count. The carve-out is explicitly NOT the per-window mutation/fix loop, which ALWAYS goes to Azure via `scripts/`.

---

## Point 3 — Literal user-stop only (DOC-ONLY)

**Root cause (verified).** Guard asymmetry the docs codify: `budget_exhausted` and `stagnation_intervention_exhausted` are HARNESS-computed from durable journal data and auto-finalized (`run_window.py:1462–1471` stagnation; `:1535–1539` budget). `stopped_by_user` has NO harness path — `finalize_run` (`journal.py:376–382`) does `run["status"]=status` with zero validation/provenance, reachable via the CLI view (`journal.py:679–681`) that passes `payload["status"]` verbatim. The agent confabulated a "wrap it up now" message and self-finalized a healthy run (ENDING.md:3–4: "Status: stopped by user … Termination reason: user said stop." with NO quote, ~$24 unspent, `termination_streak=2`). Owner declined a code gate (v1's 3E–3I dropped); the fix is doc teaching alone.

**Streak fact (corrected, from `termination_streak` at `journal.py:475`).** The streak walks `control_return` rows in REVERSE counting trailing stagnant+intervened rows: window 9 (+1) → window 4 (+1) → window 3 (non-stagnant → break) = **2**, far below the default 5.

**Teaching (DOC-only, identical at SKILL.md:594 + CLAUDE.md:48–54).** Exactly THREE criteria, no others: (1) `budget_exhausted` [HARNESS, never call `finalize_run`]; (2) `stagnation_intervention_exhausted` [HARNESS, never call `finalize_run`]; (3) a LITERAL user stop message typed in the live conversation [agent finalizes BY HAND, only with a quotable user turn]. NEVER finalize from an inferred / remembered / assumed / summarized / "it feels done" / "we should wrap up" / "~$24 is enough" signal. The ending document's "Termination reason" must QUOTE the literal user turn or NAME the harness criterion. State plainly: `finalize_run` enforces NONE of this in code (owner declined a gate) — the discipline is entirely the agent's. If stuck with no real stop and no harness criterion, keep launching the next cluster or ASK and wait.

**Stale-comment caveat (do NOT echo it).** `run_window.py:1531–1534` says "User-stop / five-in-a-row terminations are the agent's judgment and call `finalize_run`," but five-in-a-row is HARNESS-auto-finalized at `:1468`. The docs must be MORE correct than that comment (five-in-a-row = HARNESS-finalized). The comment is inside FOUNDATION termination logic — do NOT edit it; just don't echo its error.

**Conditional archive-analyst touch — confirmed UNNEEDED.** A grep of `archive-analyst.md` found no "wrap up / finalize / looks done" language (only "one pass, then stop," about the subagent's own pass). v1's conditional 3-edit is a **no-op**; not applied. (`grounding-engineer.md`'s handoff verdict is scoped to "ready for archive_record" / "re-triage," never a run-stop — no risk there either.)

---

## Cross-cutting synergy: the shared vocabulary reconciled across CLAUDE.md / SKILL.md / subagents

Apply the overlapping regions as ONE merged pass, not independent overwrites. Enforced invariants (post-apply greps in Verification):

1. **Cost-asymmetry exception** reads identically at CLAUDE.md:34–37, SKILL.md:35–39, SKILL.md:803, SKILL.md:812; all cite the `mutate.py:8–10` boundary; covers BOTH Claude powers as one class; explicitly NOT the per-window loop.
2. **THREE PATHS** vocabulary (NOVEL / SIMILAR-TO-EXISTING / USELESS) is identical in SKILL.md:478–486 (canonical), the discovery subsection, `archive-analyst.md`, `grounding-engineer.md`, CLAUDE.md:14. No "History-similar" survives.
3. **Discovery-source-agnostic** (Azure DR call ↔ `archive-analyst.md`) and **grounding-source-agnostic** (Azure grounding call ↔ `grounding-engineer.md`) are siblings: "the framework treats the result identically regardless of who produced it." The path determines the action (ground+island vs combine), not which grounder runs it.
4. **No-kill** rule scoped "Azure CALL only; `run_window` kill + `--resume` allowed" at every mention; no `@medium` prescription anywhere.
5. **Termination** — exactly three criteria, "no code gate," literal-quote requirement read identically at all four doc anchors.
6. **Counting** — a Claude discovery pass and a Claude grounding injection each count as ONE intervention class; a single discovery pass that triages N ideas and grounds several is still ONE `control_return` row (do not imply each grounded idea is its own intervention). The automatic per-window meta round never counts.
7. **Filenames/titles** the cross-references depend on: subagents live at `.claude/skills/shinka-orchestrator/subagents/{archive-analyst,debug-agent,grounding-engineer}.md`; the SKILL.md grounding fallback subsection is titled **"Claude-authored grounding (structural-pivot fallback)"**; the discovery subsection is **"Two ways to do the DISCOVERY role."** Use these exact strings everywhere so no cross-reference dangles.

---

## The NEW grounding subagent — FULL FILE

Save verbatim to `C:/Users/dtlic/Documents/GitHub/ShinkaEvolve/.claude/worktrees/wizardly-booth-ad6a2d/.claude/skills/shinka-orchestrator/subagents/grounding-engineer.md`:

````markdown
---
name: grounding-engineer
description: Author a correct, working EVOLVE-BLOCK program for a VERIFIED-MISSING technique when the inner-loop Azure mutation model refuses the structural pivot (a strong seed-family prior keeps reverting it — e.g. KMS-vs-Steiner). This is the grounding-CALL analogue of archive-analyst (the DR-CALL analogue): a Claude-native alternative to the Azure grounding mutation. You write the code yourself (you CAN write the pivot algorithm the Azure model would not), self-evaluate it, and hand back the scratch path + whether it scored correct. Spawn ONLY for a discovery-triaged pivot that is NOVEL (path i) or SIMILAR-TO-EXISTING worth combining (path ii) AND that the inner-loop Azure model has demonstrably refused to instantiate. RARE, agent-decision exception to "inner-loop LLM calls go to Azure" — NOT the per-window loop. You write ONE program to a SCRATCH path; you NEVER edit the user's initial.py.
tools: Read, Write, Bash, Grep
---

# Grounding Engineer (orchestrator subagent)

You are spawned by the Shinka orchestrator to do what the Azure **grounding call**
(mutate-with-direction) does — turn a technique + reference into WORKING CODE for THIS
task — when the Azure inner-loop model **refused the pivot** (a strong seed-family prior
reverted every attempt; the cnot run's KMS-vs-Steiner refusal). You ARE Claude: you can
author the algorithm the Azure model would not. You write ONE program to a SCRATCH path,
self-evaluate it, and report back — you do NOT archive/spawn it (the orchestrator does,
for parity). You NEVER touch the user's `initial.py`.

## What you are given (in the spawn prompt)
- The **verified-missing technique** + references (from a discovery pass — Azure DR OR
  `subagents/archive-analyst.md`), triaged as path (i) NOVEL or path (ii) SIMILAR-TO-EXISTING.
- The **task spec** + the score *shape* (`task_sys_msg`) — goal, hard constraints, what a
  correct program must satisfy. You author the pivot code freely — leak-proofing is the EVALUATOR's job at task setup (Point 5), not a prompt-hiding rule.
- The **clean seed/scaffolding** (`initial.<ext>`) — the EVOLVE-BLOCK markers + the fixed
  harness around them. For path (ii), also the closest existing program to combine into.
- The run dir, the absolute `evaluate.py` path, a SCRATCH dir to write into, and the
  per-eval `time` cap (thread it from the live `run.json`'s `task.eval_time` if given;
  otherwise use the run's default).

## How to author + verify
1. **Read** the seed to find the exact EVOLVE-BLOCK markers and the I/O contract the
   harness expects. Your code must drop in between those markers, unchanged elsewhere.
2. **Write** the full candidate to a **SCRATCH path** (the seed with YOUR authored
   EVOLVE-BLOCK substituted) — e.g. `<scratch>/grounded.py`. NEVER write over `initial.py`.
3. **Self-evaluate** (no Azure call): pipe
   `{"program_path":"<scratch>/grounded.py","eval_program_path":"<task>/evaluate.py","results_dir":"<scratch>/results","time":"<eval_time>"}`
   to `python orchestrator/scripts/evaluate.py`. Read back `correct`, `combined_score`,
   `timed_out`, `text_feedback`.
4. **Iterate up to 3 times** on `correct:false` — read `text_feedback`/`error_traceback`,
   fix the EVOLVE-BLOCK, re-evaluate. You write the fixes (off-ledger Claude tokens) — do
   NOT fall back to Azure `mutate.py` for the pivot; that already refused.
5. Stop at the first `correct:true`, or after 3 failed evaluations.

## What to output (a short report, < 400 words)
Return Markdown with exactly these sections:
- **Technique grounded** — one line: the algorithm you implemented + the path (i/ii).
- **Scratch path** — the absolute path of the program you wrote.
- **Verification** — `correct`, `combined_score`, `timed_out`, and the `text_feedback`
  tail. State plainly if it scored 0.0 / below baseline — that is EXPECTED on a first
  structural injection (a brand-new structural family rarely beats a tuned incumbent on
  its first shot); say so, do not call it a failure.
- **Parent for grounding** — `null` for path (i) NOVEL (gets its OWN island); the closest
  program id for path (ii) SIMILAR-TO-EXISTING (combine-into).
- **Handoff** — one line: "ready for archive_record + spawn_island" (correct) OR "could
  not instantiate after 3 tries — recommend re-triage / re-scope" (incorrect). This is NOT
  a run-stop signal — you never authorize a termination.

## PARITY — what the orchestrator does with your result (identical to an Azure grounding output)
Your correct program is handled EXACTLY as a successful Azure grounding mutation:
embed → `archive_record` → `spawn_island`. (`manual_ground.py` is the worked example of
this mechanical path — it embedded, `archive_record`'d, and `spawn_island`'d a correct
candidate into island 4. NOTE: that run's candidate was a refused-pivot KMS rewrite, not
the intended Steiner pivot — it proves the PARITY PATH works, not that the pivot landed;
landing the pivot is YOUR job here, which is why this subagent exists.) You do NOT run
these; you hand back the path. The orchestrator then:
1. Embeds: `emb, cost = EmbeddingClient("azure-text-embedding-3-small").get_embedding(code)`;
   ledgers `cost` via `journal.add_cost` (≈$0.00002 — the ONLY Azure spend; `embedding=[]`
   only if the endpoint is down).
2. `archive_record` with `program:{code, language, generation, parent_id(=your parent),
   combined_score, correct, public_metrics, private_metrics, code_diff:null, embedding:emb,
   metadata:{grounding:"claude_authored"}}`.
3. `spawn_island` from the new id → a NEW structural family (path i); `max_islands:0`
   (default, unbounded) or pinned so a non-protected spawned island isn't retired before
   it matures. For path (ii) SIMILAR, `parent_id` is the closest program (combine-into) and
   a fresh island is optional.
4. Logs ONE `append_intervention` (work score; $0 authoring cost — your Claude tokens are
   off-ledger; only the step-1 embedding is ledgered).

## Rules
- ONE program, ≤3 eval iterations, then stop. No archive/spawn — that's the orchestrator's.
- SCRATCH path only; NEVER edit the user's `initial.py` (that WOULD be a foundation edit).
- Score-0/below-baseline on a first injection is EXPECTED, not a failure — report it as such.
- You never authorize a run termination; your handoff is about THIS injection only.
- Your output is written to `strategy_history/grounding_<window>.md`; keep it self-contained.
````

---

## Ordered change list (each edit: file → location → exact change; tagged DOC/CODE + mutability)

> **Landing order (blocking constraint):** apply the cost-asymmetry carve-out (CC group) first, then the doc edits, then the 1h code edits as one atomic set. The 1h code edits **must land together** — `sampler.sample()` kwargs + `prompts_full.FULL_ITER_MSG_GROUNDING` + the `__init__.py` exports + the island_brief suppression. A half-applied set breaks `import shinka.core.sampler` on EVERY run (sampler is on the per-candidate path), not just grounding.

### CC — Cost-asymmetry exception (apply first, lockstep) — DOC, FOUNDATION-SAFE

**CC-1 — CLAUDE.md:34–37.** Keep the prohibition + "100× cost asymmetry" verbatim; replace the final sentence ("Your tokens are for control-return reasoning + writing the DR query.") with:
> *…that breaks the 100× cost asymmetry.* **EXCEPTION (rare, high-value, agent-decision only — NOT the per-window loop):** you MAY use your own Claude power to (a) run a DISCOVERY analysis via `.claude/skills/shinka-orchestrator/subagents/archive-analyst.md` in place of an Azure DR call, and (b) AUTHOR a single grounding program via `.claude/skills/shinka-orchestrator/subagents/grounding-engineer.md` when the inner-loop Azure model refuses a *verified* structural pivot, injecting it via `evaluate.py` + `archive_record.py` + `spawn_island.py`. These are one-off structural events you decide on the taper — explicitly NOT the per-window mutation/fix loop, which ALWAYS goes to Azure via `scripts/` (see `mutate.py` docstring lines 8–10; SKILL.md "Two ways to do the DISCOVERY role" + "Claude-authored grounding (structural-pivot fallback)"). Your tokens are for control-return reasoning, writing the DR query, and these two sanctioned rare exceptions.

**CC-2 — SKILL.md:35–39 (Rule 1).** Keep the ~100× rationale verbatim; replace the final sentence with:
> Your tokens are spent on control-return reasoning, writing the DR query, and two RARE carved exceptions on the taper: a Claude-native DISCOVERY analysis (`subagents/archive-analyst.md`) in place of an Azure DR call, and a single grounding program authored by `subagents/grounding-engineer.md` on a refused *verified* pivot. Both are one-off agent decisions — NOT the per-window inner loop, which never comes to you (boundary: `mutate.py` docstring lines 8–10).

**CC-3 — SKILL.md:803.** Replace:
> Never run the **per-window** inner mutation/fix loop in your own context — always call `mutate.py`. The ONLY exceptions are the two rare Claude powers: DISCOVERY via `subagents/archive-analyst.md` and a single structural-grounding seed via `subagents/grounding-engineer.md` when the Azure model refuses a verified pivot — one-off injections, never the per-window loop.

**CC-4 — SKILL.md:811–813 ("When in doubt").** Replace "…and the rare DR call that brings in knowledge the search can't invent.":
> …and the rare DISCOVERY pass (Azure DR, or your own Claude analysis via `subagents/archive-analyst.md`) that brings in knowledge the search can't invent, plus the rare Claude-authored grounding seed (`subagents/grounding-engineer.md`) for a verified structural pivot the Azure model refuses. Never kill a slow in-flight Azure bg call to end it sooner.

### Point 1a edits — DOC, FOUNDATION-SAFE

**1a-1 — CLAUDE.md:109.** Delete the span from "**CONFIRMED failure mode (2026-06-10…**" through "…so the ledger reflects the spend." Keep the lead (deployment / Foundry / `web_search_preview`) verbatim. Insert in its place:
> DR works — the deployment quota is 30,000,000 TPM / 30,000 RPM (raised 2026-06-16), comfortably above what a full deep-research job needs (a single job internally fires many large reasoning+search calls over 30–60 min). Use it when the search is stuck on an *algorithmic* gap and you want fresh, web-cited SOTA (author/year/arXiv) — see SKILL.md "Deep research" for when/how. `python scripts/test_dr.py` is a quick endpoint probe (prints `error.code`/`message`/`incomplete_details.reason` if it doesn't answer). If DR ever *does* keep failing, do NOT loop-retry it — fall back to the Claude-native discovery path: spawn `.claude/skills/shinka-orchestrator/subagents/archive-analyst.md` to survey the archive and judge what technique is genuinely missing (it reads every island; a text-only DR brief cannot).

**1a-2 — SKILL.md:464–476.** Delete the span from "**The CONFIRMED failure mode on this setup is `error.code='too_many_requests'`**" through "…do not loop-retry it." Keep SKILL.md:462–464a ("A server-side terminal `failed` is NOT a content_filter … surfaced by `run_dr_call`.") verbatim. Insert:
> The DR deployment quota is 30,000,000 TPM / 30,000 RPM (raised 2026-06-16), comfortably above a full deep-research job (which internally fires many large reasoning+search calls over 30–60 min), so a real job completes. If a job *does* return a server-side `failed`, read the cause off `error_code` — typical ones are a missing/blocked `web_search_preview` tool on the resource or a wrong deployment name / model-version; `scripts/test_dr.py` is a quick endpoint probe. Whatever the cause, NEVER loop-retry a heavy failed DR — diagnose it, or take the Claude-native discovery path instead.

### Point 1b + 1e/1f + 1g discovery edits — DOC, FOUNDATION-SAFE

**1bf-1 — SKILL.md:434.** Replace "DR is web-grounded *discovery* (find SOTA), not *instantiation* (write the code)." with:
> DR's WEB-CITATION job is web-grounded *discovery* (find SOTA, with author/year/arXiv refs), not *instantiation* (write the code). The DISCOVERY judgment itself you can ALSO do natively in Claude over your own archive — see "Two ways to do the DISCOVERY role" below.

**1bf-2 — SKILL.md: insert after the "When." paragraph (after line 448), before "How to write the DR query".** New subsection:
> **Two ways to do the DISCOVERY role.** DR has two separable jobs: DISCOVERY (survey what the archive already tried; judge what technique is genuinely missing/novel) and WEB-CITATION (author/year/arXiv references). Azure DR does both in one paid call. For DISCOVERY you have a sanctioned Claude-native alternative — and it is the right fallback if DR keeps failing: spawn `subagents/archive-analyst.md` (your structural-read vehicle) and/or reason directly over the archive (`archive_query` `top_n`/`recent_failures`, the directions in `evo.meta_directions`) plus your own literature knowledge. You read every island; a text-only DR brief cannot.
> **Prefer Azure DR when** you need fresh, citable web references you can't supply yourself or SOTA published after your knowledge cutoff. **Prefer Claude-native discovery when** the question is "what has my archive NOT tried / is X genuinely novel here," or when DR is unavailable.
> **Cost/ledger.** The Claude analysis TURN costs Claude tokens, NOT Azure dollars — invisible to the `budget_usd` ledger. Do NOT `append_intervention` a fabricated dollar cost for it. "Free" applies to the analysis turn ONLY — any follow-on grounding run (Azure mutate / pro+web-search) is normal Azure spend.
> **Triage its output through the THREE PATHS exactly as an Azure DR brief — see "Triage discovery output — the THREE PATHS" below.** The discovery SOURCE (Azure DR call vs Claude subagent/agent) changes NOTHING about triage.
> **Streak/cap.** A Claude-native discovery pass counts as ONE intervention class for the termination streak, same as an Azure DR. The "twice per stagnation cluster" cap (below) bounds the **paid Azure DR call**; a single Claude-native discovery pass per cluster is separately bounded.

**1bf-3 — SKILL.md:478–486 (the canonical THREE-PATHS block; supersedes v1 1D + 1G's triage halves as ONE merged pass).** Replace the entire "**Triage the returned brief — per technique, deliberately:**" block with:
> **Triage discovery output — the THREE PATHS (authoritative; canonical definition).** A discovery pass (Azure DR OR a Claude subagent/agent — see "Two ways to do the DISCOVERY role") returns MULTIPLE ideas. Handle the result IDENTICALLY regardless of source, and triage EVERY idea ONE-BY-ONE — never just the single "best" one (the cnot_grid_synth run grounded only one idea and dropped the rest; that was wrong). Map each idea to exactly one path:
> - **PATH (i) — NOVEL** (no archived program AND no prior direction implements it): GROUND it (via the grounding run below OR `subagents/grounding-engineer.md`), then give it its OWN island via `spawn_island.py` so it isn't out-competed before it matures. Use `fix_retry_budget:3`. Log `work_dr:3`.
> - **PATH (ii) — SIMILAR-TO-EXISTING** (resembles, extends, or restates something already in the archive): STILL VALUABLE — do NOT discard it. COMBINE it into the closest existing program via the grounding run (`fix_retry_budget:1`), TARGETING that program with `evo.grounding_parent_id:"<id>"` (H9 — pins the parent + its island; without it the grounding mutation lands on an arbitrary sigmoid-sampled parent; use `evo.grounding_island_idx` to pin only the island). Log `work_dr:2`.
> - **PATH (iii) — USELESS** (off-task, already-tried-and-failed-for-a-known-reason, an EXACT duplicate of an already-elite program, or it cannot be made correct for this task): ignore it. Only this path drops an idea. Log `work_dr:1` if the pass produced nothing else groundable.
>
> **Critical anti-rejection rule.** "Similar to an existing program" / "looks like a renamed version of existing code" is PATH (ii) (COMBINE), NOT a kill. An idea is dropped ONLY if it is genuinely useless (path iii) — never merely for overlapping with the archive. The cnot_grid_synth run violated this: an adversarial pass killed the real 4n block-KMS / Steiner-tree-GE pivot as "the dead 2D-mesh renamed" (it conflated "resembles a dead island" with "is the dead island"), throwing away the single highest-value idea. Resemblance to a DEAD/failed program is still path (ii) unless you can name the SAME concrete failure cause that will recur.
>
> **If you run an adversarial verification step** (in `archive-analyst`, a Claude discovery workflow, or your own reasoning), its ONLY job is to assign each idea to one of the three paths — NOT to reject ideas for being unoriginal. It MUST NOT output "reject"/"kill" merely because an idea is "similar to existing"; that verdict is PATH (ii). The only verdict that drops an idea is "genuinely useless for this task" (path iii), with a concrete reason (off-task, exact-duplicate, or a SPECIFIC recurring correctness/perf failure) — not a similarity judgment. When in doubt between (ii) and (iii), choose (ii): a wasted small COMBINE run is far cheaper than discarding the pivot that breaks the plateau.
>
> Either grounder can instantiate a path-(i) or path-(ii) idea: the Azure grounding call (below) OR `subagents/grounding-engineer.md` (use the subagent once the Azure model has demonstrably refused a verified pivot — see "Claude-authored grounding"). The path determines the action (ground+island vs combine), not which grounder runs it.

**1bf-4 — SKILL.md:130–131 (`work_dr`).** Replace "`work_dr` — DR magnitude:" with "`work_dr` — **DISCOVERY magnitude (from EITHER Azure DR OR a Claude-native archive analysis)**:" keeping the 0/1/2/3 scale; append: "(aligned with the three paths: path-(iii)-only → 1, best idea path (ii) → 2, path (i) → 3)."

**1bf-5 — SKILL.md:804.** Replace "Never call deep research twice per stagnation cluster." with "Never call the **paid Azure** deep-research twice per stagnation cluster (a single Claude-native discovery pass per cluster is the separate bound)."

**1bf-6 — SKILL.md:589–590.** Replace "For periodic structural reads, spawn `subagents/archive-analyst.md`." with:
> For periodic structural reads — and as the Claude-native DISCOVERY alternative to an Azure DR call (see "Two ways to do the DISCOVERY role") — spawn `subagents/archive-analyst.md`; triage its per-idea output through the THREE PATHS and ground a chosen idea via the Azure grounding call or `subagents/grounding-engineer.md`.

**1bf-7 — CLAUDE.md:14.** Replace "DR queries, triage briefs, spawn/ground islands)" with "DR queries, triage discovery output per-idea into the three paths — NOVEL / SIMILAR-TO-EXISTING / USELESS — regardless of source, spawn/ground islands)".

### Point 1c/1d wiring + archive-analyst three-paths reframe — DOC, FOUNDATION-SAFE

**1cd-1 — SKILL.md: insert a new subsection "Claude-authored grounding (structural-pivot fallback)" immediately after the `spawn_island.py` paragraph (after line 505).**
> **Claude-authored grounding (structural-pivot fallback).** When the inner-loop Azure model has demonstrably REFUSED a *verified* structural pivot — it reverts to the seed family despite a "replace X with Y entirely" direction (exactly KMS-vs-Steiner in the cnot run) — that is a strong-prior wall no text direction fixes. STOP re-firing Azure mutations (Point 2: do not hammer Azure). Two complementary responses:
> 1. **Strengthen the Azure grounding prompt-mode (try this first).** Set `evo.grounding_full_variant:"different_algorithm"` (or `"structural_redesign"`) to PIN the pivot variant — else `sampler.py` picks 1 of 5 full variants at random and 4 are improvement/keep-core-concepts framed — AND `evo.grounding_mode:true` to reframe the direction under a "# Required structural replacement" MANDATE (a required replacement, not a "potential recommendation"), swap in a replacement-framed, parent-de-anchoring iter message, and ensure the grounding direction wins over the pinned island's auto-brief. (Mechanics: SKILL.md "framework prompt-mode levers" and `sampler.sample(full_variant=…, grounding_mode=True)`.)
> 2. **Author it with the grounding subagent (when the prior still refuses).** Spawn `subagents/grounding-engineer.md` with the verified-missing technique + references, the `task_sys_msg`, the clean seed, and a SCRATCH dir. It authors the EVOLVE-BLOCK, self-evaluates via `evaluate.py`, and hands back the scratch path + `correct`/score. You then run the parity steps on its result — exactly as for an Azure grounding output. It is to the Azure grounding CALL what `archive-analyst.md` is to the Azure DR CALL.
>
> **Parity recipe (orchestrator runs this on a correct result — Azure-call OR subagent, identically; the mechanical path is the one `manual_ground.py` exercised):**
> 1. **Embed:** `emb, cost = EmbeddingClient("azure-text-embedding-3-small").get_embedding(code)` (≈$0.00002); ledger via `journal.add_cost(results_dir, cost)`. `embedding=[]` ONLY when the endpoint is down (disables the novelty gate for the seed).
> 2. **archive_record:** pipe `{db_path, db_config, embedding_model, program:{code, language, generation, parent_id, combined_score, correct, public_metrics, private_metrics, error_traceback:null, code_diff:null, embedding:emb, metadata:{grounding:"claude_authored"}}}` to `archive_record.py` (no embedding **API call** fires — local `EmbeddingClient` + local sklearn maintenance only; net Azure $0).
> 3. **spawn_island:** pipe `{db_path, db_config, embedding_model, program_id}` to `spawn_island.py` → its own island (path i). `max_islands:0` (default, unbounded) or pin so the seed isn't retired at the cap before it matures.
> 4. **Log** ONE `append_intervention` (work score; $0 authoring cost — Claude tokens off-ledger; only the step-1 embedding is ledgered).
>
> **A first injection that scores 0.0 / below baseline is EXPECTED, not a failure** — a brand-new structural family rarely beats a tuned incumbent on its first shot; the value is seeding it on its own island for the inner loop to refine. Do not revert it as "didn't work." **FOUNDATION LINE:** injecting via `evaluate.py` + `archive_record.py` + `spawn_island.py` is NOT a foundation edit; EDITING the user's `initial.py` to inject it IS — never do the latter mid-run.

**1cd-2 — archive-analyst.md:3 (description).** Replace "Returns a one-page summary; it does not modify the archive." with:
> Returns a one-page summary including a per-idea THREE-PATHS triage (NOVEL / SIMILAR-TO-EXISTING / USELESS) of any algorithmic gaps it finds; it does not modify the archive. Its structural read may itself BE the discovery (feeding straight into a grounding run + `spawn_island`), not merely a prelude to Azure DR.

**1cd-3 — archive-analyst.md:33–37.** Replace the "Unexplored regions" + single "Recommendation" sections with:
> - **Unexplored regions + idea list** — what kinds of approaches are absent from / under-explored in the archive that the problem likely needs? List EACH candidate idea separately (reason from the code you sampled).
> - **Per-idea THREE-PATHS triage** — for EVERY idea above, assign exactly one path and say why: **NOVEL** (no archived program implements it → ground it + its own island), **SIMILAR-TO-EXISTING** (resembles/extends something in the archive → STILL VALUABLE: combine into the closest program via a grounding run — name the closest `program_id`), or **USELESS** (off-task, an exact duplicate of an elite program, or a SPECIFIC recurring failure cause → ignore). **You MUST NOT mark an idea USELESS merely because it is "similar to existing" / "a renamed version of existing code" — that is the SIMILAR-TO-EXISTING path (combine), not a rejection.** Resemblance to a DEAD/failed island is still SIMILAR-TO-EXISTING unless you can name the same concrete failure cause that will recur. (The cnot_grid_synth run killed the real Steiner-tree-GE pivot as "the dead 2D-mesh renamed" — exactly the mistake this rule forbids.) Triage ALL ideas, not just the single best one.
> - **Recommendation** — the single most useful STRUCTURAL intervention to take FIRST given the triage (e.g. ground the top NOVEL idea on a fresh island; combine the top SIMILAR-TO-EXISTING idea into program `<id>`; `island_policy: spawn fresh island`; `sample_parent: increase exploration`; seek fresh web-cited refs via Azure DR; or `no action`). This is the ordering call, not a license to drop the other triaged ideas — the orchestrator acts on the full per-idea triage. The orchestrator grounds a chosen gap via the Azure grounding call OR `subagents/grounding-engineer.md` when the Azure model refuses the pivot.

**1cd-4 — archive-analyst.md:39–43 (Rules).** Add two bullets after "Ground every claim in a query you actually ran.":
> - Triage EVERY idea you raise one-by-one into the three paths; never collapse to a single "best" idea and drop the rest.
> - NEVER reject an idea for being "similar to existing" / "a renamed version of existing code" — that is the SIMILAR-TO-EXISTING (combine) path. Drop an idea ONLY as USELESS, with a concrete off-task / exact-duplicate / recurring-failure reason.

**1cd-5 — NEW FILE.** Create `grounding-engineer.md` (full text in the section above).

### Point 1h edits

**1h-DOC-1 — CLAUDE.md "Patching the framework" (after line 188).** Append:
> The framework also supports a **grounding prompt-mode** for structural pivots: `sampler.sample(full_variant=…, grounding_mode=True)` (`shinka/core/sampler.py`), surfaced as `evo.grounding_full_variant` + `evo.grounding_mode`, pins the pivot-friendly full variant and demands a *required replacement* instead of the random improvement-framed default. Cross-ref SKILL.md "Claude-authored grounding (structural-pivot fallback)". `shinka/core/sampler.py` and `shinka/prompts/*` are framework code editable BETWEEN runs (not the locked foundation); `construct_mutation_prompt.py` is a mutable strategy file.

**1h-DOC-2 — SKILL.md: insert "framework prompt-mode levers" within/adjacent to the grounding-run block (~488–505).**
> **Framework prompt-mode levers (grounding).** On a grounding run set `evo.grounding_full_variant:"different_algorithm"` (or `"structural_redesign"`) to PIN the pivot variant — otherwise `sampler.py` draws 1 of 5 full variants uniformly at random and only `different_algorithm` says "Ignore the current implementation"; the other 4 are improvement/keep-core-concepts framed (the cnot run's manual grounding got a random variant + weak rec header and produced another KMS variant, `steiner_tokens=0`, score 0.0). Set `evo.grounding_mode:true` to render the direction under a "# Required structural replacement" MANDATE, swap in a replacement-framed iter message that treats the shown program as the interface contract only (NOT something to improve), and ensure the grounding direction — not the pinned island's auto-brief — is what gets mandated. Both default unset (normal windows are byte-identical). If the model prior still refuses, switch to `subagents/grounding-engineer.md` (do not hammer Azure — Point 2). NOTE: the `sampler.py`/`prompts_full.py`/`construct_mutation_prompt.py`/`run_window.py` edits that implement these levers are **between-runs framework/strategy patches** (CLAUDE.md "Edit `shinka/...` directly"), distinct from forbidden orchestrator mid-run rewrites of harness/`scripts/` files — see "What never to do".

**1h-DOC-3 — SKILL.md:798–801 (reconcile the never-edit lines with the 1h code edits).** Append a clause to the "Never modify a `scripts/` file …" + "Never edit FOUNDATION files …" lines:
> (NOTE: the grounding prompt-mode levers — `sampler.py`, `prompts_full.py`, `prompts/__init__.py`, and the mutable-strategy `construct_mutation_prompt.py` + harness plumbing in `run_window.py` — are **between-runs framework/strategy patches** a developer applies from the fix plan, NOT orchestrator mid-run edits; they are not the locked FOUNDATION. The FOUNDATION set — schema, JSON contract, `evaluate`/`archive_record`/`archive_query`/`diagnostics`/`repair_record`/`journal`/the rest of the harness, `cadence_policy.py`, the termination logic, `_azure.py`'s wall, the task's `evaluate`/`init` — is still never touched.)

**1h-DOC-4 — construct_mutation_prompt.py docstring INPUT block (lines 15–38).** Add two documented keys and note default-preservation:
> `"full_variant": str | null,  # grounding lever: pin a full-rewrite variant by name (one of prompts_full.FULL_SYS_FORMAT_NAMES; pivot-friendly: "different_algorithm" / "structural_redesign"); null = today's random pick`
> `"grounding_mode": bool,       # grounding lever (default false): mandate the direction as a REQUIRED replacement + replacement-framed iter msg + suppress the island_brief overwrite of the direction`
> Add to the policy paragraph: "Under `grounding_mode`, if the caller did not pin `patch_types`, force `patch_types=['full'], patch_type_probs=[1.0]`. `full_variant=null` + `grounding_mode=false` is byte-identical to today (same RNG draw, same '# Potential Recommendations' header, same `FULL_ITER_MSG`). The OUTPUT contract (`ok/patch_sys/patch_msg/patch_type`) is unchanged — these are additive optional INPUT keys, FOUNDATION-JSON-safe."

**1h-CODE-1 — `shinka/prompts/prompts_full.py` (after line 182).** **CODE, framework (between-runs).** Add a grounding iter message with the SAME `.format` keys (`{language}, {code_content}, {performance_metrics}, {text_feedback_section}`) but a replacement framing that de-anchors from the parent:
```python
FULL_ITER_MSG_GROUNDING = """# Reference interface (do NOT improve or refactor this)

The program below is shown ONLY to define the interface contract — the same inputs,
outputs, and the EVOLVE-BLOCK-START / EVOLVE-BLOCK-END markers. Do NOT treat it as a
starting point to tune, extend, or refactor. You are REPLACING its algorithm.

```{language}
{code_content}
```

Reference performance (the bar to beat with a DIFFERENT algorithm):

{performance_metrics}{text_feedback_section}

# Task

Implement the REQUIRED replacement algorithm described in the system prompt as a
fundamentally different approach. Keep the same inputs/outputs and the EVOLVE-BLOCK
markers; do not preserve the reference algorithm's structure. Provide the complete new
program code.
""".rstrip()
```

**1h-CODE-2 — `shinka/prompts/__init__.py` (imports lines 9–13 + `__all__` lines 42–44).** **CODE, framework (between-runs).** Add `FULL_SYS_FORMAT_NAMES` and `FULL_ITER_MSG_GROUNDING` to the `from .prompts_full import (...)` block and to `__all__`.

**1h-CODE-3 — `shinka/core/sampler.py`.** **CODE, framework (between-runs).** Four coordinated changes:
- **Import (line 12–13):** add `FULL_ITER_MSG_GROUNDING` and `FULL_SYS_FORMAT_NAMES` to the `from shinka.prompts import (...)` block.
- **Signature (lines 77–85):** add `full_variant: Optional[str] = None, grounding_mode: bool = False,`.
- **island_brief overwrite (lines 96–97) — MANDATORY suppression:** change `if island_brief:` to `if island_brief and not grounding_mode:` so under grounding_mode the orchestrator-authored `meta_recommendations` (the grounding direction) is what survives.
- **Direction header (lines 135–141):** when `grounding_mode and meta_recommendations not in [None, "none"]`, emit a mandate header instead of "# Potential Recommendations":
  ```python
  sys_msg += "\n\n# Required structural replacement"
  sys_msg += ("\nYou MUST replace the existing algorithm with the approach below. "
              "Do NOT merely tune, refactor, or extend the current program — implement "
              "the required replacement as a fundamentally different algorithm:\n")
  sys_msg += f"\n{meta_recommendations}"
  ```
  else unchanged.
- **Variant pick (lines 168–172):** 
  ```python
  elif patch_type == "full":
      _names = FULL_SYS_FORMAT_NAMES
      _fv = full_variant
      if _fv is None and grounding_mode:
          _fv = "different_algorithm"
      if _fv is not None and _fv in _names:
          full_variant_idx = _names.index(_fv)
      else:
          if _fv is not None:
              logger.warning("unknown full_variant %r — falling back to random", _fv)
          full_variant_idx = np.random.randint(0, len(FULL_SYS_FORMATS))
      sys_msg += FULL_SYS_FORMATS[full_variant_idx]
  ```
- **Iter msg (lines 206–214):** under `grounding_mode`, use `FULL_ITER_MSG_GROUNDING.format(...)` instead of `FULL_ITER_MSG.format(...)` (same `.format` keys); else unchanged.

**1h-CODE-4 — `orchestrator/scripts/construct_mutation_prompt.py` (the non-fix branch, lines 118–139).** **CODE, mutable-strategy.** Read the two keys, force full patch-type under grounding_mode if not pinned, thread into `sampler.sample`:
```python
_grounding_mode = bool(payload.get("grounding_mode", False))
_full_variant = payload.get("full_variant")
if _grounding_mode and payload.get("patch_types") is None:
    payload["patch_types"] = ["full"]; payload["patch_type_probs"] = [1.0]
```
(apply BEFORE constructing `PromptSampler` at line 99 so the forced patch types take effect), then add to the `sampler.sample(...)` call: `full_variant=_full_variant, grounding_mode=_grounding_mode,`.

**1h-CODE-5 — `orchestrator/harness/run_window.py` (the `construct_mutation_prompt.main` payload, lines 660–685).** **CODE, harness plumbing (between-runs).** Add to the payload dict:
```python
"full_variant": evo.get("grounding_full_variant"),
"grounding_mode": bool(evo.get("grounding_mode", False)),
```
Default `None`/`False` on normal windows (byte-identical); set only on a one-window grounding override alongside `grounding_parent_id` / `mutation_web_search`.

### Point 2 edits — DOC, FOUNDATION-SAFE

**2-1 — CLAUDE.md: new bullet after the framework-audit bullet (after line 47).**
> - **Never manually kill a slow in-flight external Azure call (mutate/meta/DR) before its 3600s wall.** Cost books ONLY on a TERMINAL status (`_azure.py`), so a mid-flight kill leaks unlogged-but-BILLED spend (cnot run hand-rolled +$7). A 25–35 min call is normal output-bound behavior, not stuck; a single call is already cost-bounded by the per-model `max_output_tokens` cap (~$6 for gpt-5.5, ~$9 for pro). Let it ride. If a call is slow, decide for yourself how to handle it with the available knobs on the NEXT launch — never kill the in-flight call. (Azure CALL only — the sanctioned `run_window`/measure-window kill + `--resume` recovery is unaffected.) And never hammer Azure with more mutate calls on a refused verified pivot — switch to `subagents/grounding-engineer.md`.

**2-2 — CLAUDE.md: new bullet in "Things future agents should NOT do" (after line 202).**
> - Do not manually kill slow backgrounded Azure mutate/meta/DR calls — cost is captured only on a terminal status, so a kill leaks unlogged billed spend; let them ride the 3600s wall (the `run_window` kill + `--resume` recovery is different and allowed). On a refused verified pivot, switch to `subagents/grounding-engineer.md` rather than firing more Azure mutate calls.

**2-3 — SKILL.md: append two sub-paragraphs to the grounding-run block (after line 499).**
> **NEVER kill a slow grounding call mid-flight.** A grounding mutation can run 25–35 min (output-bound) — normal, not stuck. The foundational poll wall (3600s, `_POLL_TIMEOUT_SEC`) bounds it, and cost is read ONLY on a TERMINAL status, so a manually killed call leaks unlogged-but-BILLED Azure spend and forces a hand-rolled `ledger_recovery` intervention (cnot run: +$7). Let it ride to the wall; if a call is slow, handle it with the available knobs on the NEXT launch — do NOT TaskStop / bash-kill the in-flight call. A single call is already cost-bounded by the per-model `max_output_tokens` cap. (Azure CALL only — the `run_window`/measure-window kill + `--resume` recovery is a different thing and stays allowed.)
> **When the inner-loop Azure model REFUSES a verified structural pivot** — it reverts to the seed family despite a "replace X with Y entirely" direction (KMS-vs-Steiner) — no text direction or extra mutate calls fix it. STOP re-firing Azure mutations and switch to the grounding subagent (`subagents/grounding-engineer.md`), which authors the program to inject via the parity path (see "Claude-authored grounding"). NOT the per-window loop.

**2-4 — SKILL.md:673–679 (transport FOUNDATION paragraph).** Append:
> This 3600s wall is the ONLY sanctioned way an external Azure bg-poll call (mutate/meta/DR) ends — the orchestrator must NOT manually kill (TaskStop / bash-kill) a slow one to end it sooner. Cost is captured only on a TERMINAL status, so a killed call leaks unlogged-but-BILLED Azure spend (cnot run: +$7 hand-rolled `ledger_recovery_grounding_kills`). If a call is pathologically slow, handle it at the source on the NEXT launch with the available knobs — not with a kill. (Azure CALL only; the `run_window`/measure-window kill + `--resume` recovery is a different thing and stays allowed.)

**2-5 — SKILL.md:804–805 (never-do list).** Add two bullets:
> - Never kill a slow in-flight external Azure call (mutate/meta/DR) before the 3600s wall — cost books only on a terminal status, so a kill leaks unlogged billed spend (Azure CALL only; `run_window` kill + `--resume` allowed).
> - Never keep firing Azure mutate calls on a verified pivot the model has already refused — switch to `subagents/grounding-engineer.md`.

### Point 3 edits — DOC, FOUNDATION-SAFE (supersede v1 3A–3D; 3E–3I remain dropped)

**3-1 — SKILL.md:594–607 ("Stop when").** Replace the opener sentence (keep the existing harness-computed/auto-finalize explanation from "This is now harness-computed…" onward verbatim):
> **Stop ONLY when EXACTLY ONE of these THREE criteria is met — there are no others, and you invent none:** (a) **budget_exhausted** — HARNESS-decided and auto-finalized; you NEVER call `finalize_run` for it. (b) **stagnation_intervention_exhausted** — HARNESS-decided and auto-finalized: five consecutive control-returns each STAGNANT and each with an intervention (a framework rewrite, a DR, a Claude-native discovery pass, a Claude-authored grounding injection, OR a deliberate config-lever flip — the AUTOMATIC per-window meta round does NOT count) that still could not break the stagnation; you NEVER call `finalize_run` for it either. (c) **a LITERAL user stop message in the LIVE conversation** — a turn the user ACTUALLY TYPED in THIS session telling you to stop. Criterion (c) is the ONLY termination you finalize BY HAND, and you may write `stopped_by_user` ONLY when you can QUOTE that real user turn. NEVER finalize `stopped_by_user` from an inferred, remembered, assumed, summarized, paraphrased, or "it feels done / we should wrap up / the run is stuck / ~$24 is enough" signal — **confabulating a user stop is the single worst failure mode here** (the 2026-06-14 run threw away ~$24 of budget and a half-finished structural pivot exactly this way, at `termination_streak=2`, writing an ENDING.md that said "user said stop" with no quotable turn). If you feel stuck but the user has NOT literally said stop and neither harness criterion has fired, KEEP GOING — launch the next cluster — or ASK and WAIT for a real reply; do not self-stop. **There is NO code gate:** `finalize_run` (journal.py:376) writes whatever status you pass with no provenance check, so this discipline is ENTIRELY yours.

**3-2 — SKILL.md:615–622 ("End of run"), the finalize sentence.** Replace:
> Seed it from `journal.build_run_summary(results_dir)`. **You call `finalize_run` BY HAND only for criterion (c) (a literal user stop)** — `budget_exhausted` and `stagnation_intervention_exhausted` are auto-finalized by the harness (the budget-exhausted terminal return at run_window.py:1535–1539 and the stagnation return at :1462–1471); **never call `finalize_run` with those two statuses.** Before you EVER call `finalize_run(results_dir, "stopped_by_user")`, you MUST be able to quote the literal user turn that said stop; if you cannot produce that quote, you have no basis to finalize — keep going or ASK. The ending document's **"Termination reason"** line must likewise QUOTE that literal user turn (e.g. `Termination reason: user stop — user wrote: "…"`) or NAME the harness criterion; do NOT write "user said stop" without the quote (the 2026-06-14 ENDING.md did exactly that and it was a confabulation). `finalize_run` does NOT enforce this in code (owner chose doc-only) — the safeguard is your discipline alone. Then **archive** with the `archive_run` view into `orchestrator/run_archive/<run_id>__<finished_at>/`.

**3-3 — CLAUDE.md:48–54 (termination bullet).** Full rewrite:
> - **Do not stop until a termination criterion is met. There are EXACTLY THREE, no others, and you invent none:** (1) **budget exhausted** [HARNESS-decided, auto-finalized — never call `finalize_run` for it]; (2) **five consecutive control-returns each STAGNANT and each with an intervention** (a framework rewrite, a DR, a Claude-native discovery pass, a Claude-authored grounding injection, OR a deliberate config-lever flip — the AUTOMATIC per-window meta round does NOT count) that still could not break the stagnation [HARNESS-decided + auto-finalized as `return_reason="stagnation_intervention_exhausted"` via `journal.termination_streak` over your canonical `control_return` rows; no "≥1 DR" requirement — a DR is just one intervention class — and never call `finalize_run` for it]; (3) **a LITERAL user stop message typed in the live conversation.** You finalize `stopped_by_user` BY HAND only for (3), and only when you can QUOTE the actual user turn back. NEVER finalize from an inferred / remembered / assumed / summarized / "it feels done" / "we should wrap up" signal — confabulating a user stop is the single worst failure here (2026-06-14 run: ~$24 unspent, `termination_streak=2`, an ENDING.md that wrote "user said stop" with no quotable turn). **There is NO code gate** — `finalize_run` writes any status you pass — so the discipline is yours. If stuck with no real stop and no harness criterion, keep launching the next cluster, or ASK and wait. Do not self-stop.

**3-4 — CLAUDE.md: new bullet in "Things future agents should NOT do" (after line 202).**
> - Do NOT finalize a run as `stopped_by_user` (or any terminal status) unless a REAL criterion is met. `budget_exhausted` and `stagnation_intervention_exhausted` are finalized BY THE HARNESS — never call `finalize_run` for those. `stopped_by_user` is valid ONLY when the user LITERALLY typed a stop message in the live conversation, and you must be able to QUOTE that turn before finalizing (and quote it in the ending doc's "Termination reason"). Never infer / remember / assume / summarize / hallucinate a user stop; "it feels done" / "we should wrap up" / "enough budget spent" is NOT a stop. `finalize_run` does not enforce this in code (owner declined a gate) — the discipline is yours. If stuck with no real stop and no harness criterion, keep going or ASK.

**3-5 — archive-analyst.md stop-language qualifier.** **NOT APPLIED** — grep confirmed no "wrap up / finalize / looks done" language exists (only "one pass, then stop," about the subagent's own pass). No edit needed.

---

## Verification / acceptance (per point; tests, greps; confirm no foundation drift)

**Point 1a (DOC).** Grep CLAUDE.md + SKILL.md: zero residual occurrences of `too_many_requests`, "CONFIRMED failure mode", "cannot sustain a REAL deep-research job", "dies mid-research". "30,000,000 TPM / 30,000 RPM" reads identically in both. `search_surcharge_usd` floor in `deep_research.py:33,149` unchanged. `test_dr.py` framed as a plain endpoint probe in both. "Never loop-retry a heavy failed DR" survives in the new positive teaching.

**Point 1b/1e/1f (DOC).** One canonical THREE-PATHS block at SKILL.md:478–486; NOVEL / SIMILAR-TO-EXISTING / USELESS appear identically in SKILL.md (canonical + discovery subsection), `archive-analyst.md`, `grounding-engineer.md`, CLAUDE.md:14. No "History-similar" anywhere (grep = 0). Anti-rejection rule present in the canonical block AND `archive-analyst.md` Rules. `work_dr` reads source-agnostic. SKILL.md:804 distinguishes the Azure-DR cap from the Claude-native bound.

**Point 1c/1d (DOC).** `grounding-engineer.md` exists, frontmatter valid, parity recipe matches `manual_ground.py`'s mechanical path, and it does NOT claim `id=5b404498` was a successful Steiner grounding (grep the file for "Steiner seed scored" = 0). SKILL.md "Claude-authored grounding (structural-pivot fallback)" subsection exists and names the subagent. CLAUDE.md CC-1 and archive-analyst Recommendation both reference it. The corrected score-0 teaching is generic, not the false Steiner claim.

**Point 1h (CODE).** 
- Landing-order check: `python -c "import shinka.core.sampler"` succeeds (all new symbols exported) — confirms no half-applied import break.
- **Parity test** in `orchestrator/tests/test_parity.py`: with `full_variant=None, grounding_mode=False`, `PromptSampler.sample(...)` output is byte-identical to the pre-edit baseline for a fixed seed (same variant draw at line 170, same "# Potential Recommendations" header, same `FULL_ITER_MSG`).
- **Positive grounding test**: with `grounding_mode=True` and an `island_brief` set + a distinct `meta_recommendations`, the rendered `sys_msg` contains the `meta_recommendations` direction under "# Required structural replacement" and does NOT contain the island brief (confirms the line 96–97 suppression); `full_variant` resolves to `different_algorithm`; the iter msg is `FULL_ITER_MSG_GROUNDING`.
- **Default-window regression**: existing `orchestrator/tests` stay green (run `conda run -n shinka python -m pytest orchestrator/tests -q`). `construct_mutation_prompt.py` OUTPUT keys unchanged.
- Confirm `evo.grounding_full_variant` / `evo.grounding_mode` are `None`/`False` on a normal `run.json` (byte-identical windows).

**Point 2 (DOC).** No-kill rule present at CLAUDE.md (2 bullets), SKILL.md grounding block (2 paras), SKILL.md:673–679, SKILL.md:804–805 — every mention carries the "Azure CALL only; `run_window` kill + `--resume` allowed" scope clause (grep). No `@medium` prescription anywhere (grep "@medium" in CLAUDE.md/SKILL.md returns only the pre-existing reasoning-effort-gotcha docs, none as a no-kill prescription). The 1g "switch to grounding-engineer" trigger phrase reads identically in all three locations.

**Point 3 (DOC-only).** SKILL.md:594 + CLAUDE.md:48–54 enumerate exactly three criteria with identical names and prohibition vocabulary; "no code gate / discipline is yours" appears in all four locations (2 SKILL + 2 CLAUDE); "user says stop" no longer appears as an undefined peer (grep). The stale `run_window.py:1531–1534` comment is NOT echoed (docs say five-in-a-row is HARNESS-finalized). `finalize_run`, the CLI view, the termination logic, and `test_end_of_run_summary_and_archive` are unchanged.

**No foundation drift.** Untouched: sqlite schema, the scripts' JSON contract (OUTPUT keys), `evaluate.py`, the user's `evaluate.py`/`initial.*`, `cadence_policy.py`, the termination logic / wake schedule, `_azure.py`'s 3600s wall, `deep_research.py`. The only CODE edits are the 1h framework/strategy/plumbing set, all default-preserving and applied between runs.

---

## Open questions for the user (genuine decisions; resolved ones noted)

1. **RESOLVED — DR quota:** 30,000,000 TPM / 30,000 RPM (folded into 1a edits).
2. **RESOLVED — `finalize_run` code gate:** DECLINED, doc-only (v1 3E–3I dropped). Recorded: if a future run repeats the confabulation despite this teaching, revisit the gate + a human-controlled sentinel.
3. **1h island_brief suppression — confirm the chosen mechanism.** I suppress the `sampler.py:96–97` overwrite under `grounding_mode` so the orchestrator's grounding direction is mandated. Equivalent alternative: null `island_brief` in `construct_mutation_prompt.py` under grounding_mode. Either works; I chose the sampler guard as the single point of truth. Confirm.
4. **1h variant restriction.** `grounding_full_variant` warn-rejects the 3 non-pivot variants and accepts only `different_algorithm` / `structural_redesign`. Acceptable, or allow all 5?
5. **1g trigger floor.** Spawn `grounding-engineer` after the Azure model has refused a verified pivot **once** (saves the wasted Azure spend the run incurred, consistent with Point 2's no-kill economics) vs **twice** (proves a strong-prior wall, not a one-off). The file currently says "demonstrably refused"; I lean toward a floor of 1. Pick.
6. **1c/1d eval-time cap.** `grounding-engineer.md` threads the eval `time` from the live `run.json`'s `task.eval_time` (so verification uses the same wall as the inner loop) rather than a hard-coded 35 min. Confirm that's the intended source.
7. **Exact-duplicate escape hatch (1e).** I added "an EXACT duplicate of an already-elite program is path (iii) USELESS" as the ONLY similarity-based drop. It slightly softens the absolute "similar is never a kill" rule — keep it, or make path (ii) truly absolute (a duplicate then routes to a no-op combine run)?
8. **Per-idea triage granularity (1f).** A discovery pass returning N ideas spawns `grounding-engineer` once per grounded idea (parallel scratch dirs), kept as one `control_return` intervention row. Confirm that's the intended granularity (one program per spawn, not a list-accepting subagent).
9. **`deep_research.py:149` code comment (1a).** The doc no longer pre-warns about failure cost, but the surcharge floor code stays. Add a one-line comment at `:149` ("Azure bills a submitted-then-failed DR even when usage is empty") so the rationale survives in code? (Out of the DOC-only 1a scope; flag for a between-runs code pass.)
10. **`work_dr` rename (deferred).** It now covers Azure DR + Claude-native discovery; keeping the name + documenting the dual meaning. A rename to `work_discovery` would touch the harness work-score JSON contract (foundation) — out of scope; flagged.
11. **Post-run / ending-document additions (NOT mid-run):** a first-class `--ground-from-file` / protected grounding-base so a score-0 seed can't be evicted before it matures; an optional killed-call cost floor in the grounding *caller* mirroring DR's `search_surcharge_usd`. Spec now or defer to the ending document?

---

**Files this plan edits (all absolute):**
- `C:/Users/dtlic/Documents/GitHub/ShinkaEvolve/.claude/worktrees/wizardly-booth-ad6a2d/CLAUDE.md`
- `C:/Users/dtlic/Documents/GitHub/ShinkaEvolve/.claude/worktrees/wizardly-booth-ad6a2d/.claude/skills/shinka-orchestrator/SKILL.md`
- `C:/Users/dtlic/Documents/GitHub/ShinkaEvolve/.claude/worktrees/wizardly-booth-ad6a2d/.claude/skills/shinka-orchestrator/subagents/archive-analyst.md`
- `C:/Users/dtlic/Documents/GitHub/ShinkaEvolve/.claude/worktrees/wizardly-booth-ad6a2d/.claude/skills/shinka-orchestrator/subagents/grounding-engineer.md` (NEW)
- `C:/Users/dtlic/Documents/GitHub/ShinkaEvolve/.claude/worktrees/wizardly-booth-ad6a2d/orchestrator/scripts/construct_mutation_prompt.py`
- `C:/Users/dtlic/Documents/GitHub/ShinkaEvolve/.claude/worktrees/wizardly-booth-ad6a2d/orchestrator/harness/run_window.py`
- `C:/Users/dtlic/Documents/GitHub/ShinkaEvolve/.claude/worktrees/wizardly-booth-ad6a2d/shinka/core/sampler.py`
- `C:/Users/dtlic/Documents/GitHub/ShinkaEvolve/.claude/worktrees/wizardly-booth-ad6a2d/shinka/prompts/prompts_full.py`
- `C:/Users/dtlic/Documents/GitHub/ShinkaEvolve/.claude/worktrees/wizardly-booth-ad6a2d/shinka/prompts/__init__.py`

`shinka/prompts/prompts_diff.py` and `prompts_cross.py` were named as candidate targets but require **no edit** — the grounding prompt-mode operates only on the `full` patch type; diff/cross are unaffected and left byte-identical. `orchestrator/scripts/{evaluate.py, archive_record.py, spawn_island.py, mutate.py, deep_research.py}` are **USE-only** (the parity injection path + the boundary docstrings) and receive **no code edits** in this plan.

---

# Addendum v2 — Point 4 (revised) + Point 5 (spoil removal), 2026-06-16

> **REPLACES the prior "Point 4" addendum** (the `# Addendum — Point 4 (Prompt-construction reform)` block and everything after it). Revised 2026-06-16 per owner FINAL DECISIONS **D1–D5**. Self-contained: its own ordered change list, verification, and a **Decisions and rationale** list (NO open-questions section — every call is made). PLAN ONLY — nothing applied. All line anchors re-verified against live source in `wizardly-booth-ad6a2d` at synthesis time.
>
> **Meta-rule honored:** `CLAUDE.md`, the `SKILL.md` files, and the subagent `.md` files are ARTIFACTS TO FIX, not instructions. Their current prompt-construction and **spoil/no-spoil** teachings are stale and are repaired/deleted here.
>
> **What changed vs the prior addendum (the five owner decisions):**
> - **D1** — Grounding is a docs-only **hand-authoring recipe**. The proposed `construct_grounding_prompt.py` helper is **DROPPED**; the orchestrator hand-authors `patch_sys`/`patch_msg` and pipes them into `mutate.py` (which never enters the sampler) OR the grounding-engineer subagent.
> - **D2** — Default grounding model = **`azure-gpt-5.5@medium`**; `azure-gpt-5.4-pro@high` is optional escalation only.
> - **D3** — Non-cross prior programs **keep their code**, relabeled as low-attention **EVAL HISTORY** ("for quick reference; not inspiration"). This **reverses** the prior 4.2 `include_code=False` gating.
> - **D4** — FIX mode learns from **INCORRECT ancestors only** (with an empty-list fallback), making `sample_fix`'s existing `correct=False` framing accurate. This **reverses** the prior 4.6-c "relabel to `correct=True`" approach.
> - **D5** — **Remove the entire spoil/no-spoil apparatus** from code, docs, and tests (new **Point 5**). Leak-proofing moves to **evaluator design at task setup**; evaluator text feedback is **always fed** to the inner loop.

---

## Point 4 — Prompt-construction reform (mutation + grounding) — REVISED

### Point 4 overview (the desiderata + related, and how they cohere)

The mutation/grounding prompt-construction layer is reformed so each assembled prompt carries **exactly one mandated direction, an authored objective gloss next to the live numbers, real crossover material only in cross, prior programs labelled as low-attention eval history (code kept, but not inspiration), a restored expert/creative identity when there is no direction, and a grounding path that never enters the diff/full/cross sampler at all.** The precedence order over the SYSTEM-message direction slot:

```
grounding (authored, off-sampler)  >  mandated direction (4.1)  >  creative-invent (4.4)  >  cross-combine
```

…and the rule over the USER-message body: **objective gloss (4.3) + parent metrics always; genuine crossover code only in cross; any non-cross priors are CODE kept but labelled low-attention EVAL HISTORY (4.2).** Grounding (4.5) is lifted entirely out of this machinery into a **hand-authored** prompt. The reform is **default-preserving** for normal evolution except four deliberate, owner-mandated framing changes (the direction header, the eval-history relabel, the no-direction creative preamble, and the optional objective gloss when `task.objective_brief` is set).

**Mutability map (every file Point 4 touches):**

| Layer | Files | Class |
|---|---|---|
| framework-between-runs | `shinka/prompts/prompts_base.py`, `prompts_full.py`, `prompts_diff.py`, `prompts_cross.py`, `prompts_init.py`, `prompts/__init__.py`, `shinka/core/sampler.py` | CLAUDE.md "Edit `shinka/...` directly" sanctions; NOT the locked foundation |
| mutable-strategy | `orchestrator/scripts/construct_mutation_prompt.py` | orchestrator-rewritable; OUTPUT JSON contract `{ok,patch_sys,patch_msg,patch_type}` preserved. **No new script** (D1 dropped `construct_grounding_prompt.py`). |
| harness-plumbing (between-runs) | `orchestrator/harness/run_window.py` | threads a config knob + sources fix-mode ancestors; NOT a mid-run lever flip |
| DOC | `CLAUDE.md`, `SKILL.md`, `subagents/grounding-engineer.md`, this plan | — |
| **FOUNDATION — untouched** | sqlite schema, the scripts' JSON OUTPUT contract, `evaluate.py`, user's `evaluate.py`/`initial.*`, `cadence_policy.py`, termination logic, `_azure.py` wall | never edited |

---

### 4.1 Directions — one per prompt + concise directive framing (UNCHANGED from prior addendum)

No spoil entanglement; D1–D5 do not touch this. Summary of the three coordinated edits (full diffs unchanged from the prior addendum):

- **(4.1-A) `sampler.py` weak-header replacement** — replace `# Potential Recommendations` / "The following are potential recommendations…" with a concise directive header (CODE, framework-between-runs):
  ```python
  _verb = "edit" if patch_type == "diff" else "rewrite"
  sys_msg += "\n\n# Direction for this attempt"
  sys_msg += (
      f"\nBase your {_verb} on the direction below. It is the intended approach for "
      "this generation — treat it as the goal of your change, not an optional suggestion:\n"
  )
  sys_msg += f"\n{meta_recommendations}"
  ```
  This is now the single, **unconditional** header for every non-cross direction (there is no `grounding_mode` if-branch — grounding never enters the sampler; see 4.5).
- **(4.1-B) `run_window.py:366`** — drop the redundant inline prefix: `return "Direction to pursue in THIS attempt: " + chosen` → `return chosen` (CODE, harness-plumbing). Safe: `test_improvements.py` asserts only the direction substring.
- **(4.1-C) `construct_mutation_prompt.py`** — defensive first-line splitter on the hand-authored `content` fallback so exactly one direction reaches the header; no-op on the structured path and exempt under `brief_compose_mode="augment"` (CODE, mutable-strategy).

---

### 4.2 Non-cross priors = labeled EVAL HISTORY — keep code, reframe (REVISED per D3)

**State today (verified).** `construct_eval_history_msg` (`prompts_base.py:39–81`) is the single shared eval-history builder for diff/full/cross (`correct=True`) AND fix (`correct=False`). The `correct=True` header (`:48–51`) reads `"Here are the performance metrics of a set of prioviously implemented programs:"` (typo `prioviously`), then `# Prior programs` (`:59–60`) + the **full fenced `prog.code`** per program (`:61`) + `Performance metrics:` (`:63–67`). This framing reads as "here are programs to study/combine" — indistinguishable across diff/full/cross — which over-weights priors as inspiration in non-cross modes. Cross supplies its REAL crossover partner separately via `get_cross_component` (`prompts_cross.py:57–73`, `sampler.py:224–228`), with the genuine "combine the best parts" instruction in `CROSS_SYS_FORMAT`/`CROSS_ITER_MSG`.

**Decision (D3): KEEP the prior-program CODE in non-cross; RELABEL it as low-attention EVAL HISTORY.** This **reverses** the prior addendum's `include_code=False` gating. The fix is a **pure header/wording change** in the `correct=True` branch of `construct_eval_history_msg` — no `include_code` parameter, **no `sampler.py` change**, no signature churn. The per-program code/metrics body shape is unchanged. Cross keeps its genuine-crossover framing untouched (it lives entirely in `get_cross_component` + `CROSS_*`). The single reframed header is shared by diff/full AND cross and that is correct: in cross the eval-history block is the performance-landscape context list, while the actual partner + combine instruction is the separate `get_cross_component` material; relabeling the shared list as "reference, not inspiration" does not weaken cross's real crossover framing.

**(4.2-A) — `construct_eval_history_msg` `correct=True` header reframe + typo fix (CODE, framework-between-runs).** `prompts_base.py:47–51`, old → new:
```python
inspiration_str = (
    "Here are the performance metrics of a set of prioviously "
    "implemented programs:\n\n"
)
```
```python
inspiration_str = (
    "# Eval history (for quick reference)\n\n"
    "Below are some prior attempts and their scores, shown ONLY as eval "
    "history for quick reference if you need it. These are NOT inspiration "
    "and you need not study them closely — they are previously evaluated "
    "programs kept here for context, not a pattern to copy or merge:\n\n"
)
```

**(4.2-B) — per-program section header reword (CODE, framework-between-runs).** `prompts_base.py:59–60`, old → new (the fenced `prog.code` at `:61` and the `Performance metrics:` block at `:63–67` are **UNCHANGED — code stays**):
```python
if i == 0:
    inspiration_str += "# Prior programs\n\n"
```
```python
if i == 0:
    inspiration_str += "## Prior attempts (reference only — do not copy)\n\n"
```

**(4.2-C) — `correct=False` header typo fix (CODE, framework-between-runs).** `prompts_base.py:53–56`, fix only `prioviously → previously` (D4 owns the fix-mode sampling change; D3/this point leave the incorrect-branch framing otherwise intact):
```
"Here are the error output of a set of prioviously implemented but incorrect programs:\n\n"
→ "Here are the error output of a set of previously implemented but incorrect programs:\n\n"
```

> **`sampler.py:181–186` is unchanged** under D3 (no `include_code` argument is added). The prior addendum's 4.2-B (`include_code=False`) and the `include_code` parameter are **DROPPED**.

**FIX mode keeps code, now accurately (cross-ref 4.6/D4):** `sample_fix` still calls `construct_eval_history_msg(..., correct=False)` with the default code-bearing rendering; under **D4** the ancestors fed in are now INCORRECT-only (filtered at the harness), so the `correct=False` header is accurate and the code shown is genuinely-failed ancestors.

---

### 4.3 Metric slot — orchestrator-authored objective (spoil caveats removed per D5/D5h)

**State today (verified).** The only "metric" content in any mutation prompt is the raw `perf_str(parent.combined_score, parent.public_metrics)` line (`prompts_base.py:13–20`) in the `{performance_metrics}` slot. `perf_str` renders ONLY `combined_score` + `public_metrics` — private/held-out metrics are never passed (cnot's `evaluate.py` puts `depths_per_L` under `private`). No `task.objective_brief` field exists.

**Fix — additive optional boot config field `task.objective_brief` (string|null).** An orchestrator-authored qualitative paragraph (the score SHAPE: what we optimize + hard constraints + the native gate/operation set), drafted once at boot, **augmenting** (never replacing) the raw `perf_str` line. Unset/null ⇒ every prompt byte-identical to today. **The orchestrator authors `task.objective_brief` FREELY** — there is no spoil gate; leak-proofing is an evaluator-design responsibility (Point 5), the same as for `task_sys_msg`.

**(4.3-A) — `prompts_base.py`, new helper after `perf_str` (CODE, framework-between-runs).** Spoil caveats removed from the docstring:
```python
def objective_section(objective_brief) -> str:
    """Render the orchestrator-authored qualitative score-shape paragraph (what we optimize +
    hard constraints + native gates) as a header for the metric slot. Empty string when no brief
    was authored -> byte-identical to the legacy prompt. AUGMENTS perf_str's numbers; carries the
    authored prose the orchestrator wrote at boot. Leak-proofing is an evaluator-design concern
    (Point 5), not a property of this string."""
    if not objective_brief or not str(objective_brief).strip():
        return ""
    return f"# What we are optimizing\n{str(objective_brief).strip()}\n\n"
```

**(4.3-B) — `prompts/__init__.py`** — export `objective_section` alongside `perf_str`.

**(4.3-C) — `sampler.py`, thread `objective_brief` into the parent metric slot (CODE, framework-between-runs).** Add `objective_brief: Optional[str] = None` as the last kwarg of `sample()`; build once before the branch: `metric_block = objective_section(objective_brief) + perf_str(parent.combined_score, parent.public_metrics)`; pass `performance_metrics=metric_block` in each diff/full/cross branch. Add the same kwarg to `sample_fix` and render it via `objective_section` into the fix SYSTEM message after the `FIX_SYS_FORMAT` append (no `{performance_metrics}` slot in `FIX_ITER_MSG`). Default `None` ⇒ byte-identical.

**(4.3-D) — `construct_mutation_prompt.py`, read + thread (CODE, mutable-strategy).** Read `_objective_brief = payload.get("objective_brief")`; pass `objective_brief=_objective_brief` to BOTH `sampler.sample(...)` and `sampler.sample_fix(...)`. OUTPUT contract byte-identical.

> **D5h reconciliation (spoil caveats DROPPED):** the prior 4.3 carried a **"Spoil fact"** paragraph, a **"Spoil discipline"** paragraph, the docstring clause "never a held-out metric (the no-spoil discipline lives with the author…)", and a 4.3-D rationale that `objective_brief` is "NOT in `_EVAL_TEXT_KEYS` and NOT stripped under `use_text_feedback:false`." **All are deleted.** `_EVAL_TEXT_KEYS` no longer exists after Point 5; the objective_brief is authored freely; leak-proofing is the evaluator's job at setup.

**(4.3-E) — `run_window.py`, thread the boot field (CODE, harness-plumbing).** Add `"objective_brief": task.get("objective_brief"),` to the `construct_mutation_prompt.main` payload (immediately after the `"task_sys_msg"` line). `None` when unset.

**Canonical reuse:** `task.objective_brief` is also the `{objective}` block of the hand-authored grounding prompt (4.5) — author once, reuse everywhere.

---

### 4.4 Creative/expert framing when no direction (UNCHANGED from prior addendum)

No spoil entanglement (the `task_sys_msg` references at the old lines 692–694 are about the `BASE_SYSTEM_MSG` dead-code fallback, not spoil). Summary of the edits (full diffs unchanged):

- **(4.4-A)** `prompts_base.py` — new `EXPERT_CREATIVE_PREAMBLE` constant after `BASE_SYSTEM_MSG`; export from `prompts/__init__.py`.
- **(4.4-B)** `sampler.py` — gate the preamble onto no-direction diff/full gens (after island_brief handling, before the 4.1-A header), regardless of `task_sys_msg`:
  ```python
  has_direction = meta_recommendations not in (None, "none")
  if (not has_direction) and patch_type in {"diff", "full"}:
      sys_msg += EXPERT_CREATIVE_PREAMBLE
  ```
- **(4.4-C)** `FULL_ITER_MSG` `# Task` — add the symmetric creative invitation; `CROSS_ITER_MSG` untouched.

**FIX stays improvement-neutral** (D4): `sample_fix` builds its own SYSTEM message from `FIX_SYS_FORMAT` and never calls the 4.4 gate — confirmed; no edit routes the preamble into a repair.

---

### 4.5 Grounding = hand-authoring recipe, docs only (REVISED per D1/D2 — script dropped, default `azure-gpt-5.5@medium`)

**State today (verified, load-bearing).** `mutate.py` reads `patch_sys`/`patch_msg` (`:159–160`) and `patch_type` (`:132`) **directly** from its stdin payload, routes `patch_type in ('full','cross','fix')` to `apply_full_patch` (`:88`), parses the reply via `extract_between(raw, "<NAME>" …)` / `<DESCRIPTION>` (`:193–194`) then applies the `` ```{language}`` ``-fenced `<CODE>` block, and **never imports `sampler`/`PromptSampler`/`construct_mutation_prompt`** (its only `shinka.llm` imports are `extract_between` and `resolve_model_backend`). Model/effort come from `model_name`+`reasoning_effort` (`:143–144`); web search from `enable_web_search` (`:149`). **So a ready-made `patch_sys`/`patch_msg` pipes straight in, bypassing the sampler entirely** — exactly what D1 exploits.

**Decision (D1): DROP the proposed `construct_grounding_prompt.py` helper. Grounding is a DOCS-ONLY hand-authoring recipe.** The orchestrator HAND-AUTHORS two strings — `patch_sys` and `patch_msg` — and feeds them to ONE of two executors. No new script, no sampler/prompts changes for grounding (v2 item 1h stays dropped — see "Revision to v2 item 1h"). **Default grounding model = `azure-gpt-5.5@medium` (D2)**; `azure-gpt-5.4-pro@high` is optional escalation only.

**The recipe — four authored ingredients:** (1) the **INIT seed program** = the task's `initial.<ext>` (NOT a sigmoid-sampled parent — de-anchors from the seed family); (2) a **DETAILED direction** = the technique + key steps + **reference pointers** (author/year/arXiv/URL); (3) the **objective/constraints** = the boot-authored score SHAPE + hard constraints + native gate/operation set (reuse `task.objective_brief` from 4.3 when set); (4) a same-input/output **"write a new program implementing this idea"** instruction.

**`patch_sys` (copy-paste template the orchestrator authors):**
```
{task_sys_msg}

# Required structural replacement
You MUST implement the algorithm described below from scratch. The reference program is shown
ONLY to define the input/output contract and the EVOLVE-BLOCK markers; do NOT tune, extend, or
refactor it.

{direction}            # the DETAILED technique: key steps + reference pointers (author/year/arXiv/URL)

# Objective and constraints
{objective}            # the score SHAPE + constraints + native gate set (= task.objective_brief)

<FULL_SYS_FORMAT_DIFFERENT body>   # paste the different_algorithm <NAME>/<DESCRIPTION>/<CODE>```{language}```
                                   # response-format block, copied read-only from
                                   # shinka.prompts.FULL_SYS_FORMAT_DIFFERENT, so mutate.py's
                                   # <NAME>/<DESCRIPTION> parse + apply_full_patch contract are met
```

**`patch_msg` (copy-paste template):**
```
# Reference interface (do NOT improve or refactor this)

The program below defines the interface contract only — same inputs, outputs, and the
EVOLVE-BLOCK-START / EVOLVE-BLOCK-END markers. You are REPLACING its algorithm.

```{language}
{seed_code}            # the INIT program initial.<ext>, NOT a sigmoid-sampled parent
```

# Task
Implement the algorithm described in the system prompt as a fundamentally different approach.
Keep the SAME inputs/outputs and the EVOLVE-BLOCK markers; provide the complete new program code.
```

`patch_type = "full"` (so `mutate.py` routes to `apply_full_patch`).

**Two executors of the identical authored prompt:**
- **(a) AZURE via `mutate.py`** — pipe `{parent_code:<seed_code>, patch_sys, patch_msg, patch_type:"full", model_name:"azure-gpt-5.5", reasoning_effort:"medium", enable_web_search:<true when a reference must be read else false>, patch_dir, language, run_id, generation}`. **DEFAULT `azure-gpt-5.5@medium` (D2)**; `azure-gpt-5.4-pro@high` is optional escalation only (when 5.5 can't read/implement the reference). The `@high` effort is valid for pro (pro rejects `low`).
- **(b) SUBAGENT** — hand the same four ingredients to `subagents/grounding-engineer.md`; Claude authors the EVOLVE-BLOCK and self-evaluates (off-ledger). Use path (b) once the Azure inner-loop model has demonstrably refused the verified pivot (v2 1g).

**Parity tail (UNCHANGED — the `manual_ground.py` mechanical path / the 1c/1d parity contract):** on `correct:true` from EITHER executor → `evaluate.py` → `EmbeddingClient("azure-text-embedding-3-small").get_embedding(code)` (ledger the ≈$0.00002 via `journal.add_cost`) → `archive_record.py` (`metadata:{grounding:"authored"}`) → `spawn_island.py` → ONE `append_intervention`. `parent_id=null` for a NOVEL pivot (its own island); the closest program id for SIMILAR-TO-EXISTING.

**Why safe by construction:** the authored prompt structurally CANNOT suffer the four sampler scaffolding pulls — ONE detailed direction (no list, no island_brief to overwrite it, because the sampler is never entered), NO archive-sampled inspiration CODE (only the seed, framed "do NOT improve/refactor"), the seed framed as a reference interface not a parent-to-tune. Framing ladder stays consistent: **recommended (no-direction) < mandated-direction (4.1) < required-replacement (grounding).**

> **D1/D5 reconciliation (spoil notes DROPPED):** the prior 4.5 carried a `use_text_feedback` INPUT key on the dropped helper, a "NEVER held-out numbers" note on the `{objective}` block, and a "Spoil:" paragraph. **All are removed.** There is no helper and no strip; the `{objective}` block is phrased neutrally as "the boot-authored score shape + constraints." Leak-proofing is the evaluator's job at setup (Point 5).

---

### 4.6 Related fixes (REVISED: 4.6-c = fix-incorrect-only per D4; 4.6-d/e dropped per D5)

| # | Issue | Disposition |
|---|---|---|
| a | Typo `prioviously` (`prompts_base.py:49,54`) | **FOLD into 4.2-A/4.2-C** — both branches fixed. |
| b | Typo `repond` (`prompts_diff.py:2`, `prompts_cross.py:13`, `prompts_init.py:7` — THREE sites) | **KEEP** — `repond → respond`; also `a edit → an edit` on `prompts_diff.py:2`. (Bundled hygiene; lands so `repond → 0` and `prioviously → 0` in `shinka/prompts/`.) |
| c | FIX mode renders **correct** ancestors under the `correct=False` "previously implemented but incorrect" header | **REVISED per D4 (see below):** make the `correct=False` framing ACCURATE by sourcing **INCORRECT ancestors only** at the harness (empty-list fallback when none), NOT by relabeling to `correct=True`. Keep FIX improvement-neutral. **The prior "pass `correct=True`" approach is removed.** |
| d | Nested-`metadata` strip in `_strip_eval_text` | **DROPPED — moot under Point 5 (D5g):** once `_strip_eval_text`/`_EVAL_TEXT_KEYS` is deleted, there is no nested-metadata channel to harden. No edit. |
| e | C2 runtime caution + `run_window` `_utf` gate ("already spoil-safe") | **DROPPED — moot:** there is no spoil regime to be "safe" against. The runtime caution stays as a plain numeric channel; see Point 5. |
| f | `INIT_USER_MSG` drops `task_description` | **LEAVE (off orchestrator path)** — note only. |
| g | NOVELTY prompt (`prompts_novelty.py`) | **LEAVE (dead in orchestrator)** — the active gate is `novelty_check.py` (embedding cosine, no LLM). |
| h | `inspiration_sort_order="ascending"` | **LEAVE (intentional)** — the 4.2 reframed header is a prefix on the whole block, survives the sort. |

#### D4 — FIX mode samples ONLY incorrect ancestors

**State today (verified).** Both fix sub-paths render ancestors through `construct_eval_history_msg(..., correct=False)`, but the ancestors are frequently CORRECT, so the framing lies:
1. **IMMEDIATE-FIX:** `run_window.py` calls `_attempt_immediate_fixes(..., parent, ...)` where `parent` is the normally-sampled parent, drawn from `archived_correct` on an ordinary generation (CORRECT). It is passed as `ancestor_inspirations=[learn_from]` and rendered `correct=False` — a CORRECT program mislabeled incorrect.
2. **REPAIR-mode (`needs_fix=True`):** the `query_type:"ancestry"` chain of the incorrect parent is passed whole as `ancestor_inspirations`; that lineage typically contains CORRECT ancestors back to the seed — all rendered `correct=False`.

The `correct` boolean is always present in the summary dicts (`_common.program_summary` / `_PROGRAM_SUMMARY_FIELDS`), so the harness can filter on it. `sample_fix` already renders no eval-history when `ancestor_inspirations` is empty.

**Decision (D4): make fix-mode ancestors INCORRECT-only at the HARNESS**, so `construct_eval_history_msg(correct=False)` is accurate by construction — no sampler/prompt-template change. Empty-list fallback when none are incorrect (show NONE rather than a correct one mislabeled). The just-failed candidate / incorrect parent is still the repair target via its own code+error, so a repair with no incorrect ancestors is still well-formed.

**(4.6-c-1) — IMMEDIATE-FIX (CODE, mutable-strategy).** In `_attempt_immediate_fixes`, gate the `learn_from` ancestor on its actual incorrectness before the `construct_mutation_prompt.main` call:
```python
# D4: fix-mode learns from INCORRECT ancestors only (correct=False framing must be accurate);
# the sampled parent is normally CORRECT, so this is [] in practice and the repair leans on the
# just-failed candidate's own code + error.
_fix_ancestors = [learn_from] if (learn_from and not learn_from.get("correct", False)) else []
```
and pass `"ancestor_inspirations": _fix_ancestors,`.

**(4.6-c-2) — REPAIR-mode ancestry filter (CODE, harness-plumbing).** After fetching the ancestry chain into `ancestors`, filter to incorrect rows (`[] when none`):
```python
# D4: keep only INCORRECT ancestors so construct_eval_history_msg(correct=False) is accurate;
# [] when none (sample_fix then shows no eval-history and repairs from the incorrect parent's own failure).
ancestors = [a for a in ancestors if not a.get("correct", False)]
```
No change to the parent/`incorrect_program` error wiring.

**(4.6-c-3) — accuracy comments (CODE).** In `construct_mutation_prompt.py`, change the docstring line ~21 "FIX branch only: correct ancestors to learn from" → "FIX branch only: INCORRECT ancestors only (the `correct=False` framing requires it); empty when none", and add a one-line comment in the `needs_fix` branch that the harness has already filtered to incorrect ancestors. In `sampler.py`, add a comment above the `sample_fix` `construct_eval_history_msg(correct=False)` call noting ancestors are guaranteed incorrect-only by the harness. In `prompts_base.py`, update the `construct_eval_history_msg` docstring: "When `correct=False`, callers MUST pass only genuinely-incorrect programs (the harness filters fix-mode ancestors to incorrect-only)." **No JSON OUTPUT-key change; `correct=False` stays; no `correct=True` relabel.**

**(4.6-c-4) — test (mutable-strategy test).** Add `test_fix_mode_ancestors_incorrect_only`: (a) assert `_attempt_immediate_fixes` passes `[]` as `ancestor_inspirations` for a CORRECT `learn_from` and non-empty for an INCORRECT one (monkeypatch `construct_mutation_prompt.main` to capture the payload); (b) assert the repair-mode filter drops correct ancestors from an ancestry list. Offline (no LLM). Add **after** Point 5 removes the no-spoil ancestor test; do not assert any held-out/strip behavior.

---

### Revision to v2 item 1h (UNCHANGED — stays dropped)

v2 item 1h (the sampler `grounding_mode`/`full_variant` retrofit) **remains SUPERSEDED and DROPPED.** Under D1, grounding is a hand-authored off-sampler prompt (no helper, no sampler kwargs), so 1h's only remaining piece of grounding-specific CODE is gone: after this revision, grounding lands entirely in **DOC + the `grounding-engineer.md` subagent**, net **zero** new grounding code. 1h's four-axis diagnosis stays valid as motivation for the always-on directive header (4.1) and for "grounding goes via an authored prompt." `sampler.sample()` keeps **no** grounding awareness. (`sampler.py`/`prompts/*` are still edited by Point 4 sub-points 4.1/4.2/4.3/4.4 — just not for grounding.)

---

## Point 5 — Remove the spoil apparatus (D5)

**Philosophy NOTE (taught at setup, not inner loop — replaces every no-spoil teaching).** *Leak-proofing is the EVALUATOR's job at task DRAFTING/SETUP, not the inner loop's.* When you author/convert a task, design `evaluate.py` so nothing it returns to the inner loop can be gamed: put any held-out / gate-defining number under the `private` metrics dict (never `public` — only `public` reaches the prompt via `perf_str`, `prompts_base.py:13`), and make `text_feedback` describe the failure WITHOUT handing over a held-out target. **Once the evaluator is leak-proof, ANY code proposal that PASSES and IMPROVES the metric is a GOOD candidate** — full evaluator text feedback is **always** fed to the mutation/fix/meta prompts because it helps the search converge. This is already how the live evaluator works: `perf_str` renders only `combined_score` + `public_metrics`, and cnot's `evaluate.py` puts `depths_per_L` under `private` — the metric slot already cannot auto-leak. The harness STILL refuses to start on a missing/empty/sentinel `task_sys_msg` — reframed as "ensures a goal was authored," **NOT a spoil rule.**

### 5.1 Code deletions (the `use_text_feedback` decision, `_strip_eval_text`, meta/run_window gates)

**DECISION on the `use_text_feedback` knob — DELETE IT from the orchestrator surface; ALWAYS feed evaluator text feedback to the inner loop.** Keep the `PromptSampler.use_text_feedback` **parameter** in `sampler.py`, default it **True**, and have the orchestrator pass `True` unconditionally.

**Rationale (the decisive call — resolves the SA1-vs-map conflict in favor of DELETE).** (1) The flag's only orchestrator job was spoil-mitigation — D5(b) mandates always feeding feedback, so the false branch becomes unreachable policy. (2) Keeping it as a "benign verbosity knob" is a fiction: in `sampler.py` (`:185,192,289`) `use_text_feedback` ALSO gates the legit `text_feedback`/eval-history signal we now want **always on**, so a residual orchestrator knob would still gate *help*, not just verbosity. (3) Fewer moving parts is cleaner. SCOPE OF DELETION: the orchestrator/harness/scripts surface only (the `evo.use_text_feedback` config key, the `_utf` gates, the `_strip_eval_text`/`_EVAL_TEXT_KEYS` block, the `meta_summarize` gate). The framework `PromptSampler` parameter stays (it is a legit text-feedback rendering capability), defaulted True. **`EvolutionConfig.use_text_feedback` (`shinka/core/config.py:56`) is LEFT UNTOUCHED** — it is the legacy upstream dataclass field, confirmed-dead for the orchestrator (`config.py:6–8`: "The orchestrator does not read EvolutionConfig at all"), consumed only by the `load_configs_from_yaml` hydra path; removing it would gratuitously break the `_target_: shinka.core.EvolutionConfig` yamls for zero orchestrator benefit. A future agent must NOT "finish the job" and delete it.

**(5.1-a) — `construct_mutation_prompt.py` strip block + docstring (CODE, mutable-strategy).** DELETE the entire `# No-spoil (H9)` block at `:77–93` (the `if not bool(payload.get("use_text_feedback", True)): _EVAL_TEXT_KEYS = (...); def _strip_eval_text(...): ...; _strip_eval_text(...)`). In the `PromptSampler(...)` construction (`:99–106`) replace `use_text_feedback=bool(payload.get("use_text_feedback", True))` with hardcoded `use_text_feedback=True`. Delete the `use_text_feedback` docstring INPUT line (~`:30`, "false on a spoil-risk task"). The runtime-caution comment that cross-refs `_EVAL_TEXT_KEYS` (~`:152–153`) is reworded to "numeric/boolean, not evaluator text" (drop the `_EVAL_TEXT_KEYS`/spoil-survival framing). OUTPUT contract `{ok,patch_sys,patch_msg,patch_type}` unchanged (only INPUT preprocessing is removed).

**(5.1-b) — `run_window.py` immediate-fix path (CODE, harness-plumbing).** Delete the `_utf` definition (`:420–423`) and its "COMPLETE spoil mitigation" comment. In the `incorrect_program.metadata` dict (`:439–441`) drop the `if _utf else ""` conditionals so `stdout_log`/`stderr_log` are **always** populated from the eval. In the immediate-fix `construct_mutation_prompt.main` payload (`:452–455`) delete the `use_text_feedback` key and its H9 comment. *(Coordinate with D4 4.6-c-1 at the same site — the incorrect-ancestor filter lands alongside the `_utf` cleanup, one patch per site.)*

**(5.1-c) — `run_window.py` sampled-fix repair path (CODE, harness-plumbing).** Delete the `_utf = bool(evo.get("use_text_feedback", True))` line (`:612`) and the `if not _utf:` channel-blanking branch (`:614–618`). Promote the existing `elif not _pmd.get("stderr_log")` fallback (`:619–624`) to an **unconditional** `if not _pmd.get("stderr_log"):` so a domain failure still backfills `text_feedback` into `stderr_log` (keeps the repair prompt non-blind). *(Coordinate with D4 4.6-c-2 at the same site.)*

**(5.1-d) — `run_window.py` mutation + meta payloads (CODE, harness-plumbing).** Delete `"use_text_feedback": evo.get("use_text_feedback", True),` from the `construct_mutation_prompt` payload (`:680`). Delete `"use_text_feedback": bool(evo.get("use_text_feedback", True)),` + its `H9\M6` comment from the `_meta_payload` (`:1358–1359`).

**(5.1-e) — `run_window.py` BOOT guard reframe (CODE, harness-plumbing).** **KEEP the guard** (refuse-to-start on missing/empty/sentinel `task_sys_msg`, `:1087–1101`). Strip only its no-spoil framing: change the comment "author `task_sys_msg` (the goal + hard constraints, without spoiling the held-out metric)" → "… (the goal + hard constraints)"; change the `SystemExit` message "…author the goal + hard constraints (no-spoil) before running…" → "…author the goal + hard constraints before running…".

**(5.1-f) — `meta_summarize.py` gate (CODE, mutable-strategy).** Delete the `_utf = bool(payload.get("use_text_feedback", True))` line (`:180`) and its `H9/M6` spoil-risk comment (`:177–179`). In `_err_reason` (`:191`) change `e = (p.get("error_traceback") or "") if _utf else ""` → `e = p.get("error_traceback") or ""` so the failure reason is always shown.

**(5.1-g) — `record_policy.py` comment (CODE, mutable-strategy).** Reword `:57–59`: drop the "so they survive `use_text_feedback:false`" framing; keep "numeric/boolean, always recorded, never echo a traceback." (Minor — factual, no spoil connotation.)

**(5.1-h) — `sampler.py` parameter default (CODE, framework-between-runs).** Change the `PromptSampler.__init__` default `use_text_feedback: bool = False` → `use_text_feedback: bool = True`. The parameter and its uses (`:56,185,192,281,289`) STAY — they are the framework's legit text-feedback rendering capability; the orchestrator now always passes True. No spoil semantics anywhere in this file. *(This default-flip is folded into the same atomic `sampler.py` rewrite as Point 4.1/4.2/4.3/4.4 — one coordinated edit, not competing diffs.)*

**(5.1-i) — `configs/orchestrator_run.default.json:22` (CONFIG).** DELETE the `"use_text_feedback": true,` line from the `evo` block (the knob is removed from the orchestrator surface; feedback is unconditionally on).

**(5.1-j) — `shinka/core/config.py:56` (framework — LEAVE).** No functional change. Optionally add a one-line comment that `EvolutionConfig.use_text_feedback` is the legacy hydra-path field, dead for the orchestrator and carrying no spoil semantics. Default value unchanged.

### 5.2 Doc deletions + the leak-proof-evaluator teaching

**(5.2-a) — `CLAUDE.md` Boot bullet (~26–33).** Rewrite. Delete "and it must not spoil the metric," the "spoiling-risk self-check" sentence, the "`use_text_feedback`, default on" clause, and "the complete mitigation is `use_text_feedback:false`." New text:
> *Boot is your first critical-path job.* You author the `task_sys_msg` (goal + hard constraints + the score SHAPE + an abstract runtime caution). The harness **refuses to start** while `task_sys_msg` is missing/empty or still the `__UNSET_AUTHOR_AT_BOOT__` sentinel — that guard only ensures a goal was authored. **Leak-proofing is the evaluator design done at task SETUP, not an inner-loop concern:** any held-out / gate-defining number belongs in the evaluator `private` metrics (never `public` — only `public` reaches the prompt), so a passing+improving candidate is always a good candidate. Full evaluator text feedback is always fed to the inner loop because it helps. (See SKILL.md "Boot"; shinka-setup / shinka-convert for the evaluator-leak-proof design.)

**(5.2-b) — `SKILL.md` roles intro (18–19).** Replace "without spoiling the held-out metric" → "the leak-proof design lives in the evaluator (held-out numbers under `private` metrics), not the system message."

**(5.2-c) — `SKILL.md` Boot heading + steps (304 heading, 306–322).** Rename the heading "Boot: author the goal (no-spoil) + a spoiling self-check" → **"Boot: author the goal + objective."** In step 1, delete "no-spoil" from the runtime-caution parenthetical. **DELETE step 2 entirely** ("Do NOT spoil the eval criterion … the spoiling self-check … mitigation is `use_text_feedback:false`…") and replace with a NOTE:
> *Leak-proofing is the evaluator design, set at task SETUP (see shinka-setup / shinka-convert):* held-out and gate-defining numbers go under the evaluator `private` metrics dict (only `public` reaches the prompt via `perf_str`), and `text_feedback` describes a failure without handing over a target. Once the evaluator is leak-proof, every passing+improving candidate is good, and the harness always feeds evaluator text feedback to the mutation/fix/meta prompts (it speeds convergence).

**(5.2-d) — `SKILL.md` mutable-levers table (749).** DELETE the entire `use_text_feedback` row (the knob no longer exists on the orchestrator surface).

**(5.2-e) — `SKILL.md` run-config schema (~686 area, the `evo`/`task` object).** If a `use_text_feedback` key appears in the documented `evo` schema, remove it. Add no replacement knob. (The grounding-run block edits in 5.2-i below; the 4.3 `task.objective_brief` schema add is in Point 4.3's doc edits.)

**(5.2-f) — `shinka-setup/SKILL.md:58` + the evaluator-authoring section.** Replace "WITHOUT spoiling the held-out metric" → "the evaluator must be leak-proof — held-out / gate-defining numbers go under the `private` metrics dict (only `public` metrics reach the inner loop), and `text_feedback` describes failures without revealing a target." Add a one-line note in the `evaluate.py`-authoring guidance: "Leak-proofing is an evaluator-design responsibility: never return a held-out target in `public` metrics or `text_feedback`. `perf_str` renders only `public` — put gate-defining numbers under `private`."

**(5.2-g) — `shinka-convert/SKILL.md:81`.** Replace "authors a real goal (no-spoil)" → "authors a real goal." Add to the `evaluate.py`-generation step: "make the evaluator leak-proof — held-out numbers under `private` metrics, only `public` surfaced to the inner loop."

**(5.2-h) — `configs/README.md:23`.** Replace "a no-spoil `task_sys_msg`" → "a `task_sys_msg` authoring the goal." If the README documents `use_text_feedback`, remove that mention.

**(5.2-i) — `tasks/cnot_grid_synth/README.md:65–66`.** Keep the factual note that the README is never fed to the LLM, but reframe: replace "so it does not spoil the search" → "consistent with the leak-proof-evaluator design — the held-out `depths_per_L` stays under the evaluator `private` metrics and the paper target is not surfaced to the inner loop; correctness is enforced by `evaluate.py`'s authoritative checks."

> The grounding-engineer subagent file and the Point-1 grounding lines are reconciled in the **Reconciliation patch-list** below (they are part of the v2 body / the new subagent file, not Point 4/5 proper).

### 5.3 Tests

**(5.3-a) — DELETE the two no-spoil tests + registrations (`test_improvements.py`).** Remove `test_no_spoil_blanks_ancestor_inspiration` (def ~`:2290`, registered `:3418`) and `test_no_spoil_meta_blanks_error_text` (def ~`:2311`, registered `:3419`) and their two registration-list entries (or the runner `NameError`s).

**(5.3-b) — REPURPOSE `test_fix_prompt_reads_only_metadata_channels` (~`:653`).** Drop the docstring's "COMPLETE spoil mitigation" framing and the assertion that relied on blanking. Keep it as a contract test that the fix prompt's error section comes from the parent's `stdout_log`/`stderr_log` metadata: assert the marker IS present when the channel is populated, absent when empty — **no `use_text_feedback` flag.**

**(5.3-c) — REWORD `test_c2_runtime_budget_caution` (~`:676` docstring, `:706`).** Remove the `use_text_feedback=False` kwarg at `:706` (the knob no longer exists) and drop the "survives `use_text_feedback=false`" phrasing from the docstring. Assert `# Runtime budget` is present for the slow case without the flag (the caution is numeric and unconditional).

**(5.3-d) — KEEP `test_boot_guard` (~`:602`).** It asserts refuse-to-start behavior + the sentinel in the starter — none of which change. Stays green after the 5.1-e wording reframe (it does not assert the spoil wording).

**(5.3-e) — optional positive inversion.** Add a test that evaluator text IS fed to the fix prompt regardless of any flag (positive inversion of the deleted no-spoil tests). Plus the D4 `test_fix_mode_ancestors_incorrect_only` (4.6-c-4), added after 5.3-a.

**Net test delta:** baseline 97 → 2 deleted (5.3-a) + 2 repurposed (5.3-b/c) + 1–2 added (4.6-c-4, optional 5.3-e) → green.

---

## Ordered change list (every edit: file → location → exact change; DOC/CODE + mutability; landing order)

> **Landing order (blocking).** Apply as ONE coordinated wave. The hard conflict point is `sampler.py` — edited by **4.1-A (header), 4.2 (no `include_code` — unchanged here), 4.3-C (objective kwarg), 4.4-B (creative preamble), 4.6-c-3 (fix comment), AND 5.1-h (`use_text_feedback` default → True)** — these MUST be one atomic rewrite of `sample()`/`sample_fix()`, never competing diffs, or `import shinka.core.sampler` breaks on EVERY run. The `run_window.py` fix sites are the second conflict point: **D4 (4.6-c-1/c-2) and D5 (5.1-b/c) edit the same immediate-fix and repair blocks** — apply each block once, combining the incorrect-ancestor filter with the `_utf` removal.

1. **`shinka/core/sampler.py`** (CODE, framework-between-runs) — atomic `sample()`/`sample_fix()` rewrite: add imports `objective_section`, `EXPERT_CREATIVE_PREAMBLE`; `sample()` gains `objective_brief: Optional[str] = None`; append `EXPERT_CREATIVE_PREAMBLE` on no-direction diff/full (4.4-B); replace the weak header with `# Direction for this attempt` (4.1-A); build `metric_block = objective_section(...) + perf_str(...)` in all three branches (4.3-C); `sample_fix` gains `objective_brief` rendered into its SYSTEM msg + the D4 incorrect-only comment (4.6-c-3) — **`correct=False` stays, no relabel**; **`__init__` default `use_text_feedback=True`** (5.1-h). **No `include_code` parameter** (D3) and **no grounding kwargs** (1h dropped).
2. **`shinka/prompts/prompts_base.py`** (CODE, framework-between-runs) — `construct_eval_history_msg` `correct=True` header → EVAL-HISTORY reframe (4.2-A) + per-program header reword (4.2-B) + `correct=False` typo fix (4.2-C) + docstring incorrect-only note (4.6-c-3); add `objective_section` helper (4.3-A); add `EXPERT_CREATIVE_PREAMBLE` (4.4-A).
3. **`shinka/prompts/__init__.py`** (CODE) — export `objective_section`, `EXPERT_CREATIVE_PREAMBLE`. **DROP** any `FULL_SYS_FORMAT_NAMES`/`FULL_ITER_MSG_GROUNDING` grounding exports (1h dropped; no grounding helper).
4. **Mode templates** (CODE, framework-between-runs) — `prompts_full.py` `# Task` creative sentence (4.4-C); `prompts_diff.py:2` + `prompts_cross.py:13` + `prompts_init.py:7` `repond → respond`, `prompts_diff.py:2` `a edit → an edit` (4.6-b).
5. **`orchestrator/scripts/construct_mutation_prompt.py`** (CODE, mutable-strategy) — **DELETE** `_strip_eval_text`/`_EVAL_TEXT_KEYS` block + hardcode `use_text_feedback=True` + reword docstring/comments (5.1-a); first-line splitter (4.1-C); read+thread `objective_brief` (4.3-D); fix-branch docstring/comment to incorrect-only (4.6-c-3). **No nested-metadata strip** (4.6-d dropped).
6. **`orchestrator/harness/run_window.py`** (CODE, harness-plumbing) — immediate-fix: D4 incorrect-ancestor gate (4.6-c-1) + D5 `_utf` removal / always-populate channels (5.1-b); repair: D4 ancestry filter (4.6-c-2) + D5 `_utf` removal / unconditional `stderr_log` backfill (5.1-c); delete `use_text_feedback` from mutation + meta payloads (5.1-d); BOOT-guard wording reframe (5.1-e); add `"objective_brief": task.get("objective_brief"),` to the mutation payload (4.3-E). **No grounding payload keys** (1h-CODE-5 dropped).
7. **`orchestrator/scripts/meta_summarize.py`** (CODE, mutable-strategy) — delete `_utf` gate, always show `error_traceback` in `_err_reason` (5.1-f).
8. **`orchestrator/scripts/record_policy.py`** (CODE, mutable-strategy) — reword `:57–59` comment (5.1-g).
9. **`configs/orchestrator_run.default.json`** (CONFIG) — delete `"use_text_feedback": true,` (5.1-i).
10. **`shinka/core/config.py`** (framework — LEAVE) — optional comment only; default unchanged (5.1-j).
11. **`orchestrator/tests/test_improvements.py`** (test) — delete 2 no-spoil tests + 2 registrations (5.3-a); repurpose `test_fix_prompt_reads_only_metadata_channels` (5.3-b); reword `test_c2_runtime_budget_caution` (5.3-c); add `test_fix_mode_ancestors_incorrect_only` (4.6-c-4) + the Point-4 prompt-shape assertions below + optional positive feedback test (5.3-e).
12. **DOC edits** — `CLAUDE.md` (Boot bullet 5.2-a; automatic-meta one-direction sentence 4.1; objective_brief + auto-preamble Boot sentences 4.3/4.4; grounding hand-authoring rewrite per D1/D2 — see Reconciliation); `SKILL.md` (5.2-b/c/d/e; Boot step 1 objective_brief; grounding-run hand-authoring recipe + both verbatim templates + default `azure-gpt-5.5@medium`; creative-no-direction note); `shinka-setup/SKILL.md` (5.2-f); `shinka-convert/SKILL.md` (5.2-g); `configs/README.md` (5.2-h); `tasks/cnot_grid_synth/README.md` (5.2-i); `subagents/grounding-engineer.md` (four authored ingredients, two executors, default `azure-gpt-5.5@medium`, leak-proof reword — see Reconciliation).

---

## Verification / acceptance

**Greps (must pass):**
- `prioviously` → 0 hits in `shinka/prompts/`; `repond` → 0 hits.
- `# Potential Recommendations` → 0 hits in `shinka/core/sampler.py`; `# Direction for this attempt` present.
- `_strip_eval_text` / `_EVAL_TEXT_KEYS` → 0 hits in `orchestrator/scripts/`.
- `use_text_feedback` → 0 hits in `orchestrator/` (scripts + harness) and `configs/orchestrator_run.default.json`; present only as the `PromptSampler` parameter in `shinka/core/sampler.py` (defaulted True) and the untouched legacy `EvolutionConfig` field in `shinka/core/config.py`.
- Spoil/held-out sweep — `spoil`, `no-spoil`, `held-out` (as a hide-it-from-the-loop directive) → 0 hits across `CLAUDE.md`, `SKILL.md`, `shinka-setup/SKILL.md`, `shinka-convert/SKILL.md`, `configs/README.md`, `tasks/cnot_grid_synth/README.md`, `subagents/grounding-engineer.md`, and this plan (only the Point-5 deletion instructions + the leak-proof NOTE remain).
- `eval history` / `reference only — do not copy` present in `construct_eval_history_msg`'s `correct=True` branch; the fenced `prog.code` line still present (D3 keeps code).
- No `construct_grounding_prompt.py` anywhere (D1 dropped); `grep` of `mutate.py` confirms it imports neither `sampler` nor `PromptSampler` (the hand-authoring transport).

**Import / regression:**
- `python -c "import shinka.core.sampler"` succeeds (all new symbols exported) — no half-applied break.
- `C:/Users/dtlic/miniconda3/envs/shinka/python.exe -m pytest orchestrator/tests -q` stays green (or the env interpreter from `conda run -n shinka which python`).

**New / changed assertions (`test_improvements.py`):**
- **D3:** a diff/full mutation prompt MUST contain the inspiration code substring (e.g. the fixture's `y = 2`) AND the eval-history label (e.g. "eval history" / "reference only"), and MUST NOT frame priors as "inspiration to combine." cross `patch_msg` still contains its `get_cross_component` partner code. (This **inverts** the prior addendum's "diff/full does NOT contain inspiration code" assertion.)
- **4.3:** with `task.objective_brief` set, the prompt shows `# What we are optimizing` once; unset ⇒ byte-identical to baseline.
- **4.4:** no-direction diff AND full `patch_sys` contains `# Expert framing`; with a direction present it does NOT (and `# Direction for this attempt` is present); cross never gets `# Expert framing`.
- **D4:** `test_fix_mode_ancestors_incorrect_only` — `[]` for a correct `learn_from`, non-empty for an incorrect one; repair-mode filter drops correct ancestors.
- **D5:** evaluator text IS fed to the fix prompt with no flag (positive inversion); the C2 runtime caution renders without `use_text_feedback`.

**No foundation drift.** Untouched: sqlite schema, the scripts' JSON OUTPUT contract (`construct_mutation_prompt` still returns `{ok,patch_sys,patch_msg,patch_type}`), `evaluate.py`, user's `evaluate.py`/`initial.*`, `cadence_policy.py`, termination logic, `_azure.py` wall, `EvolutionConfig.use_text_feedback`. The boot refuse-to-start guard is KEPT (only its wording de-spoiled).

---

## Decisions and rationale

- **D1 — grounding is a docs-only hand-authoring recipe; `construct_grounding_prompt.py` is DROPPED.** Verified `mutate.py` reads `patch_sys`/`patch_msg`/`patch_type` directly and never imports the sampler, so a hand-authored full prompt pipes straight through. A new script would add a mutable-strategy file for zero benefit. Net new grounding code = 0 lines.
- **D2 — default grounding model `azure-gpt-5.5@medium`; `azure-gpt-5.4-pro@high` optional escalation.** Harmonizes with the per-window meta default and sidesteps the pro-rejects-`low` gotcha (the recipe pins `medium`/`high`).
- **D3 — keep prior-program CODE in non-cross; relabel as low-attention EVAL HISTORY.** Reverses the prior `include_code=False` gating. A pure header/wording change in the `correct=True` branch — no `include_code` parameter, no `sampler.py` change, no signature churn. Cross's genuine crossover framing (in `get_cross_component` + `CROSS_*`) is provably outside the edited region, so the change is default-preserving for cross.
- **D4 (REVISED 2026-06-16 per owner) — FIX becomes a first-class sampled MODE at 5%, taken from diff; sample the MODE first, then the PARENT conditioned on it.** Final mode distribution `[diff 0.55, full 0.30, cross 0.10, fix 0.05]` (diff 0.60→0.55). `run_window` samples the 4-way mode BEFORE the parent: if `fix`, request an INCORRECT parent (`sample_parent select="errored"`, `needs_fix=True`, routed to `sample_fix`); if `diff`/`full`/`cross`, sample a CORRECT parent and FORCE that mode into `sampler.sample` (no internal re-draw). The existing errfrac `repair` latch still forces fix. Fallbacks: `fix` with no errored parent in the pool → `sample_parent` returns a correct parent (`needs_fix=False`) and the slot falls back to a diff/full/cross draw; a forced `cross` with no inspirations keeps the sampler's existing cross-suppression. This SUPERSEDES the prior "filter fix-mode ancestors to incorrect-only" framing (that only touched the learn-from list; the owner wants fix-mode to deliberately operate on incorrect PARENTS).
- **D5 — full spoil/no-spoil removal; `use_text_feedback` DELETED from the orchestrator surface, KEPT as a framework `PromptSampler` parameter defaulted True.** Decisive call over the "keep as verbosity knob" alternative: in `sampler.py` the flag also gates the legit `text_feedback`/eval-history signal we now want always on, so a residual orchestrator knob would still gate help, not just verbosity. Always feeding evaluator text speeds convergence. `EvolutionConfig.use_text_feedback` (legacy hydra path, dead for the orchestrator) is left untouched to avoid breaking upstream-compat yamls. The boot refuse-to-start guard is KEPT (ensures a goal was authored — not a spoil rule). 4.6-d (nested-metadata strip) and 4.6-e (C2 "spoil-safe" note) are moot once `_strip_eval_text` is gone, and are dropped. Leak-proofing moves to evaluator design at task setup — already how the live evaluator works (`perf_str` renders only `public`; cnot's `depths_per_L` is `private`).
- **`EvolutionConfig.use_text_feedback` scope (REVISED 2026-06-16 per owner — DELETE it too).** The owner's directive: "don't leave any legacy docs or fields; if it's outdated, remove it to avoid future confusion." So the legacy `EvolutionConfig.use_text_feedback` field (`shinka/core/config.py:56`) and the `format_text_feedback` toggle plumbing it feeds (the hydra `load_configs_from_yaml` path) are **DELETED**, not preserved — along with any `use_text_feedback` mention in the shipped task/evolution YAMLs under `shinka/configs/`. Removing a now-unused dataclass field does not break the JSON OUTPUT contract or the live orchestrator (which never reads `EvolutionConfig`). Any stale doc passage is removed outright rather than annotated.

---

### Reconciliation patch-list for the v2 body (Points 1–3, 1h, subagent)

Apply these so the whole document is internally consistent with D1–D5. Each is a residual spoil/held-out/grounding-helper reference outside Point 4/5 proper.

1. **Top banner (line 5)** — the `➕ Point 4 added` note. Update its grounding sentence to: "grounding is an orchestrator **hand-authored** prompt (seed + detailed direction + objective + same-I/O instruction) fed to `mutate.py` or the grounding-engineer subagent; it never enters the sampler." Append: "**Point 5 (spoil removal)** added 2026-06-16 — see the Addendum tail." No `construct_grounding_prompt.py` reference.

2. **Summary table + DOC/CODE balance (lines 28–39)** — add a **Point 5** row (spoil removal: CODE `construct_mutation_prompt.py`/`run_window.py`/`meta_summarize.py`/`record_policy.py`, CONFIG `orchestrator_run.default.json`, TESTS `test_improvements.py`, DOC `CLAUDE.md`+`SKILL.md`+`shinka-setup`+`shinka-convert`+`configs/README`+`cnot README`). Re-word the "Only 1h carries code" balance line: Point 4 (4.1–4.4 framework code + D4/D5 strategy/plumbing) + Point 5 carry code; grounding (D1) is docs-only.

3. **Line 117 — the 1h "No-spoil gate unaffected" paragraph.** **DELETE** (the 1h sampler retrofit is superseded and dropped; the paragraph is moot AND contradicts D5). Note its removal in the dropped-1h list.

4. **Line 195 — grounding-engineer subagent draft, "NEVER any held-out numbers."** Reword to: "the task spec + the score SHAPE; you author the pivot code freely — leak-proofing is the EVALUATOR's job at setup (Point 5), not a prompt-hiding rule."

5. **The NEW grounding-engineer subagent FULL FILE (lines 172–257) — when authored.** (i) Make its "What you are given" inputs the FOUR hand-authored ingredients (INIT seed `initial.<ext>`; DETAILED direction + reference pointers; the objective/score-shape = `task.objective_brief`; the same-I/O instruction). (ii) State the subagent and the Azure-via-`mutate.py` path are TWO executors of the identical hand-authored prompt. (iii) Default any Azure fallback to **`azure-gpt-5.5@medium`** (D2). (iv) Remove any `grounding_mode`/`full_variant` sampler-lever reference (1h dropped). (v) Replace the line-195 "NEVER any held-out numbers" per patch 4 above. (vi) KEEP the parity contract (embed → `archive_record` → `spawn_island`) and the "score-0 on a first injection is EXPECTED" teaching.

6. **Point 1 grounding lines (e.g. line 17 "WORKING CODE", line 34, the 1g trigger, the 1cd-1 grounding subsection ~322–333).** Wherever a grounding direction is described as carrying "never held-out numbers" or routed through `evo.meta_directions`/`grounding_parent_id`/`grounding_full_variant`/`grounding_mode`, reword to the hand-authoring frame: "the orchestrator hand-authors `patch_sys`/`patch_msg` (seed + detailed direction + objective + same-I/O instruction, `patch_type:"full"`) and feeds them to `mutate.py` (default `azure-gpt-5.5@medium`) or the subagent — bypassing the sampler; leak-proofing is the evaluator's job (Point 5), so the prompt carries only the goal SHAPE + technique + refs." Drop any `evo.grounding_*` lever names.

7. **1h DOC edits in the v2 body (1h-DOC-1…4, lines ~351–363) and 1h-CODE-1…5 (lines ~365–436).** These are already inside the dropped-1h block; ensure none survive as live instructions — the `sampler.sample(full_variant=, grounding_mode=)` levers, `FULL_ITER_MSG_GROUNDING`, `FULL_SYS_FORMAT_NAMES` exports, and the `construct_mutation_prompt`/`run_window` grounding-payload threading are all DROPPED. Any cross-ref to "framework prompt-mode levers (grounding)" in `1h-DOC-2`/`1h-DOC-3` (SKILL.md `~488–505`, `798–801`) reframes to the hand-authoring recipe.

8. **SKILL.md grounding-run block (the live `~480–505` region, "History-similar" vocabulary + `evo.grounding_parent_id` recipe).** Replace with the D1 hand-authoring recipe + both verbatim `patch_sys`/`patch_msg` templates + the two-executor + parity-tail text + default `azure-gpt-5.5@medium`; delete the `evo.meta_directions`/`grounding_parent_id`/`grounding_island_idx`/`mutation_web_search`/`grounding_full_variant`/`grounding_mode` grounding-run levers; explicitly note it BYPASSES the sampler so none of the four scaffolding pulls can occur.

9. **CLAUDE.md "Patching the framework" / framework-audit-grounding bullet.** Replace any "grounding prompt-mode levers (`sampler.sample(full_variant=, grounding_mode=)`)" wording with: "Grounding is a HAND-AUTHORED full-rewrite prompt — seed=`initial.<ext>` + a detailed direction with reference pointers + the boot-authored objective/constraints + a same-I/O instruction + the `FULL_SYS_FORMAT_DIFFERENT` response format — fed to `mutate.py` (`azure-gpt-5.5@medium` default; pro@high optional) OR `subagents/grounding-engineer.md`. It does NOT enter the diff/full/cross sampler. No `construct_grounding_prompt.py` and no sampler changes."

10. **"Files this plan edits" (lines ~510–525) + the prior-addendum "Open questions" (lines 900–910) and the v2-body "Open questions" (lines 498–511).** In the files list: REMOVE `construct_grounding_prompt.py`; ADD `meta_summarize.py`, `record_policy.py`, `configs/orchestrator_run.default.json`, `shinka-setup/SKILL.md`, `shinka-convert/SKILL.md`, `configs/README.md`, `tasks/cnot_grid_synth/README.md`, `orchestrator/tests/test_improvements.py`. **REPLACE both "Open questions" sections with this Addendum's single "Decisions and rationale" list** — every prior open question (DR quota; finalize gate; 1h island_brief/variant; grounding helper form; default grounding model; non-cross eval-history form; FIX `correct=` granularity; nested-metadata strip) is now decided (DR/finalize stand from Points 1/3; 1h dropped; grounding = recipe + `5.5@medium`; non-cross = keep code, relabel; FIX = incorrect-only; nested strip = moot).

11. **Prior-addendum "Must also change" re-baseline bullets (lines ~889–896) and 4.6 sweep rows d/e (lines 807–808).** Already folded above: lines 33/37 (summary table) per patch 2; line 95 (axis-3) re-homes to 4.1; lines 359/392/481 (`# Potential Recommendations` byte-identity) re-baseline to `# Direction for this attempt`; the `construct_grounding_prompt.py` test/grep assertions are DELETED (file dropped); 4.6 rows d and e are struck.

**Files this revised Addendum edits (all absolute, under `C:/Users/dtlic/Documents/GitHub/ShinkaEvolve/.claude/worktrees/wizardly-booth-ad6a2d/`):** `CLAUDE.md`; `.claude/skills/shinka-orchestrator/SKILL.md`; `.claude/skills/shinka-orchestrator/subagents/grounding-engineer.md` (NEW); `.claude/skills/shinka-setup/SKILL.md`; `.claude/skills/shinka-convert/SKILL.md`; `configs/README.md`; `configs/orchestrator_run.default.json`; `tasks/cnot_grid_synth/README.md`; `shinka/core/sampler.py`; `shinka/prompts/prompts_base.py`; `shinka/prompts/prompts_full.py`; `shinka/prompts/prompts_diff.py`; `shinka/prompts/prompts_cross.py`; `shinka/prompts/prompts_init.py`; `shinka/prompts/__init__.py`; `orchestrator/scripts/construct_mutation_prompt.py`; `orchestrator/scripts/meta_summarize.py`; `orchestrator/scripts/record_policy.py`; `orchestrator/harness/run_window.py`; `orchestrator/tests/test_improvements.py`; this plan. **`shinka/core/config.py`** is touched comment-only/left as-is. **No** `construct_grounding_prompt.py`. **FOUNDATION untouched.**

---

**Replacement boundary:** the Markdown above replaces everything from the `# Addendum — Point 4 (Prompt-construction reform), added 2026-06-16` banner (line 529) through the end of `docs/FIX_PLAN_RUN_POSTMORTEM_20260616.md` (line 910). The v2 body (lines 1–528) stays, amended per the Reconciliation patch-list (patches 1–11). Key load-bearing facts verified against live source: `construct_mutation_prompt.py:77–93` strip block + `:82,:104` `use_text_feedback` reads; `prompts_base.py:39–81` `construct_eval_history_msg` (`prioviously` typo at `:49,:54`, `# Prior programs` at `:60`, fenced `prog.code` at `:61`); `mutate.py` reads `patch_sys`/`patch_msg`/`patch_type` directly and never imports the sampler; `perf_str` renders only `public_metrics`.
