> **STATUS: APPLIED (2026-07-13).** Historical record of the three-track audit
> (upstream parity / SimpleTES / concurrency) and its implementation batch.
> Live guidance is CLAUDE.md + .claude/skills/shinka-orchestrator/SKILL.md.

# SimpleTES Paper Audit — Algorithm, Φ, RPUCG, K, Ablations, Cost

Source: `scratchpad/audit/simpletes_paper.pdf` (110 pp), text-extracted to `scratchpad/audit/paper_text.txt` (cited as `paper_text:LINE`). Code cross-checks in `scratchpad/simpletes/`. Paper title "Evaluation-driven Scaling for Scientific Discovery," arXiv 2604.19341v1, 21 Apr 2026 (`paper_text:3,85`).

## 1. Algorithm overview

The loop is Algorithm 1 "SIMPLETES" (`paper_text:335-354`). Notation: problem instruction `x0`, generator `G:X→Y`, evaluator `V:Y→ℝ×M`, initial solution `y0`, design-space parameters `(C,L,K,Φ)∈H` (`paper_text:336`). For a candidate `y`, `V(y)=(r,m)` where `r∈ℝ` is a scalar score/reward and `m∈M` is auxiliary feedback/metadata (verifier messages, error traces) (`paper_text:294-296`).

Core structure (`paper_text:338-354`):
- **Node** = tuple `(y,r,m)` (`paper_text:374`). **Trajectory** = a sequence of `L` refinement steps starting from `(y0,r0,m0)`, accumulating nodes into set `S` (`paper_text:373-374`).
- `function TRAJECTORY(S)`: for `ℓ=1..L`: `x←Φ(S)`; generate `K` candidates `{y_k}←G(x)`; evaluate each `(r_k,m_k)←V(y_k)`; commit **only** the argmax: `S←S∪{(y_k*,r_k*,m_k*)}` with `k*=argmax_k r_k` (`paper_text:339-348`).
- Run `C` **independent** trajectories in parallel from `S0`; return the global argmax-`r` node over `∪_c S_c` (`paper_text:351-354`).

Total evaluator-query budget `N = C·L·K` (`paper_text:219,685,4021`). Three scaling axes: global width `C` (parallel exploration), refinement depth `L` (feedback-driven refinement), local sample size `K` (best-of-K local selection) (`paper_text:218-222`). Named special cases: pure sequential refinement `π_seq=(1,L,1,Φ)` (`paper_text:381-382`); adding K converts allocation `(C,L,1,Φ)→(C,L/K,K,Φ)` (`paper_text:422`). Code mirror: `TrajectoryPolicyBase` with per-chain budgets, best-of-K commit in `_finalize_locked` (`simpletes/policies/base.py:489-534`).

## 2. Φ(S) / context construction

Φ is the **context-construction mapping** `Φ:FinSet(Y×ℝ×M)→X` (`paper_text:370,446`). It has two sub-problems: **history selection** (which nodes) and **prompt formatting** (how) (`paper_text:448-449`). Each prompt contains exactly four information types (`paper_text:508-517`):
1. plain task instruction `x0`;
2. evaluation configs (timeout, resource limits);
3. selected historical nodes from `S` — their **scores, evaluator feedback `m`, and concise summaries**;
4. optional auto-accumulated signals: repeated exceptions, timeout patterns, missing imports, `requirements.txt` package info, **LLM reflections over committed winners**, or validated warm-start artifacts.

By default Φ conditions on **multiple** historical nodes (recombination), explicitly contrasted with sequential refinement and prior work (ThetaEvolve [152] / TTT-Discover [166]) that condition on a **single** node (`paper_text:376-378,496-498`). The paper is minimalist: no expert hints, no hand-specified error patterns; adaptation moves into the search (`paper_text:507-508`). Selected nodes are added to inspiration set `S^(c)`; chain-local memory `R^(c)` holds failure patterns; reflection (Approach/Insight of the best node) is appended to inspirations (`paper_text:2960-2967`).

Code serialization (`simpletes/templates/generation.py`): per-node `INSPIRATION_TEMPLATE` renders `--- Inspiration {index} ---`, `Score`, `Metrics`, an optional `reflection_block`, then a fenced `Code` block (`generation.py:5-15`). `GENERATION_PROMPT_TEMPLATE` orders: Task instruction → generation rules → EXACT_PREFIX/SUFFIX → available packages → policy_context_section → `[SAMPLED INSPIRATIONS]` → `[FAILURE PATTERNS]` → generation-strategy bullets (`generation.py:23-55`). The rpucg reflection is a two-line **Approach:/Insight:** summary (`simpletes/policies/rpucg.py:31-45`), distinct from other policies' two-paragraph template.

## 3. RPUCG (default selector)

RPUCG = "graph-based extension of PUCT" (`paper_text:468`). Full formulas (`paper_text:471-493`):

**Propagated value** (Eq. 3): `U_i = max( r_i , γ · max_{j∈Ch(i)} U_j )`, where `Ch(i)` = all nodes that have included node `i` in their proposal (descendants inspired by `i`); `γ∈(0,1]` is the discount factor; second term ignored if `Ch(i)` empty (`paper_text:470-480`). Intuition: a node is valuable either because it already scores high, or because it inspired strong descendants (`paper_text:480-486`).

**Selection score** (Eq. 4): `RPUCG(i) = U_i + λ·ρ_i·√(1+|S|)/(1+n_i)`, where `λ` = exploration constant, `ρ_i` = relative prior of node `i` = its **score percentile within current `S`**, `n_i` = number of times `i` was previously included in a proposal's context (`paper_text:487-493`). Term 1 favors high propagated value; term 2 is the exploration bonus (small `n_i` ⇒ boosted) (`paper_text:494-495`).

**Differences from vanilla PUCT** (`rpucg.py:1-15` header; `paper_text:487-497`): (a) DAG-aware `U` propagates values bottom-up through the full parent→child graph with γ-decay, instead of a tree Q from rollout backups; (b) both `Q≡U-rank` and prior `ρ` are **global percentile ranks** over the whole population, so no scale factor is needed; (c) **anti-inbreeding** — Φ selects nodes greedily by RPUCG while **excluding the one-hop neighborhood** (self + parents + children) of already-selected nodes to reduce redundancy (`paper_text:495-496`; `rpucg.py:213-231`).

Code confirms: `V(s)=max(raw, γ·max_child V(c))` in `_compute_v_values` processed in reverse creation order (`rpucg.py:110-137`); percentile ranks via bisect (`rpucg.py:139-149`); score `q + c·p·sqrt(1+total_expansions)/(1+nc)` (`rpucg.py:202`). Note code uses **per-chain** `visit_counts`/`total_expansions` for the exploration term while `q`/`p` ranks are global — a subtlety vs. the paper's `|S|`/`n_i`.

**Why it beats alternatives:** ablation (Table 18) argues purely score-based (Balance) or Random selection is "myopic"; RPUCG's graph-based state-value estimation plus explicit explore/exploit balance, and LLM-elite's semantic insight, both beat naive baselines (`paper_text:2934-2944`). But numbers are close (see §5) and the paper concedes the primary driver is budget scaling, not the selector (`paper_text:2953-2958`).

## 4. K parallel samples

`K` = local sample size: within one refinement step, `G` produces `K` candidates from the **same proposal `x`** (same prompt, same parent trajectory), all evaluated, and **only the single highest-scoring** candidate is committed to `S` as the next node (`paper_text:341-347,420-421`). Purpose: guard against a weak/noisy/failed single generation poisoning the trajectory ("local commitment risk") (`paper_text:416-421`). The K candidates are **not** independent chains — they share proposal `x` and merge via best-of-K into one node. Theory models each of K proposals improving with prob. `p`, raising step-improvement prob. to `1-(1-p)^K` (`paper_text:4006-4010`).

**Merge into graph:** batch of K completes → `_find_best_node` → commit best to chain, bump visit counts (`base.py:451-534`; `rpucg.py:237-248`). Asynchronous runtime: each `Φ(S)` call = one logical local batch; K candidates dispatched as **one K-sample request OR K separate one-sample requests** (semantically identical, reduced by same best-of-K rule) (`paper_text:539-547`). Code default `stream_k_candidates=True` (independent k=1 jobs) (`config.py:170`).

**Scaling findings on K** (`paper_text:426-434,2577-2593`): increasing K from 1 to a moderate value consistently improves; too-large K (leaving small L) saturates or reverses. Interaction with depth is **depth-dependent**: at shallow L, larger K gives no monotonic gain; at large L, larger K consistently drives superior final performance (a rigorously-selected node establishes a higher-quality foundation that compounds). Treat K as a trade-off parameter, not monotonic (`paper_text:4017-4019`).

## 5. Ablations + defaults

**Which components matter most:** The paper's headline is that **budget scaling (C·L·K), not the selection heuristic, is the primary driver** — "absolute numerical differences in peak performance across strategies are relatively modest" (`paper_text:2953-2958`). Among C vs L, effect is task-dependent: math/construction (AC1, Erdős) benefit most from scaling `C` up to 32; GPU-kernel (TriMul) benefits most from scaling `L` (`paper_text:2563-2571`).

**Φ ablation (Table 18, AC1 / Erdős)** (`paper_text:2927-2933`): Random 1.505457/0.380926; Balance 1.505857/0.380909; LLM-elite 1.505069/0.380871; RPUCG insp=1 1.506647/0.380913, insp=3 1.504571/0.380893, insp=5 1.504476/0.380908, insp=10 1.504977/0.380951. **Inspiration count sweet spot = 3 or 5**; insp=1 → marginal single-trajectory refinement, insp=10 → context crowding (`paper_text:2945-2952`).

**Reflection / Failure-Patterns ablation (Table 19)** (`paper_text:2975-2986`): Failure Patterns alone ("Off/On") is the strong foundation; On/On best on AC1, Off/On slightly best on Erdős. Gaps marginal; framework "robust… provided basic negative constraints are present."

**Trajectory-level pruning** (`paper_text:2990-3029`): early-stopping worst chains at L=25/50 cutoffs; optimal chain survives 10/18 runs even at aggressive L=25-keep-1; degradation typically <0.01%, always <0.03%; efficacy highly task-dependent (harmless for circle packing, riskier for autocorrelation/Erdős).

**Concrete defaults** (`paper_text:684-692`): global width **C=32**, refinement depth **L=100**, local sample size **K=16** ⇒ **N=51.2K** evaluator queries. RPUCG default selector with **λ=1.0, γ=0.8**. Generator `G` = open-source **gpt-oss-120b** and **gpt-oss-20b** via vLLM; reasoning "high", temperature **1.0**. Token-forcing: total context **49,152** tokens, program ≤**15,536**, input+reasoning ≤**33,616** (`paper_text:687-691`). **Code defaults differ** (they are library defaults, not paper-experiment): `num_inspirations=5` (`config.py:123`), `k_candidates=4` (`config.py:169`), rpucg `c=1.0, gamma=0.8, k=1` (`rpucg.py:66-75`), `num_chains=4, max_generations=100` (`base.py:203-206`), `backpressure_multiplier=0` (`config.py:190`). Post-training (Alg. 2): IRFT credit, cold-start K=16/L=100 collecting 320 trajectories/task, then `Ĉ=32`; top-`R%` with R=10 (first 4 iters) then R=5 (last 2 iters); 6 iterations (`paper_text:2622-2630`).

## 6. Cost, caching, throughput, wall-clock

- **Cost comparison:** Claude-Code+Opus 4.6 plateaued at 0.9438 on AC2 at ~100M tokens / **$500**; SIMPLETES matched it with gpt-oss-120b at **$60** (8.3× gap), and reached SOTA 0.9627 at ~**$400** (`paper_text:3990-3996`). gpt-oss-120b cost basis: $0.15/1M input, $0.60/1M output (OpenRouter median) (`paper_text:4032-4033`).
- **Compute footprint (post-training only):** 15 h on 32 Nvidia H200 for training; 82 h on 256 H200 for TES sampling (`paper_text:2629-2630`).
- **Throughput / parallelism:** two worker pools (generation, evaluation) connected by bounded queues; K dispatched as one K-sample request (lower overhead) or K one-sample requests (better generation/eval overlap) (`paper_text:543-547`). Trajectory-level **backpressure**: each active trajectory has a bounded number of unresolved local batches; the default setting recovers strict synchronization (no second batch until prior resolves — a per-chain in-flight cap of one; code exposes this as `backpressure_multiplier=0`); queues physically bounded, submission blocks rather than discards; no eviction of admitted jobs — control at admission time (`paper_text:548-555`).
- **Evaluation engineering:** single compute node (no E2B/Daytona cloud sandbox, to cut cost); each eval in a fresh subprocess with process-group timeouts + memory limits; Docker with networking disabled for complex tasks; independent out-of-process score re-verification against isolated test data to mitigate reward hacking (`paper_text:556-567`).
- **Wall-clock in tasks:** per-candidate limits e.g. 300 s (RNA-seq denoising) (`paper_text:2477`); GPU-kernel timings reported as mean wall-clock ms on H200/H100/A100/MI300 (`paper_text:1476,1553`).
- **Caching:** **no LLM-inference caching mechanism is described.** The paper never mentions prompt/KV caching, prefix caching, or a generation cache for the framework's inference. (A grep for "cach" over `paper_text.txt` does return 20 hits, but every one is task-level, not framework-inference: a cached scoring baseline (`paper_text:1282`), GPU input-cache behavior (`paper_text:1546`), preventing test-set caching (`paper_text:1787`), intermediate-state caching inside a discovered algorithm (`paper_text:1964`), and a reward-hacking discussion about kernels caching input pointers/buffers (`paper_text:2756-2767`) — none about LLM prompt/KV/prefix/generation caching.) The only reuse mechanism is the **best-solution restart** (re-seed `y0` from the prior run's argmax, resetting all history/bookkeeping), which the paper found usually plateaus after one restart and therefore excluded from the main design space (`paper_text:521-534,694-697`).

**Notable absence:** the theory (App. B, Theorem 7.2) justifies `C` via a Pólya-urn "Matthew Effect" model and gives `L*≈log_λ(1-s)`, `C*≈Θ(log 1/ε)` (`paper_text:3982-3987`) — but explicitly "does not consider the complexity induced by Φ" (`paper_text:3934-3935`), so RPUCG itself has no formal guarantee; its support is empirical (Table 18) only.
