"""Azure background-mode transport for the deep-research path.

The deep-research client lives in ``dr_client`` (submit + poll against the
separate DR resource). The old agentic proposer (``AgentLLMClient`` + tools)
was removed in the orchestrator rewrite — the inner loop's mutation call lives
in ``orchestrator/scripts/mutate.py``.
"""
