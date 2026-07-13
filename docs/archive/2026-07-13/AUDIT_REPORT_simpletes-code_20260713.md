> **STATUS: APPLIED (2026-07-13).** Historical record of the three-track audit
> (upstream parity / SimpleTES / concurrency) and its implementation batch.
> Live guidance is CLAUDE.md + .claude/skills/shinka-orchestrator/SKILL.md.

# SimpleTES Code Audit — Reader Report

All paths below are relative to the SimpleTES clone root (`...scratchpad/simpletes/`); code files are under `simpletes/`.

## 1. Data model

**Node** (`simpletes/node.py:42-59`) is a dataclass with: `id` (uuid4 hex, `engine/runtime.py:57`, `engine/core.py:495`), `code` (full program text, not a diff), `parent_ids: list[str]` (multi-parent → the archive is a **DAG**, not a tree), `gen_id` (batch id), `chain_idx`, `shared_construction_id`, `metrics` (dict; score comes from `metrics["combined_score"]`, coerced to `-inf` on error/NaN, `node.py:20-28`), `score`, `status` (`EVAL_PENDING`/`DONE` only, `node.py:31-39`), `created_at` (ISO timestamp), plus `llm_input`, `llm_output`, `token_usage` (saved when `save_llm_io`), and `reflection` (short Approach/Insight text).

**Storage** is `NodeDatabase`: a plain in-memory `dict[str, Node]` with a lazily sorted-by-score cache and a `_version` counter (`node.py:137-212`). **There is no SQL database** — persistence is JSON checkpoints (`engine/checkpoint.py`). The graph is stored only child→parent via `parent_ids`; there are no children pointers — RPUCG rebuilds a parent→children map O(n) on every selection (`policies/rpucg.py:100-108`). Read-only `NodeDatabaseSnapshot`s support lock-free concurrent reads (`node.py:200-246`).

**Chains** are a first-class concept in `policies/base.py` (`TrajectoryPolicyBase`): `num_chains` independent histories `self.chains: dict[int, list[str]]` (`base.py:240`). The root (first parentless node, else global best) is seeded into every chain (`base.py:270-283`). After each batch of K candidates, **only the best-of-K child with finite score is committed to the chain** (`base.py:512-515`) — and chains filter `-inf` at selection (`base.py:588`). Two failure paths, only one of which reaches the DB: a child whose evaluation crashed/timed out gets metrics `combined_score = -inf` (`evaluator.py:221, 238, 270, 292`) and is **rejected from the DB entirely** by `validate_node_for_db` (`node.py:123-134` — `_require_finite_number` raises on `-inf` despite the adjacent comment), landing in `_failure_records` and closing the batch via `on_generation_failed` (`core.py:456-486`); a child whose metrics carry an `error` key alongside a *finite* `combined_score` has its `score` coerced to `-inf` (`node.py:20-28`), **does** enter the DB, but never joins a chain. Each chain gets a generation budget of `max_generations // num_chains` (remainder distributed +1 to the first chains; `compute_chain_budgets`, `base.py:174-180`) and a prompt budget `ceil(budget/k)` (`base.py:234-237`); exhausted chains leave `_ready_chains` (`base.py:527-529`).

## 2. RPUCG as implemented (`policies/rpucg.py`)

**Value initialization:** none persisted (visit counts and expansion totals *are* checkpointed, `rpucg.py:254-264`, but V-values never are). V-values are recomputed **from scratch on every `select()`** over the whole population snapshot — there is no incremental backprop:

```python
# rpucg.py:117,129-137  V(s) = max(raw_reward(s), γ · max_child V(c))
raw = node.score if node.score is not None else -float("inf")
if child_ids:
    max_child_v = max((v.get(cid, -float("inf")) for cid in child_ids), default=-float("inf"))
    v[node.id] = max(raw, self.gamma * max_child_v)
else:
    v[node.id] = raw
```

Nodes are processed in **reverse `created_at` order** so children are visited before parents (`rpucg.py:121-127`) — correctness relies on the timestamp ordering, not a topological sort.

**Normalization:** both Q and P are **global percentile ranks in [0,1)** over the entire population (`bisect_left(sorted_vals, val)/n`, `rpucg.py:139-149`): Q = rank of V(s), P = rank of raw score (`rpucg.py:186-191`). This removes PUCT's scale factor.

**Selection math** (`rpucg.py:199-202`), computed for chain-committed nodes only but ranked globally:

```python
q = q_rank.get(node.id, 0.0)
p = p_rank.get(node.id, 0.0)
nc = visit_counts.get(node.id, 0)
score = q + self.c * p * math.sqrt(1 + total_expansions) / (1 + nc)
```

`visit_counts` and `total_expansions` are **per-chain** (`rpucg.py:89-90, 194-195`).

**Propagation on evaluation:** when a batch's last child lands, `_finalize_hook_locked` gives each inspiration (parent) +1 visit and bumps the chain's `total_expansions` by 1 (`rpucg.py:237-248`). No value backprop happens here — values re-derive from scores at next select.

**Anti-inbreeding multi-parent selection** (`rpucg.py:213-231`): after sorting by the score above, greedily pick nodes; after each pick, exclude its 1-hop neighborhood (self + `parent_ids` + children) from later picks. Only rpucg does this.

**Root handling:** inherited — root seeded into all chains (`base.py:270-283`); with only the root committed, selection returns the root. Fallback if the DB snapshot isn't stashed: top-n by score (`rpucg.py:179-182`).

**Pruning/expansion rules:** there is **no pruning**. Chains grow only via best-of-K commits; error children never enter chains. There is no tree "expansion" step — every select is over the committed chain history.

**Diff vs `policies/puct.py`:** PUCT uses (a) Q = `max(node.score, max_child_reward[node.id])` where `max_child_reward` is a 1-level, per-chain, incrementally-updated map (`puct.py:88, 133-136`) — no γ-decay, no full-DAG propagation; (b) a raw-score scale factor `scale = max(r_max - r_min, 1e-6)` from the chain's score range (`puct.py:70-72`); (c) a linear rank prior `P(s) = (n_nodes - rank)/rank_sum` within the chain (`puct.py:91`); formula `Q + c*scale*P*sqrt(1+T)/(1+n)` (`puct.py:97-101`); (d) plain top-n, no anti-inbreeding (`puct.py:108-119`). rpucg also swaps the reflection template for a two-line SEv2-style one (`rpucg.py:31-45`) vs the two-paragraph default (`templates/reflection.py:5-19`).

`llm_puct`/`llm_rpucg` (`policies/llm_refine.py:120-135`) wrap these: base policy shortlists `llm_policy_pool_size` candidates, then an LLM reranker (temperature 0.0, max_tokens 2048, `#SELECTED: i,j` parse, top-n fallback) prunes to n (`llm_refine.py:35-75, 100-117`). The constructor default is 20 (`llm_refine.py:124,133`), but **when created via the engine that default is bypassed**: `core.py:129` always passes `config.llm_policy_pool_size` (CLI default `None`, `cli.py:207-210`), and unlike `llm_elite` there is no `None`-handling — the shortlist size becomes `None` (unbounded slice for `llm_puct`; for `llm_rpucg` the `len(selected) >= n` comparison in `rpucg.py:224` would raise `TypeError`) unless `--llm-policy-pool-size` is set explicitly.

## 3. Prompt builder

**Correction to the brief:** `simpletes/construction.py` is **not** the prompt constructor. It is the "shared construction" artifact system — JSON-serializing a numpy/tuple/dict artifact captured from the chain-best program and re-injecting it into eval subprocesses as the builtin `GLOBAL_BEST_CONSTRUCTION` (`construction.py:25-27, 190-191`), plus a ≤1200-char text summary for the prompt (`construction.py:38, 194-247`). Prompt building lives in `simpletes/generator.py` + `simpletes/templates/generation.py`.

`Generator.build_prompt` (`generator.py:242-348`):

- **Node selection rule:** the policy's `select()` picks inspirations (count = `num_inspirations` default 5, or sampled uniformly in `[min,max]_inspirations_cnt`, `base.py:299-315`); the builder just receives them.
- **Ordering:** inspirations sorted by score **descending** and numbered 1..n (`generator.py:255-265`).
- **Per-node serialization** (`generator.py:87-117`, template `templates/generation.py:5-15`): score, the full metrics dict (floats at 6 dp; `error` summarized to 240 chars, `generator.py:26, 99`), optional `Reflection:` block, and the **complete code in a fenced block**. **No diffs anywhere** — the LLM returns a whole new evolve block; the final program is rebuilt as `EXACT_PREFIX + evolved_block + EXACT_SUFFIX` (instruction in `generation.py:26-40`; actual reconstruction in `utils/code_extract.py:113-115`).
- **Other sections:** available packages from the task `requirements.txt` (`generator.py:278-285`); shared-construction summary + usage caveats (`generator.py:288-296`); per-chain failure histogram, top-10 errors with frequency % (`base.py:317-328`, `generator.py:120-150`); optional `policy_context` (elite-pool overview for `llm_elite`, `templates/elite_context.py`).
- **Context budget management: none on the prompt side.** Inspiration code is never truncated; only error strings (240 chars) and reflections (10,000 chars, `base.py:26`) are clipped. The only token control is call-time: `max_total_tokens` caps *completion* tokens as `min(max_tokens, max(1, max_total_tokens - prompt_tokens))` (`llm/litellm_client.py:271-280`).
- **Cache-friendly prefix:** the prompt opens with constant content — instruction, generation rules, EXACT_PREFIX/EXACT_SUFFIX (`generation.py:23-40`) — so the head is stable across calls by construction, but **no prompt-caching flags are set** (grep for `cache_control`/`prompt_cach` finds nothing in `simpletes/`). With `num_inspirations == 0` a fully static prompt is cached and reused (`engine/scheduler.py:36-64`; requires `num_chains == 1`, `engine/core.py:171-174`).
- **rpucg gets a hand-rolled prompt variant** (`generator.py:303-322`): same structure but "In-context inspirations (sorted by score...)" and a closing "Try diverse approaches... Think outside the box." — it drops the default template's `=== REFERENCE SOLUTIONS ===` header and the `=== GENERATION STRATEGY ===` / "Prioritize NOVEL approaches" section (`generation.py:42-54`).

## 4. K samples + engine concurrency

**K:** each `select()` produces one prompt and a logical batch of `k_candidates` (default 4) samples sharing one `gen_id`. With `stream_k_candidates=True` (default) the batch is dispatched as **k independent k=1 queue tasks** with the same prompt/gen_id (`engine/scheduler.py:125-149`); otherwise one task with `n=k`.

**Engine:** one asyncio process (`engine/core.py:64`). Pipeline: `Scheduler → gen_queue → gen workers → LLM → pending node → eval_queue → eval workers → evaluator subprocess → DB` (`core.py:5`). Bounded queues sized `max(4096, concurrency*8)` (`core.py:14-15, 135-138`). `LocalRuntime.run` starts `gen_concurrency` gen workers and `eval_concurrency` eval workers as asyncio tasks (`engine/runtime.py:97-104`); actual LLM parallelism comes from a `ProcessPoolExecutor` of size `gen_concurrency` (spawn context) inside the LLM client (`llm/litellm_client.py:36-54`; wired in `llm/__init__.py:68`). Evaluations run as subprocesses via `EvaluatorWorker` with `eval_timeout` (default 3000 s).

**Overlap:** yes — generation and evaluation are fully concurrent (separate queues/workers). The scheduler loop (`scheduler.py:266-292`) keeps calling `_schedule_generation`; when nothing can be scheduled it waits on `_progress_event` with a 30 s timeout. Throttling is the policy-level **backpressure**: a chain is selectable only while its in-flight batch count ≤ `backpressure_multiplier` (default **0** ⇒ strictly one batch in flight per chain; `base.py:567-584`, `config.py:186-190`), so concurrency across the run comes from having multiple chains.

**Mid-flight state updates:** each finished eval is committed immediately under `_db_lock` (`core.py:720-791`), then `selector.on_child_done` runs outside the lock; when the batch's last child (or failure, `base.py:642-665`) arrives, `finalize_batch` does reflection (LLM, unlocked) then the locked commit: error-histogram update, best-of-K append to the chain, visit-count bump (`base.py:451-534`). Selection reads a version-cached DB snapshot (`core.py:352-359`), so new nodes influence the very next `select()`. The init program is evaluated `init_eval_repeats` (default 16) times concurrently, keeping the max score (`core.py:498-508`).

## 5. LLM client

`llm/litellm_client.py`: **LiteLLM** `completion()` (any of its providers) executed in the process pool; workers return picklable `("ok"|"error", payload)` tuples (`litellm_client.py:109-159`). Retry = LiteLLM `num_retries` from `--retry`, **default 0** (`litellm_client.py:194`, `config.py:142`). Temperature default **0.7** (`config.py:138`); `drop_params=True`; token param auto-resolved (`max_tokens` vs `max_completion_tokens`, `litellm_client.py:206-214`). `reasoning_effort` (default "medium") is passed **only for models containing "gpt-oss"** (`litellm_client.py:232-235`). **n>1 in a single call** is used for non-streamed batches (`completion(..., n=n)`), with fallback to n concurrent single calls if the provider errors (`litellm_client.py:337-368`). A 1-token `preflight` ping validates credentials at startup unless `--skip-preflight` (`litellm_client.py:297-313`, `main.py:79-80`). **No prompt-caching flags exist** anywhere in the package. Second backend: `vllm_token_forcing` (`llm/vllm_forcing.py`) — direct httpx to vLLM with two-phase GPT-OSS Harmony reasoning-budget forcing. Policy-side LLM calls are separate `litellm.acompletion`: reflection at temperature 0.7 / max_tokens 2048 (`base.py:356-424`); reranker at temperature 0.0 (`llm_refine.py:51-58`).

## 6. Config defaults (`simpletes/config.py:111-190`; CLI mirrors these, `cli.py`)

| Knob | Default |
|---|---|
| `selector` | `"balance"` (README calls `rpucg` "Paper-style; strongest single selector", `README.md:64`) |
| `num_chains` C | 4 |
| `k_candidates` K | 4 (`stream_k_candidates=True`) |
| `puct_c` | **0.5** (class defaults inside puct/rpucg are `c=1.0`, but the engine always passes `config.puct_c`, `core.py:121`) |
| `rpucg_gamma` γ | 0.8 |
| `num_inspirations` | 5; `min/max_inspirations_cnt` None |
| `max_generations` | 100; `early_stop_score` None |
| `gen_concurrency` | 1; `eval_concurrency` 4; `backpressure_multiplier` 0 |
| `init_eval_repeats` | 16 (reduce=max); `eval_timeout` 3000 s |
| model / temp / max_tokens | `gemini/gemini-2.0-flash`, 0.7, 32768; `timeout` 3000 s; `retry` 0; `max_total_tokens` None |
| vLLM forcing | `reasoning_budget` 32768, `response_budget` 16384, `context_window` 49152 |
| balance ratios | exploitation 0.7 / exploration 0.2 / elite 0.2 |
| `llm_policy_pool_size` | None → 15 for `llm_elite` (`llm_elite.py:88`); for `llm_puct`/`llm_rpucg` the constructor default 20 (`llm_refine.py:124,133`) applies only on direct instantiation — the engine passes the config value through (`core.py:129`), so an unset config reaches them as None (see §2) |
| reflection | dataclass default `reflection_mode=False` (`config.py:152`) but on by default via CLI (`not args.disable_reflection`, `config.py:230`); policy LLM defaults to the main model (`core.py:126-128`) |
| checkpoints | `output_path="checkpoints"`, `log_interval` 1024 evals, `save_llm_io=True` |

The interactive wizard suggests `num_chains=4, k_candidates=4, eval_concurrency=8, gen_concurrency=4` (`main_wizard.py:215-218`). **Explicit absences:** no sqlite archive, no islands/migration, no embeddings/novelty dedup, no diff-based mutation, no meta/summarization round, no cost ledger or budget-USD tracking, no prompt-caching flags, no per-node context-token budgeting.
