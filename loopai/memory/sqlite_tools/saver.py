from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, TypeVar, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    SerializerProtocol,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from loopai.memory.db_models.base import CheckpointModel, WriteModel
from loopai.memory.sqlite_tools.utils import search_where

from tortoise import Tortoise

T = TypeVar("T", bound=Callable)


class AsyncSqliteSaver(BaseCheckpointSaver[str]):
    """An asynchronous checkpoint saver that stores checkpoints in a SQLite database.

    This class provides an asynchronous interface for saving and retrieving checkpoints
    using a SQLite database. It's designed for use in asynchronous environments and
    offers better performance for I/O-bound operations compared to synchronous alternatives.

    Attributes:
        conn (aiosqlite.Connection): The asynchronous SQLite database connection.
        serde (SerializerProtocol): The serializer used for encoding/decoding checkpoints.

    Tip:
        Requires the [aiosqlite](https://pypi.org/project/aiosqlite/) package.
        Install it with `pip install aiosqlite`.

    Warning:
        While this class supports asynchronous checkpointing, it is not recommended
        for production workloads due to limitations in SQLite's write performance.
        For production use, consider a more robust database like PostgreSQL.

    Tip:
        Remember to **close the database connection** after executing your code,
        otherwise, you may see the graph "hang" after execution (since the program
        will not exit until the connection is closed).

        The easiest way is to use the `async with` statement as shown in the examples.

        ```python
        async with AsyncSqliteSaver.from_conn_string("checkpoints.sqlite") as saver:
            # Your code here
            graph = builder.compile(checkpointer=saver)
            config = {"configurable": {"thread_id": "thread-1"}}
            async for event in graph.astream_events(..., config, version="v1"):
                print(event)
        ```

    Examples:
        Usage within StateGraph:

        ```pycon
        >>> import asyncio
        >>>
        >>> from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        >>> from langgraph.graph import StateGraph
        >>>
        >>> async def main():
        >>>     builder = StateGraph(int)
        >>>     builder.add_node("add_one", lambda x: x + 1)
        >>>     builder.set_entry_point("add_one")
        >>>     builder.set_finish_point("add_one")
        >>>     async with AsyncSqliteSaver.from_conn_string("checkpoints.db") as memory:
        >>>         graph = builder.compile(checkpointer=memory)
        >>>         coro = graph.ainvoke(1, {"configurable": {"thread_id": "thread-1"}})
        >>>         print(await asyncio.gather(coro))
        >>>
        >>> asyncio.run(main())
        Output: [2]
        ```
        Raw usage:

        ```pycon
        >>> import asyncio
        >>> import aiosqlite
        >>> from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        >>>
        >>> async def main():
        >>>     async with aiosqlite.connect("checkpoints.db") as conn:
        ...         saver = AsyncSqliteSaver(conn)
        ...         config = {"configurable": {"thread_id": "1", "checkpoint_ns": ""}}
        ...         checkpoint = {"ts": "2023-05-03T10:00:00Z", "data": {"key": "value"}, "id": "0c62ca34-ac19-445d-bbb0-5b4984975b2a"}
        ...         saved_config = await saver.aput(config, checkpoint, {}, {})
        ...         print(saved_config)
        >>> asyncio.run(main())
        {'configurable': {'thread_id': '1', 'checkpoint_ns': '', 'checkpoint_id': '0c62ca34-ac19-445d-bbb0-5b4984975b2a'}}
        ```
    """

    lock: asyncio.Lock
    is_setup: bool

    def __init__(
        self,
        db_url: str,
        *,
        serde: SerializerProtocol | None = None,
    ):
        """
        Initialize the AsyncSqliteSaver instance.
        """
        super().__init__(serde=serde)
        self.jsonplus_serde = JsonPlusSerializer()
        self.db_url = db_url
        self.lock = asyncio.Lock()
        self.loop = asyncio.get_running_loop()
        self.is_setup = False

    async def __aenter__(self) -> None:
        """
        Initialize the database connection.
        """
        await Tortoise.init(
            db_url=self.db_url,
            modules={"models": ["loopai.memory.db_models.base"]},  # 注意根据实际模块修改
        )
        await self.setup()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        """
        Close the database connection.
        """
        await Tortoise.close_connections()
    
    async def setup(self) -> None:
        """Set up the checkpoint database asynchronously."""
        async with self.lock:
            if self.is_setup:
                return
            # 自动创建表
            await Tortoise.generate_schemas()
            self.is_setup = True

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Get a checkpoint tuple from the database.

        This method retrieves a checkpoint tuple from the SQLite database based on the
        provided config. If the config contains a `checkpoint_id` key, the checkpoint with
        the matching thread ID and checkpoint ID is retrieved. Otherwise, the latest checkpoint
        for the given thread ID is retrieved.

        Args:
            config: The config to use for retrieving the checkpoint.

        Returns:
            The retrieved checkpoint tuple, or None if no matching checkpoint was found.
        """
        try:
            # check if we are in the main thread, only bg threads can block
            # we don't check in other methods to avoid the overhead
            if asyncio.get_running_loop() is self.loop:
                raise asyncio.InvalidStateError(
                    "Synchronous calls to AsyncSqliteSaver are only allowed from a "
                    "different thread. From the main thread, use the async interface. "
                    "For example, use `await checkpointer.aget_tuple(...)` or `await "
                    "graph.ainvoke(...)`."
                )
        except RuntimeError:
            pass
        return asyncio.run_coroutine_threadsafe(
            self.aget_tuple(config), self.loop
        ).result()

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        """List checkpoints from the database asynchronously.

        This method retrieves a list of checkpoint tuples from the SQLite database based
        on the provided config. The checkpoints are ordered by checkpoint ID in descending order (newest first).

        Args:
            config: Base configuration for filtering checkpoints.
            filter: Additional filtering criteria for metadata.
            before: If provided, only checkpoints before the specified checkpoint ID are returned.
            limit: Maximum number of checkpoints to return.

        Yields:
            An iterator of matching checkpoint tuples.
        """
        try:
            # check if we are in the main thread, only bg threads can block
            # we don't check in other methods to avoid the overhead
            if asyncio.get_running_loop() is self.loop:
                raise asyncio.InvalidStateError(
                    "Synchronous calls to AsyncSqliteSaver are only allowed from a "
                    "different thread. From the main thread, use the async interface. "
                    "For example, use `checkpointer.alist(...)` or `await "
                    "graph.ainvoke(...)`."
                )
        except RuntimeError:
            pass
        aiter_ = self.alist(config, filter=filter, before=before, limit=limit)
        while True:
            try:
                yield asyncio.run_coroutine_threadsafe(
                    anext(aiter_),  # type: ignore[arg-type]  # noqa: F821
                    self.loop,
                ).result()
            except StopAsyncIteration:
                break

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Save a checkpoint to the database.

        This method saves a checkpoint to the SQLite database. The checkpoint is associated
        with the provided config and its parent config (if any).

        Args:
            config: The config to associate with the checkpoint.
            checkpoint: The checkpoint to save.
            metadata: Additional metadata to save with the checkpoint.
            new_versions: New channel versions as of this write.

        Returns:
            RunnableConfig: Updated configuration after storing the checkpoint.
        """
        return asyncio.run_coroutine_threadsafe(
            self.aput(config, checkpoint, metadata, new_versions), self.loop
        ).result()

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        return asyncio.run_coroutine_threadsafe(
            self.aput_writes(config, writes, task_id, task_path), self.loop
        ).result()

    def delete_thread(self, thread_id: str) -> None:
        """Delete all checkpoints and writes associated with a thread ID.

        Args:
            thread_id: The thread ID to delete.

        Returns:
            None
        """
        try:
            # check if we are in the main thread, only bg threads can block
            # we don't check in other methods to avoid the overhead
            if asyncio.get_running_loop() is self.loop:
                raise asyncio.InvalidStateError(
                    "Synchronous calls to AsyncSqliteSaver are only allowed from a "
                    "different thread. From the main thread, use the async interface. "
                    "For example, use `checkpointer.alist(...)` or `await "
                    "graph.ainvoke(...)`."
                )
        except RuntimeError:
            pass
        return asyncio.run_coroutine_threadsafe(
            self.adelete_thread(thread_id), self.loop
        ).result()

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Get a checkpoint tuple from the database asynchronously.

        This method retrieves a checkpoint tuple from the SQLite database based on the
        provided config. If the config contains a `checkpoint_id` key, the checkpoint with
        the matching thread ID and checkpoint ID is retrieved. Otherwise, the latest checkpoint
        for the given thread ID is retrieved.

        Args:
            config: The config to use for retrieving the checkpoint.

        Returns:
            The retrieved checkpoint tuple, or None if no matching checkpoint was found.
        """
        await self.setup()
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        async with self.lock:
            # find the latest checkpoint for the thread_id
            if checkpoint_id := get_checkpoint_id(config):
                checkpoint_query = await CheckpointModel.filter(
                    thread_id=str(config["configurable"]["thread_id"]),
                    checkpoint_ns=checkpoint_ns,
                    checkpoint_id=checkpoint_id,
                ).first()
            else:
                checkpoint_query = await CheckpointModel.filter(
                    thread_id=str(config["configurable"]["thread_id"]),
                    checkpoint_ns=checkpoint_ns,
                ).order_by("-checkpoint_id").first()
            if checkpoint_query:
                if not get_checkpoint_id(config):
                    config = {
                        "configurable": {
                            "thread_id": checkpoint_query.thread_id,
                            "checkpoint_ns": checkpoint_ns,
                            "checkpoint_id": checkpoint_query.checkpoint_id,
                        }
                    }
                # find any pending writes
                writes_query = (
                    WriteModel.filter(
                        thread_id=checkpoint_query.thread_id,
                        checkpoint_ns=checkpoint_query.checkpoint_ns,
                        checkpoint_id=checkpoint_query.checkpoint_id,
                    ).order_by("task_id", "idx")
                )

                writes = [
                    (w.task_id, w.channel, self.serde.loads_typed((w.type, w.value)))
                    async for w in writes_query
                ]

                return CheckpointTuple(
                    config,
                    self.serde.loads_typed(
                        (checkpoint_query.type, checkpoint_query.checkpoint)
                    ),
                    cast(
                        CheckpointMetadata,
                        (
                            json.loads(checkpoint_query.metadata)
                            if checkpoint_query.metadata is not None
                            else {}
                        ),
                    ),
                    (
                        {
                            "configurable": {
                                "thread_id": checkpoint_query.thread_id,
                                "checkpoint_ns": checkpoint_ns,
                                "checkpoint_id": checkpoint_query.parent_checkpoint_id,
                            }
                        }
                        if checkpoint_query.parent_checkpoint_id
                        else None
                    ),
                    writes,
                )

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """List checkpoints from the database asynchronously.

        This method retrieves a list of checkpoint tuples from the SQLite database based
        on the provided config. The checkpoints are ordered by checkpoint ID in descending order (newest first).

        Args:
            config: Base configuration for filtering checkpoints.
            filter: Additional filtering criteria for metadata.
            before: If provided, only checkpoints before the specified checkpoint ID are returned.
            limit: Maximum number of checkpoints to return.

        Yields:
            An asynchronous iterator of matching checkpoint tuples.
        """
        await self.setup()
        where, params = search_where(config, filter, before)
        async with self.lock:
            query = CheckpointModel.all()
            if where:
                query = query.filter_raw(where, *params)
            query = query.order_by("-checkpoint_id")
            if limit:
                query = query.limit(limit)
            async for checkpoint_query in query:
                # find any pending writes
                writes_query = (
                    WriteModel.filter(
                        thread_id=checkpoint_query.thread_id,
                        checkpoint_ns=checkpoint_query.checkpoint_ns,
                        checkpoint_id=checkpoint_query.checkpoint_id,
                    ).order_by("task_id", "idx")
                )

                writes = [
                    (w.task_id, w.channel, self.serde.loads_typed((w.type, w.value)))
                    async for w in writes_query
                ]

                yield CheckpointTuple(
                    {
                        "configurable": {
                            "thread_id": checkpoint_query.thread_id,
                            "checkpoint_ns": checkpoint_query.checkpoint_ns,
                            "checkpoint_id": checkpoint_query.checkpoint_id,
                        }
                    },
                    self.serde.loads_typed(
                        (checkpoint_query.type, checkpoint_query.checkpoint)
                    ),
                    cast(
                        CheckpointMetadata,
                        (
                            json.loads(checkpoint_query.metadata)
                            if checkpoint_query.metadata is not None
                            else {}
                        ),
                    ),
                    (
                        {
                            "configurable": {
                                "thread_id": checkpoint_query.thread_id,
                                "checkpoint_ns": checkpoint_query.checkpoint_ns,
                                "checkpoint_id": checkpoint_query.parent_checkpoint_id,
                            }
                        }
                        if checkpoint_query.parent_checkpoint_id
                        else None
                    ),
                    writes,
                )

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Save a checkpoint to the database asynchronously.

        This method saves a checkpoint to the SQLite database. The checkpoint is associated
        with the provided config and its parent config (if any).

        Args:
            config: The config to associate with the checkpoint.
            checkpoint: The checkpoint to save.
            metadata: Additional metadata to save with the checkpoint.
            new_versions: New channel versions as of this write.

        Returns:
            RunnableConfig: Updated configuration after storing the checkpoint.
        """
        await self.setup()
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"]["checkpoint_ns"]
        type_, serialized_checkpoint = self.serde.dumps_typed(checkpoint)
        serialized_metadata = json.dumps(
            get_checkpoint_metadata(config, metadata), ensure_ascii=False
        ).encode("utf-8", "ignore")
        async with self.lock:
            await CheckpointModel.create(
                thread_id=str(thread_id),
                checkpoint_ns=checkpoint_ns,
                checkpoint_id=checkpoint["id"],
                parent_checkpoint_id=config["configurable"].get("checkpoint_id"),
                type=type_,
                checkpoint=serialized_checkpoint,
                metadata=serialized_metadata,
            )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Store intermediate writes linked to a checkpoint asynchronously.

        This method saves intermediate writes associated with a checkpoint to the database.

        Args:
            config: Configuration of the related checkpoint.
            writes: List of writes to store, each as (channel, value) pair.
            task_id: Identifier for the task creating the writes.
            task_path: Path of the task creating the writes.
        """
        await self.setup()
        async with self.lock:
            for idx, (channel, value) in enumerate(writes):
                await WriteModel.create(
                    thread_id=str(config["configurable"]["thread_id"]),
                    checkpoint_ns=str(config["configurable"]["checkpoint_ns"]),
                    checkpoint_id=str(config["configurable"]["checkpoint_id"]),
                    task_id=task_id,
                    idx=WRITES_IDX_MAP.get(channel, idx),
                    channel=channel,
                    type=self.serde.dumps_typed(value)[0],
                    value=self.serde.dumps_typed(value)[1],
                )

    async def adelete_thread(self, thread_id: str) -> None:
        """Delete all checkpoints and writes associated with a thread ID.

        Args:
            thread_id: The thread ID to delete.

        Returns:
            None
        """
        async with self.lock:
            await CheckpointModel.filter(thread_id=str(thread_id)).delete()
            await WriteModel.filter(thread_id=str(thread_id)).delete()

    def get_next_version(self, current: str | None, channel: None) -> str:
        """Generate the next version ID for a channel.

        This method creates a new version identifier for a channel based on its current version.

        Args:
            current (Optional[str]): The current version identifier of the channel.

        Returns:
            str: The next version identifier, which is guaranteed to be monotonically increasing.
        """
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(current.split(".")[0])
        next_v = current_v + 1
        next_h = random.random()
        return f"{next_v:032}.{next_h:016}"
