from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import partial
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

from app.config import Settings
from app.gateway.protocols import GatewayClient
from app.graph.state import VideoAgentState
from app.nodes.chain_frame import make_chain_frame_node
from app.nodes.generate_shot import make_generate_shot_node
from app.nodes.lock_continuity_bible import (
    SessionFactory,
    lock_continuity_bible_node,
)
from app.nodes.plan_story import plan_story_node
from app.providers.protocols import VideoProvider
from app.providers.registry import build_provider

_checkpoint_tenant_id: ContextVar[str | None] = ContextVar(
    "checkpoint_tenant_id", default=None
)


class TenantAwarePostgresSaver(AsyncPostgresSaver):
    """Apply the checkpoint tenant to the same cursor used by saver SQL.

    The upstream saver does not include this application's ``tenant_id`` column
    in its inserts. The column default stamps the active PostgreSQL setting.
    Keeping set/reset inside the saver cursor lock also prevents tenant context
    from leaking when a connection is reused.
    """

    @asynccontextmanager
    async def _cursor(self, *, pipeline: bool = False):
        tenant_id = _checkpoint_tenant_id.get()
        if tenant_id is None:
            raise ValueError("Checkpoint config requires configurable.tenant_id")
        async with super()._cursor(pipeline=pipeline) as cursor:
            await cursor.execute(
                "SELECT set_config('app.tenant_id', %s, false)",
                (tenant_id,),
            )
            try:
                yield cursor
            finally:
                await cursor.execute(
                    "SELECT set_config('app.tenant_id', '', false)"
                )

    async def aget_tuple(self, config):
        tenant_id = _tenant_from(config)
        with self._tenant_scope(config):
            checkpoint = await super().aget_tuple(config)
        return _stamp_checkpoint_tuple(checkpoint, tenant_id)

    async def alist(
        self,
        config,
        *,
        filter: dict[str, Any] | None = None,
        before=None,
        limit: int | None = None,
    ):
        tenant_id = _tenant_from(config)
        with self._tenant_scope(config):
            async for checkpoint in super().alist(
                config, filter=filter, before=before, limit=limit
            ):
                yield _stamp_checkpoint_tuple(checkpoint, tenant_id)

    async def aput(self, config, checkpoint, metadata, new_versions):
        with self._tenant_scope(config):
            next_config = await super().aput(
                config, checkpoint, metadata, new_versions
            )
        next_config.setdefault("configurable", {})["tenant_id"] = _tenant_from(
            config
        )
        return next_config

    async def aput_writes(
        self, config, writes, task_id: str, task_path: str = ""
    ) -> None:
        with self._tenant_scope(config):
            await super().aput_writes(
                config, writes, task_id, task_path=task_path
            )

    def _tenant_scope(self, config):
        return _TenantScope(_tenant_from(config))


class _TenantScope:
    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self.token = None

    def __enter__(self):
        self.token = _checkpoint_tenant_id.set(self.tenant_id)

    def __exit__(self, exc_type, exc, traceback):
        _checkpoint_tenant_id.reset(self.token)


def _tenant_from(config) -> str:
    try:
        return str(config["configurable"]["tenant_id"])
    except (KeyError, TypeError) as exc:
        raise ValueError(
            "Checkpoint config requires configurable.tenant_id"
        ) from exc


def _stamp_checkpoint_tuple(checkpoint, tenant_id: str):
    if checkpoint is None:
        return None
    config = {
        **checkpoint.config,
        "configurable": {
            **checkpoint.config.get("configurable", {}),
            "tenant_id": tenant_id,
        },
    }
    parent_config = checkpoint.parent_config
    if parent_config is not None:
        parent_config = {
            **parent_config,
            "configurable": {
                **parent_config.get("configurable", {}),
                "tenant_id": tenant_id,
            },
        }
    return checkpoint._replace(config=config, parent_config=parent_config)


async def build_graph(
    checkpointer,
    *,
    gateway: GatewayClient,
    session_factory: SessionFactory,
    settings: Settings | None = None,
    provider: VideoProvider | None = None,
):
    settings = settings or Settings(_env_file=None)
    provider = provider or build_provider(settings)
    media_root = settings.media_root
    shot_generation = settings.feature_shot_generation

    graph = StateGraph(VideoAgentState)
    graph.add_node(
        "plan_story",
        partial(
            plan_story_node,
            gateway=gateway,
            session_factory=session_factory,
        ),
    )
    graph.add_node(
        "lock_continuity_bible",
        partial(
            lock_continuity_bible_node,
            gateway=gateway,
            session_factory=session_factory,
        ),
    )
    for beat_index in range(1, 5):
        graph.add_node(
            f"generate_shot_{beat_index}",
            make_generate_shot_node(
                beat_index,
                provider=provider,
                session_factory=session_factory,
                media_root=media_root,
            ),
        )
        graph.add_node(
            f"chain_frame_{beat_index}",
            make_chain_frame_node(
                beat_index,
                session_factory=session_factory,
                media_root=media_root,
            ),
        )

    graph.add_edge(START, "plan_story")
    graph.add_conditional_edges(
        "plan_story",
        _after_plan,
        {
            "lock_continuity_bible": "lock_continuity_bible",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "lock_continuity_bible",
        lambda state: _after_bible(state, shot_generation=shot_generation),
        {
            "generate_shot_1": "generate_shot_1",
            "end": END,
        },
    )
    for beat_index in range(1, 5):
        graph.add_conditional_edges(
            f"generate_shot_{beat_index}",
            _after_generate,
            {
                f"chain_frame_{beat_index}": f"chain_frame_{beat_index}",
                "end": END,
            },
        )
        if beat_index < 4:
            graph.add_conditional_edges(
                f"chain_frame_{beat_index}",
                _after_chain,
                {
                    f"generate_shot_{beat_index + 1}": f"generate_shot_{beat_index + 1}",
                    "end": END,
                },
            )
        else:
            graph.add_edge(f"chain_frame_{beat_index}", END)

    return graph.compile(checkpointer=checkpointer)


def _after_plan(state: VideoAgentState) -> str:
    if state.get("outcome") in {"PARTIAL", "FAILED"}:
        return "end"
    return "lock_continuity_bible"


def _after_bible(state: VideoAgentState, *, shot_generation: bool) -> str:
    if state.get("outcome") in {"PARTIAL", "FAILED"}:
        return "end"
    if shot_generation:
        return "generate_shot_1"
    return "end"


def _after_generate(state: VideoAgentState) -> str:
    if state.get("outcome") in {"PARTIAL", "FAILED"}:
        return "end"
    beat = int(state.get("current_beat_index") or 1)
    return f"chain_frame_{beat}"


def _after_chain(state: VideoAgentState) -> str:
    if state.get("outcome") in {"PARTIAL", "FAILED"}:
        return "end"
    beat = int(state.get("current_beat_index") or 1)
    if beat >= 4:
        return "end"
    return f"generate_shot_{beat + 1}"


@asynccontextmanager
async def postgres_checkpointer(
    settings: Settings,
) -> AsyncIterator[TenantAwarePostgresSaver]:
    connection_string = settings.database_url.replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    async with TenantAwarePostgresSaver.from_conn_string(
        connection_string
    ) as saver:
        yield saver
