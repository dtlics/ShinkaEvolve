from typing import List, Optional, Tuple, Literal
import numpy as np
from shinka.database import Program
from shinka.database.inspirations import InspirationContextBuilder
from shinka.prompts import (
    construct_eval_history_msg,
    perf_str,
    format_text_feedback_section,
    BASE_SYSTEM_MSG,
    DIFF_SYS_FORMAT,
    DIFF_ITER_MSG,
    FULL_ITER_MSG,
    FULL_SYS_FORMATS,
    CROSS_SYS_FORMAT,
    CROSS_ITER_MSG,
    get_cross_component,
    FIX_SYS_FORMAT,
    FIX_ITER_MSG,
    format_error_output_section,
)
from shinka.prompts.prompts_init import INIT_SYSTEM_MSG, INIT_USER_MSG
from shinka.defaults import default_patch_type_probs, default_patch_types
import logging

logger = logging.getLogger(__name__)


class PromptSampler:
    def __init__(
        self,
        task_sys_msg: Optional[str] = None,
        language: str = "python",
        patch_types: Optional[List[str]] = None,
        patch_type_probs: Optional[List[float]] = None,
        use_text_feedback: bool = False,
        inspiration_sort_order: Literal[
            "ascending", "chronological", "none"
        ] = "ascending",
    ):
        if patch_types is None:
            patch_types = default_patch_types()
        if patch_type_probs is None:
            patch_type_probs = default_patch_type_probs()

        self.task_sys_msg = task_sys_msg
        self.language = language
        self.patch_types = patch_types
        self.patch_type_probs = patch_type_probs
        # Check if probabilities sum to 1.0 w. tolerance for errors
        prob_sum = np.sum(patch_type_probs)
        if not np.isclose(prob_sum, 1.0, atol=1e-6):
            raise ValueError(
                f"Coding type probabilities must sum to 1.0, got {prob_sum:.6f}"
            )
        # Whether to use text feedback in the prompt
        self.use_text_feedback = use_text_feedback
        # Context builder for sorting inspirations (least-to-most by default)
        self.context_builder = InspirationContextBuilder(
            sort_order=inspiration_sort_order
        )

    def initial_program_prompt(self) -> Tuple[str, str]:
        """Generate the prompt for the initial program."""
        if self.task_sys_msg is None:
            sys_msg = INIT_SYSTEM_MSG
            task_description = "The user has not provided a task description."
        else:
            sys_msg = self.task_sys_msg
            task_description = self.task_sys_msg

        user_msg = INIT_USER_MSG.format(
            language=self.language,
            task_description=task_description,
        )
        return sys_msg, user_msg

    def sample(
        self,
        parent: Program,
        archive_inspirations: List[Program],
        top_k_inspirations: List[Program],
        meta_recommendations: Optional[str] = None,
        island_brief: Optional[str] = None,
        failure_note: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        if self.task_sys_msg is None:
            sys_msg = BASE_SYSTEM_MSG
        else:
            sys_msg = self.task_sys_msg

        # Phase 2 of research-grounding: when the orchestrator has a
        # fresh DR brief for this parent's island, prefer it over the
        # global freeform meta_recommendations. The freeform string
        # still acts as a fallback for islands the DR cadence didn't
        # touch this cycle.
        if island_brief:
            meta_recommendations = island_brief

        # Build the candidate type list: the ``cross`` arm is suppressed when there
        # are no inspirations to cross-pollinate with.
        skip_cross = (
            len(archive_inspirations) == 0 and len(top_k_inspirations) == 0
        )
        valid_types: List[str] = []
        valid_probs: List[float] = []
        for t, p in zip(self.patch_types, self.patch_type_probs):
            if t == "cross" and skip_cross:
                continue
            valid_types.append(t)
            valid_probs.append(p)
        if len(valid_types) < len(self.patch_types):
            prob_sum = sum(valid_probs)
            if prob_sum > 0:
                valid_probs = [p / prob_sum for p in valid_probs]
            else:
                if len(valid_types) > 0:
                    valid_probs = [1.0 / len(valid_types)] * len(valid_types)
                else:
                    # No valid types left — fall back to the full list
                    # (rare; happens only if every type is suppressed at
                    # once, which the config validator should prevent).
                    valid_types = list(self.patch_types)
                    valid_probs = list(self.patch_type_probs)
            patch_type = np.random.choice(valid_types, p=valid_probs)
        else:
            patch_type = np.random.choice(
                self.patch_types,
                p=self.patch_type_probs,
            )

        # Add meta-recommendations BEFORE format instructions (if provided).
        # ``cross`` manages its own focus material (cross-pollination context)
        # and doesn't want a generic rec slot competing with it.
        skip_meta_rec_for = {"cross"}
        if meta_recommendations not in [None, "none"] and patch_type not in skip_meta_rec_for:
            sys_msg += "\n\n# Potential Recommendations"
            sys_msg += (
                "\nThe following are potential recommendations for the "
                "next program generation:\n"
            )
            sys_msg += f"\n{meta_recommendations}"
            logger.info(
                f"Added meta recommendation to system prompt: "
                f"{meta_recommendations[:80]}..."
            )
        else:
            logger.debug(
                f"No meta recommendation added: "
                f"meta_recommendations={bool(meta_recommendations)}, "
                f"patch_type={patch_type}"
            )

        # The persistent failure caution rides into EVERY generation — rendered
        # independently of patch_type and of any per-gen direction/island_brief, so
        # it is never dropped on a cross gen, when an island_brief replaced the
        # direction, or when no direction was sampled (M1/M2/M3/M4).
        if failure_note not in (None, "", "none") and str(failure_note).strip():
            sys_msg += "\n\n# Known failure modes to avoid"
            sys_msg += (
                "\nDo NOT reintroduce these recurring failure modes seen in past "
                "attempts:\n"
            )
            sys_msg += f"\n{failure_note}"

        # Add format instructions AFTER meta-recommendations
        if patch_type == "diff":
            sys_msg += DIFF_SYS_FORMAT
        elif patch_type == "full":
            # Randomly sample from different full rewrite variants
            full_variant_idx = np.random.randint(0, len(FULL_SYS_FORMATS))
            selected_format = FULL_SYS_FORMATS[full_variant_idx]
            sys_msg += selected_format
        elif patch_type == "cross":
            sys_msg += CROSS_SYS_FORMAT

        # Build sorted inspiration context (combines archive + top-k)
        sorted_inspirations = self.context_builder.build_context(
            archive_inspirations, top_k_inspirations
        )

        if len(sorted_inspirations) > 0:
            eval_history_msg = construct_eval_history_msg(
                sorted_inspirations,
                language=self.language,
                include_text_feedback=self.use_text_feedback,
            )
        else:
            eval_history_msg = ""

        # Format text feedback section for current program
        text_feedback_section = ""
        if self.use_text_feedback:
            text_feedback_section = "\n" + format_text_feedback_section(
                parent.text_feedback
            )

        if patch_type == "diff":
            iter_msg = DIFF_ITER_MSG.format(
                language=self.language,
                code_content=parent.code,
                performance_metrics=perf_str(
                    parent.combined_score, parent.public_metrics
                ),
                text_feedback_section=text_feedback_section,
            )
        elif patch_type == "full":
            iter_msg = FULL_ITER_MSG.format(
                language=self.language,
                code_content=parent.code,
                performance_metrics=perf_str(
                    parent.combined_score, parent.public_metrics
                ),
                text_feedback_section=text_feedback_section,
            )
        elif patch_type == "cross":
            iter_msg = CROSS_ITER_MSG.format(
                language=self.language,
                code_content=parent.code,
                performance_metrics=perf_str(
                    parent.combined_score, parent.public_metrics
                ),
                text_feedback_section=text_feedback_section,
            )
            iter_msg += "\n\n" + get_cross_component(
                archive_inspirations,
                top_k_inspirations,
                language=self.language,
            )
        elif patch_type == "paper":
            raise NotImplementedError("Paper edit not implemented.")
        else:
            raise ValueError(f"Invalid patch type: {patch_type}")

        return (
            sys_msg,
            eval_history_msg + "\n" + iter_msg,
            patch_type,
        )

    def sample_fix(
        self,
        incorrect_program: Program,
        ancestor_inspirations: Optional[List[Program]] = None,
        failure_note: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        """
        Generate prompts for fixing an incorrect program.

        This is used when no correct parent exists in the database,
        and we need to fix an incorrect program using its error output.

        Args:
            incorrect_program: The incorrect program to fix
            ancestor_inspirations: Programs from the ancestry of the program
                (sorted chronologically, oldest first)
            failure_note: Optional persistent caution (recurring failure modes to
                avoid), rendered into the repair prompt so a fix does not
                reintroduce a known failure class.

        Returns:
            Tuple of (system_message, user_message, patch_type="fix")
        """
        if self.task_sys_msg is None:
            sys_msg = BASE_SYSTEM_MSG
        else:
            sys_msg = self.task_sys_msg

        sys_msg += FIX_SYS_FORMAT.format(language=self.language)

        # M4: the persistent failure caution rides into fix-mode prompts too, so a
        # repair does not reintroduce a known failure class.
        if failure_note not in (None, "", "none") and str(failure_note).strip():
            sys_msg += "\n\n# Known failure modes to avoid\n"
            sys_msg += f"{failure_note}"

        # Build eval history from ancestor inspirations (already chronological)
        if ancestor_inspirations and len(ancestor_inspirations) > 0:
            eval_history_msg = construct_eval_history_msg(
                ancestor_inspirations,
                language=self.language,
                include_text_feedback=self.use_text_feedback,
                correct=False,
            )
        else:
            eval_history_msg = ""

        # Format text feedback section
        text_feedback_section = ""
        if self.use_text_feedback and incorrect_program.text_feedback:
            text_feedback_section = "\n" + format_text_feedback_section(
                incorrect_program.text_feedback
            )

        # Extract stdout/stderr from metadata if available
        metadata = incorrect_program.metadata or {}
        stdout_log = metadata.get("stdout_log", "")
        stderr_log = metadata.get("stderr_log", "")

        error_output_section = format_error_output_section(
            stdout_log=stdout_log,
            stderr_log=stderr_log,
        )

        iter_msg = FIX_ITER_MSG.format(
            language=self.language,
            code_content=incorrect_program.code,
            text_feedback_section=text_feedback_section,
            error_output_section=error_output_section,
        )

        patch_type = "fix"
        logger.info(
            f"Generated FIX prompt for incorrect program "
            f"(Gen: {incorrect_program.generation}, "
            f"Score: {incorrect_program.combined_score or 0.0:.4f}, "
            f"Ancestors: {len(ancestor_inspirations or [])})"
        )

        return (
            sys_msg,
            eval_history_msg + "\n" + iter_msg if eval_history_msg else iter_msg,
            patch_type,
        )
