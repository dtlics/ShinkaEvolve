import asyncio
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from shinka.database import DatabaseConfig, Program, ProgramDatabase
from shinka.database.async_dbase import AsyncProgramDatabase


def _program(program_id: str) -> Program:
    return Program(
        id=program_id,
        code="def f():\n    return 1\n",
        correct=True,
        combined_score=1.0,
        generation=0,
        island_idx=0,
    )


def test_program_database_init_without_openai_key(monkeypatch):
    """DB construction should not require API credentials."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "no_key_init.db"
        db = ProgramDatabase(config=DatabaseConfig(db_path=str(db_path), num_islands=1))
        try:
            db.add(_program("p0"))
            assert db.get("p0") is not None
        finally:
            db.close()


def test_async_db_add_without_openai_key_when_embeddings_disabled(monkeypatch):
    """Async wrapper should preserve disabled embedding mode in worker DBs."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "no_key_async.db"
            sync_db = ProgramDatabase(
                config=DatabaseConfig(db_path=str(db_path), num_islands=1),
                embedding_model="",
            )
            async_db = AsyncProgramDatabase(sync_db=sync_db)
            try:
                await async_db.add_program_async(_program("async-p0"))
                assert sync_db.get("async-p0") is not None
            finally:
                await async_db.close_async()
                sync_db.close()

    asyncio.run(_run())


def test_async_db_add_forwards_verbose_flag(monkeypatch):
    """Async add should forward verbose to the underlying writer database."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    observed = {}
    original_add = ProgramDatabase.add

    def tracking_add(self, program, verbose=False, defer_maintenance=False):
        observed["verbose"] = verbose
        return original_add(
            self,
            program,
            verbose=verbose,
            defer_maintenance=defer_maintenance,
        )

    monkeypatch.setattr(ProgramDatabase, "add", tracking_add)

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "verbose_forwarding.db"
            sync_db = ProgramDatabase(
                config=DatabaseConfig(db_path=str(db_path), num_islands=1),
                embedding_model="",
            )
            async_db = AsyncProgramDatabase(sync_db=sync_db)
            try:
                await async_db.add_program_async(_program("async-p0"), verbose=True)
            finally:
                await async_db.close_async()
                sync_db.close()

    asyncio.run(_run())

    assert observed == {"verbose": True}


def test_async_db_add_skips_duplicate_source_job_id(monkeypatch):
    """Async DB writes should be idempotent for the same completed scheduler job."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "duplicate_source_job.db"
            sync_db = ProgramDatabase(
                config=DatabaseConfig(db_path=str(db_path), num_islands=1),
                embedding_model="",
            )
            async_db = AsyncProgramDatabase(sync_db=sync_db)
            try:
                first = _program("async-p0")
                first.metadata = {"source_job_id": "job-123"}
                second = _program("async-p1")
                second.metadata = {"source_job_id": "job-123"}

                await async_db.add_program_async(first)
                await async_db.add_program_async(second)

                assert sync_db.get("async-p0") is not None
                assert sync_db.get("async-p1") is None
                assert sync_db._count_programs_in_db() == 1
                assert sync_db.has_program_with_source_job_id("job-123") is True
            finally:
                await async_db.close_async()
                sync_db.close()

    asyncio.run(_run())


def test_async_db_source_job_id_check_treats_inflight_insert_as_existing(monkeypatch):
    """Retries should see an in-flight source_job_id before commit finishes."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "inflight_source_job.db"
            sync_db = ProgramDatabase(
                config=DatabaseConfig(db_path=str(db_path), num_islands=1),
                embedding_model="",
            )
            async_db = AsyncProgramDatabase(sync_db=sync_db)
            try:
                async_db._in_flight_source_job_ids.add("job-123")
                assert await async_db.has_program_with_source_job_id_async("job-123")
            finally:
                await async_db.close_async()
                sync_db.close()

    asyncio.run(_run())


def test_async_db_can_fetch_program_by_source_job_id(monkeypatch):
    """Async DB should recover the already-persisted row for retry side effects."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fetch_source_job.db"
            sync_db = ProgramDatabase(
                config=DatabaseConfig(db_path=str(db_path), num_islands=1),
                embedding_model="",
            )
            async_db = AsyncProgramDatabase(sync_db=sync_db)
            try:
                program = _program("async-p0")
                program.metadata = {"source_job_id": "job-123"}

                await async_db.add_program_async(program)

                recovered = await async_db.get_program_by_source_job_id_async("job-123")

                assert recovered is not None
                assert recovered.id == "async-p0"
                assert recovered.metadata["source_job_id"] == "job-123"
            finally:
                await async_db.close_async()
                sync_db.close()

    asyncio.run(_run())


def test_async_db_add_skips_source_job_id_while_another_insert_is_inflight(monkeypatch):
    """Do not insert a duplicate row while the same source job is still in flight."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "inflight_duplicate_source_job.db"
            sync_db = ProgramDatabase(
                config=DatabaseConfig(db_path=str(db_path), num_islands=1),
                embedding_model="",
            )
            async_db = AsyncProgramDatabase(sync_db=sync_db)
            try:
                async_db._in_flight_source_job_ids.add("job-123")
                duplicate = _program("async-p1")
                duplicate.metadata = {"source_job_id": "job-123"}

                await async_db.add_program_async(duplicate)

                assert sync_db.get("async-p1") is None
                assert sync_db._count_programs_in_db() == 0
            finally:
                await async_db.close_async()
                sync_db.close()

    asyncio.run(_run())


def test_async_db_can_record_attempt_events(monkeypatch):
    """Attempt log writes should work without API credentials."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "attempt_log.db"
            sync_db = ProgramDatabase(
                config=DatabaseConfig(db_path=str(db_path), num_islands=1),
                embedding_model="",
            )
            async_db = AsyncProgramDatabase(sync_db=sync_db)
            try:
                await async_db.record_attempt_event_async(
                    generation=7,
                    stage="proposal",
                    status="failed",
                    details={"reason": "test"},
                )
                sync_db.cursor.execute(
                    "SELECT generation, stage, status FROM attempt_log"
                )
                rows = [tuple(row) for row in sync_db.cursor.fetchall()]
                assert rows == [(7, "proposal", "failed")]
            finally:
                await async_db.close_async()
                sync_db.close()

    asyncio.run(_run())


def test_async_db_uses_fresh_writer_database_per_add(monkeypatch):
    """Multi-writer async DB should build a fresh writer DB per add operation."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "writer_reuse.db"
            sync_db = ProgramDatabase(
                config=DatabaseConfig(db_path=str(db_path), num_islands=1),
                embedding_model="",
            )
            async_db = AsyncProgramDatabase(sync_db=sync_db)
            original_init = ProgramDatabase.__init__
            writer_db_ids = []

            def tracking_init(self, *args, **kwargs):
                original_init(self, *args, **kwargs)
                if kwargs.get("read_only", False) is False:
                    writer_db_ids.append(id(self))

            monkeypatch.setattr(ProgramDatabase, "__init__", tracking_init)
            try:
                await async_db.add_program_async(_program("async-p0"))
                await async_db.add_program_async(_program("async-p1"))

                assert len(writer_db_ids) >= 2
                assert sync_db.get("async-p0") is not None
                assert sync_db.get("async-p1") is not None
            finally:
                await async_db.close_async()
                sync_db.close()

    asyncio.run(_run())


def test_async_db_can_run_multiple_writes_concurrently(monkeypatch):
    """Async DB should allow multiple write tasks to overlap when workers > 1."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    active_adds = 0
    peak_adds = 0
    lock = threading.Lock()
    original_add = ProgramDatabase.add

    def tracking_add(self, program, verbose=False, defer_maintenance=False):
        nonlocal active_adds, peak_adds
        with lock:
            active_adds += 1
            peak_adds = max(peak_adds, active_adds)
        try:
            time.sleep(0.05)
            return original_add(
                self,
                program,
                verbose=verbose,
                defer_maintenance=defer_maintenance,
            )
        finally:
            with lock:
                active_adds -= 1

    monkeypatch.setattr(ProgramDatabase, "add", tracking_add)

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "concurrent_writer.db"
            sync_db = ProgramDatabase(
                config=DatabaseConfig(db_path=str(db_path), num_islands=1),
                embedding_model="",
            )
            async_db = AsyncProgramDatabase(sync_db=sync_db, max_workers=2)
            try:
                await asyncio.gather(
                    async_db.add_program_async(_program("async-p0")),
                    async_db.add_program_async(_program("async-p1")),
                )
                assert sync_db.get("async-p0") is not None
                assert sync_db.get("async-p1") is not None
            finally:
                await async_db.close_async()
                sync_db.close()

    asyncio.run(_run())

    assert peak_adds >= 2


# ----------------------------------------------------------------------
# Phase 1 of research-grounding: error_traceback column round-trip and
# old-schema migration. The Program column is a nullable TEXT that holds
# a truncated stderr/traceback from wrap_eval.save_json_results when the
# evaluator raises an exception. Downstream analytics (and the agentic
# proposer's evaluate_tool path) can read it without parsing
# stdout/stderr blobs.
# ----------------------------------------------------------------------


def test_error_traceback_roundtrip(monkeypatch):
    """A Program with ``error_traceback`` set must round-trip through
    INSERT → SELECT preserving the field exactly."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    tb_text = (
        "Traceback (most recent call last):\n"
        '  File "/tmp/eval.py", line 5, in run\n'
        "    raise ValueError('bad input')\n"
        "ValueError: bad input"
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "error_traceback_rt.db"
        db = ProgramDatabase(
            config=DatabaseConfig(db_path=str(db_path), num_islands=1),
            embedding_model="",
        )
        try:
            failed = Program(
                id="err-1",
                code="def f():\n    raise ValueError('bad input')\n",
                correct=False,
                combined_score=0.0,
                generation=1,
                island_idx=0,
                error_traceback=tb_text,
            )
            db.add(failed)

            loaded = db.get("err-1")
            assert loaded is not None
            assert loaded.error_traceback == tb_text
            # SQLite stores bools as 0/1; we just need falsy.
            assert not loaded.correct

            # And a successful program leaves error_traceback at None.
            ok = Program(
                id="ok-1",
                code="def f():\n    return 1\n",
                correct=True,
                combined_score=1.0,
                generation=2,
                island_idx=0,
            )
            db.add(ok)
            loaded_ok = db.get("ok-1")
            assert loaded_ok is not None
            assert loaded_ok.error_traceback is None
        finally:
            db.close()


def test_old_schema_migration_adds_error_traceback(monkeypatch):
    """An existing DB created before Phase 1 must migrate cleanly: the
    error_traceback column is added by _run_migrations on next open,
    and prior rows keep working."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "old_schema.db"

        # Hand-roll a pre-migration schema: the same CREATE TABLE shape
        # as before Phase 1 — no error_traceback column. We don't need
        # every index or foreign-key; the migration only inspects
        # PRAGMA table_info(programs).
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE programs (
                id TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                language TEXT NOT NULL,
                parent_id TEXT,
                archive_inspiration_ids TEXT,
                top_k_inspiration_ids TEXT,
                generation INTEGER NOT NULL,
                timestamp REAL NOT NULL,
                code_diff TEXT,
                combined_score REAL,
                public_metrics TEXT,
                private_metrics TEXT,
                text_feedback TEXT,
                complexity REAL,
                embedding TEXT,
                embedding_pca_2d TEXT,
                embedding_pca_3d TEXT,
                embedding_cluster_id INTEGER,
                correct BOOLEAN DEFAULT 0,
                children_count INTEGER NOT NULL DEFAULT 0,
                metadata TEXT,
                migration_history TEXT,
                island_idx INTEGER,
                system_prompt_id TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO programs(id, code, language, generation, timestamp, "
            "combined_score, correct, public_metrics, private_metrics, metadata,"
            "archive_inspiration_ids, top_k_inspiration_ids) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-1",
                "def f(): pass\n",
                "python",
                0,
                0.0,
                1.0,
                1,
                "{}",
                "{}",
                "{}",
                "[]",
                "[]",
            ),
        )
        conn.commit()
        conn.close()

        # Sanity: column was not there pre-migration.
        check_conn = sqlite3.connect(str(db_path))
        cols_before = {
            row[1] for row in check_conn.execute("PRAGMA table_info(programs)")
        }
        assert "error_traceback" not in cols_before
        check_conn.close()

        # Open via ProgramDatabase — _run_migrations should ALTER TABLE
        # to add the new column without dropping data.
        db = ProgramDatabase(
            config=DatabaseConfig(db_path=str(db_path), num_islands=1),
            embedding_model="",
        )
        try:
            # Column exists now.
            assert db.cursor is not None
            cols_after = {
                row[1] for row in db.cursor.execute("PRAGMA table_info(programs)")
            }
            assert "error_traceback" in cols_after

            # Pre-existing row still loads and has error_traceback=None.
            legacy = db.get("legacy-1")
            assert legacy is not None
            assert legacy.error_traceback is None

            # New writes that set error_traceback land in the column.
            db.add(
                Program(
                    id="new-1",
                    code="def f():\n    1/0\n",
                    correct=False,
                    combined_score=0.0,
                    generation=1,
                    island_idx=0,
                    error_traceback="ZeroDivisionError: division by zero",
                )
            )
            new = db.get("new-1")
            assert new is not None
            assert new.error_traceback == "ZeroDivisionError: division by zero"
        finally:
            db.close()
