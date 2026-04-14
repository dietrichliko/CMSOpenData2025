import pathlib
import sqlite3
from typing import Any

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Integer, String, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy_state_machine import StateConfig, StateMixin  # type: ignore


class Base(DeclarativeBase):
    pass


INITIALIZED = "initialized"
SUBSCRIBED = "subscribed"
TRANSFERRED = "transferred"
STUCK = "stuck"
DONE = "done"
FAILED = "failed"


class Dataset(Base, StateMixin):
    __tablename__ = "datasets"

    state_config = StateConfig(
        initial=INITIALIZED,
        states=[INITIALIZED, SUBSCRIBED, TRANSFERRED, STUCK, DONE, FAILED],
        transitions=[
            ["set_subscribed", INITIALIZED, SUBSCRIBED],
            ["set_failed", INITIALIZED, FAILED],
            ["set_transferred", SUBSCRIBED, TRANSFERRED],
            ["set_done", TRANSFERRED, DONE],
            ["set_failed", TRANSFERRED, FAILED],
            ["set_stuck", SUBSCRIBED, STUCK],
            ["clear", FAILED, INITIALIZED],
            ["reset", SUBSCRIBED, INITIALIZED],
        ],
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    subscription: Mapped[str] = mapped_column(sa.String, nullable=True)
    ok_cnt: Mapped[int] = mapped_column(sa.Integer, nullable=True)
    replicating_cnt: Mapped[int] = mapped_column(sa.Integer, nullable=True)
    stuck_cnt: Mapped[int] = mapped_column(sa.Integer, nullable=True)

    files: Mapped[list["File"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"Dataset(id={self.id}, name='{self.name}')"


sa.event.listen(Dataset, "init", Dataset.init_state_machine)  # type: ignore
sa.event.listen(Dataset, "load", Dataset.init_state_machine)  # type: ignore


class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[int] = mapped_column(String, nullable=False)
    entries: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"))
    dataset: Mapped["Dataset"] = relationship(back_populates="files")

    def __repr__(self) -> str:
        return f"File(id={self.id}, path='{self.path}', size={self.size}, checksum='{self.checksum}', entries={self.entries})"


def create_engine(database_path: pathlib.Path) -> sa.engine.Engine:
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    engine = sa.create_engine(database_url, echo=False)

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(  # type: ignore
        dbapi_connection: sqlite3.Connection, connection_record: Any
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.close()

    return engine
