"""A temporary archive + watch folder + application-state root with a reopenable writer."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from docflow.filing.operations import DurableFiler
from docflow.ingestion.stability import check_input
from docflow.state.database import StateDatabase
from docflow.state.paths import StatePaths
from docflow.state.repositories import StateStore


def quick_observe(path: Path):
    return check_input(path, observations=2, interval=0, sleep=lambda s: None)


@dataclass
class Env:
    tmp: Path
    home: Path
    archive: Path
    watch: Path
    state: StatePaths
    db: StateDatabase = field(init=False)
    store: StateStore = field(init=False)
    scope_id: str = field(init=False)

    def __post_init__(self) -> None:
        self.archive.mkdir(parents=True, exist_ok=True)
        self.watch.mkdir(parents=True, exist_ok=True)
        self.open()
        self.scope_id = self.store.scopes.register(self.archive, home=self.home).id

    def open(self) -> None:
        self.db = StateDatabase(self.state).open()
        self.store = StateStore(self.db)

    def reopen(self) -> None:
        """Simulate a process restart: drop the writer and its lock, then reopen."""
        self.db.close()
        self.open()

    def filer(self, **kwargs) -> DurableFiler:
        kwargs.setdefault("observe", quick_observe)
        return DurableFiler(self.store, source_roots={"watch": self.watch}, **kwargs)

    def rows(self, sql: str, *params) -> list[tuple]:
        return [tuple(r) for r in self.db.connection.execute(sql, params)]

    def close(self) -> None:
        self.db.close()


def make_env(tmp_path: Path, home: Path) -> Env:
    return Env(tmp_path, home, tmp_path / "archive", tmp_path / "inbox",
               StatePaths(tmp_path / "app-state"))


def classified_job(env: Env, marks: list[int], name: str = "scan.pdf") -> str:
    """Write a synthetic scan to the watch folder and advance its job to ``classified``."""
    from tests.reliability.synthetic import write_image_pdf

    write_image_pdf(env.watch / name, marks)
    admission = env.filer().admit(env.scope_id, f"watch:{name}")
    assert admission.job_id is not None
    jobs = env.store.jobs
    assert jobs.transition(env.scope_id, admission.job_id, expected="ready", new="ocr")
    assert jobs.transition(env.scope_id, admission.job_id, expected="ocr", new="classified")
    return admission.job_id
