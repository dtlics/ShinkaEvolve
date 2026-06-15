from .prompts_base import (
    construct_eval_history_msg,
    construct_individual_program_msg,
    perf_str,
    format_text_feedback_section,
    BASE_SYSTEM_MSG,
)
from .prompts_diff import DIFF_SYS_FORMAT, DIFF_ITER_MSG
from .prompts_full import (
    FULL_SYS_FORMAT_DEFAULT,
    FULL_ITER_MSG,
    FULL_SYS_FORMATS,
)
from .prompts_cross import (
    CROSS_SYS_FORMAT,
    CROSS_ITER_MSG,
    get_cross_component,
)
from .prompts_fix import (
    FIX_SYS_FORMAT,
    FIX_ITER_MSG,
    format_error_output_section,
)
from .prompts_init import INIT_SYSTEM_MSG, INIT_USER_MSG
# L20: prompts_meta.py (META_STEP1/2/3) was DEAD — the orchestrator's meta round
# (orchestrator/scripts/meta_summarize.py) builds its own prompts and never imported these;
# the file + its re-exports are removed.
from .prompts_novelty import NOVELTY_SYSTEM_MSG, NOVELTY_USER_MSG
from .prompts_deep_research import (
    DR_STAGE_C_SYS_MSG,
    DR_STAGE_C_USER_MSG,
)

__all__ = [
    "construct_eval_history_msg",
    "construct_individual_program_msg",
    "perf_str",
    "format_text_feedback_section",
    "BASE_SYSTEM_MSG",
    "DIFF_SYS_FORMAT",
    "DIFF_ITER_MSG",
    "FULL_SYS_FORMAT_DEFAULT",
    "FULL_SYS_FORMATS",
    "FULL_ITER_MSG",
    "CROSS_SYS_FORMAT",
    "CROSS_ITER_MSG",
    "get_cross_component",
    "FIX_SYS_FORMAT",
    "FIX_ITER_MSG",
    "format_error_output_section",
    "INIT_SYSTEM_MSG",
    "INIT_USER_MSG",
    "NOVELTY_SYSTEM_MSG",
    "NOVELTY_USER_MSG",
    # Deep-research (DR) prompt — the single web-grounded research call
    "DR_STAGE_C_SYS_MSG",
    "DR_STAGE_C_USER_MSG",
]
