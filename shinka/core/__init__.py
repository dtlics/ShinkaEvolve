"""shinka.core — slimmed to the orchestrator surface (Azure-only).

The old `ShinkaEvolveRunner` / meta-summarizer / novelty-judge / prompt-evolver
machinery was removed in the orchestrator rewrite; the outer loop lives in
`orchestrator/`. What remains is what the orchestrator + task evaluators need:
`PromptSampler` (mutation prompt assembly), `EvolutionConfig` (config dataclass),
and `run_shinka_eval` (the evaluation contract task `evaluate.py` files call).
"""

from .config import EvolutionConfig
from .sampler import PromptSampler
from .wrap_eval import run_shinka_eval

__all__ = [
    "EvolutionConfig",
    "PromptSampler",
    "run_shinka_eval",
]
