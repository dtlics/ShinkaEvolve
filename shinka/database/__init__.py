"""shinka.database — the archive. Slimmed to the synchronous `ProgramDatabase`
the orchestrator uses (the async wrapper + the prompt-evolution DB were removed
with the old runner / prompt-evolver)."""

from .dbase import ProgramDatabase, Program, DatabaseConfig

__all__ = [
    "ProgramDatabase",
    "Program",
    "DatabaseConfig",
]
