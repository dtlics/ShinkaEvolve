from typing import Any, Dict, List, Optional


class QueryResult:
    def __init__(
        self,
        content: str,
        msg: str,
        system_msg: str,
        new_msg_history: List[Dict],
        model_name: str,
        kwargs: Dict,
        input_tokens: int,
        output_tokens: int,
        thinking_tokens: int = 0,
        cost: float = 0.0,
        input_cost: float = 0.0,
        output_cost: float = 0.0,
        thought: str = "",
        model_posteriors: Optional[Dict[str, float]] = None,
        num_tool_calls: int = 0,
        num_total_queries: int = 1,
        final_output_obj: Optional[Any] = None,
    ):
        self.content = content
        self.msg = msg
        self.system_msg = system_msg
        self.new_msg_history = new_msg_history
        self.model_name = model_name
        self.kwargs = kwargs
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.thinking_tokens = thinking_tokens
        self.cost = cost
        self.input_cost = input_cost
        self.output_cost = output_cost
        self.thought = thought
        self.model_posteriors = model_posteriors or {}
        self.num_tool_calls = num_tool_calls
        self.num_total_queries = num_total_queries
        # Structured output (e.g. a Pydantic model instance) when the
        # caller requested ``output_type=`` on the agent. Legacy / text
        # paths leave this as None. Kept off ``to_dict`` because the
        # object isn't always JSON-serializable and the orchestrator
        # consumes it programmatically rather than via the DB row.
        self.final_output_obj = final_output_obj

    def __str__(self):
        """Return string representation of query result."""
        lines = []
        lines.append("=" * 80)
        lines.append(f"Model: {self.model_name}")
        lines.append(f"Total Cost: ${self.cost:.4f}")
        lines.append(f"  Input: ${self.input_cost:.4f} ({self.input_tokens} tokens)")
        lines.append(f"  Output: ${self.output_cost:.4f} ({self.output_tokens} tokens)")
        lines.append(
            f"  --> Thinking tokens: {self.thinking_tokens} ({self.thinking_tokens / self.output_tokens:.2f})"
        )
        if self.thinking_tokens > 0:
            lines.append(f"  Thinking: {self.thinking_tokens} tokens")
        lines.append("-" * 80)
        if self.thought:
            lines.append("Thought:")
            lines.append(self.thought)
            lines.append("-" * 80)
        lines.append("Content:")
        lines.append(self.content)
        if self.model_posteriors:
            lines.append("-" * 80)
            lines.append("Model Posteriors:")
            for model, prob in self.model_posteriors.items():
                lines.append(f"  {model}: {prob:.4f}")
        if self.num_total_queries > 0:
            lines.append("-" * 80)
            lines.append(f"Number of Total Queries: {self.num_total_queries}")
        if self.num_tool_calls > 0:
            lines.append("-" * 80)
            lines.append(f"Number of Tool Calls: {self.num_tool_calls}")
        lines.append("=" * 80)
        return "\n".join(lines)
