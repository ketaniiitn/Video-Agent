from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import partial
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

from app.config import Settings
from app.db.urls import to_psycopg_url
from app.gateway.protocols import GatewayClient
from app.graph.state import VideoAgentState
from app.nodes.chain_frame import make_chain_frame_node
from app.nodes.generate_shot import make_generate_shot_node
from app.nodes.assemble import make_assemble_node
from app.nodes.deliver import make_deliver_node
from app.nodes.flag_degraded import make_flag_degraded_node
from app.nodes.lock_continuity_bible import (
    SessionFactory,
    lock_continuity_bible_node,
)
from app.nodes.plan_story import plan_story_node
from app.nodes.qc_score import make_qc_score_node
from app.nodes.repair_shot import make_repair_shot_node
from app.pipeline.constants import MAX_REPAIR_ATTEMPTS
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
    qc_repair = settings.feature_qc_repair
    assemble_deliver = settings.feature_assemble_deliver
    terminal_after_chain = not qc_repair and not assemble_deliver

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
                terminal_after_chain=terminal_after_chain,
            ),
        )
        graph.add_node(
            f"qc_score_{beat_index}",
            make_qc_score_node(
                beat_index,
                gateway=gateway,
                session_factory=session_factory,
                media_root=media_root,
                assemble_deliver=assemble_deliver,
            ),
        )
        graph.add_node(
            f"repair_shot_{beat_index}",
            make_repair_shot_node(
                beat_index,
                provider=provider,
                session_factory=session_factory,
                media_root=media_root,
            ),
        )
        graph.add_node(
            f"flag_degraded_{beat_index}",
            make_flag_degraded_node(
                beat_index,
                session_factory=session_factory,
                assemble_deliver=assemble_deliver,
            ),
        )
    graph.add_node(
        "assemble",
        make_assemble_node(session_factory=session_factory, media_root=media_root),
    )
    graph.add_node(
        "deliver",
        make_deliver_node(
            session_factory=session_factory,
            settings=settings,
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
            lambda state, assemble=assemble_deliver, b=beat_index: _after_generate(
                state, assemble=assemble, beat=b
            ),
            {
                f"chain_frame_{beat_index}": f"chain_frame_{beat_index}",
                "assemble": "assemble",
                "end": END,
            },
        )
        graph.add_conditional_edges(
            f"chain_frame_{beat_index}",
            lambda state, b=beat_index, qc=qc_repair, assemble=assemble_deliver: _after_chain(
                state, beat=b, qc=qc, assemble=assemble
            ),
            {
                f"qc_score_{beat_index}": f"qc_score_{beat_index}",
                **(
                    {f"generate_shot_{beat_index + 1}": f"generate_shot_{beat_index + 1}"}
                    if beat_index < 4
                    else {}
                ),
                "assemble": "assemble",
                "end": END,
            },
        )
        graph.add_conditional_edges(
            f"qc_score_{beat_index}",
            lambda state, b=beat_index, assemble=assemble_deliver: _after_qc(
                state, beat=b, assemble=assemble
            ),
            {
                f"repair_shot_{beat_index}": f"repair_shot_{beat_index}",
                f"flag_degraded_{beat_index}": f"flag_degraded_{beat_index}",
                **(
                    {f"generate_shot_{beat_index + 1}": f"generate_shot_{beat_index + 1}"}
                    if beat_index < 4
                    else {}
                ),
                "assemble": "assemble",
                "end": END,
            },
        )
        graph.add_edge(f"repair_shot_{beat_index}", f"chain_frame_{beat_index}")
        graph.add_conditional_edges(
            f"flag_degraded_{beat_index}",
            lambda state, b=beat_index, assemble=assemble_deliver: _after_flag(
                state, beat=b, assemble=assemble
            ),
            {
                **(
                    {f"generate_shot_{beat_index + 1}": f"generate_shot_{beat_index + 1}"}
                    if beat_index < 4
                    else {}
                ),
                "assemble": "assemble",
                "end": END,
            },
        )
    graph.add_edge("assemble", "deliver")
    graph.add_edge("deliver", END)

    return graph.compile(checkpointer=checkpointer)


def _after_plan(state: VideoAgentState) -> str:
    if state.get("outcome") in {"PARTIAL", "FAILED", "FAILED_NO_PROGRESS"}:
        return "end"
    return "lock_continuity_bible"


def _after_bible(state: VideoAgentState, *, shot_generation: bool) -> str:
    if state.get("outcome") in {"PARTIAL", "FAILED", "FAILED_NO_PROGRESS"}:
        return "end"
    if shot_generation:
        return "generate_shot_1"
    return "end"


def _after_generate(state: VideoAgentState, *, assemble: bool, beat: int) -> str:
    outcome = state.get("outcome")
    if outcome == "FAILED_NO_PROGRESS":
        return "end"
    if outcome in {"PARTIAL", "FAILED"}:
        if assemble and outcome == "PARTIAL":
            return "assemble"
        return "end"
    return f"chain_frame_{beat}"


def _after_chain(
    state: VideoAgentState, *, beat: int, qc: bool, assemble: bool
) -> str:
    outcome = state.get("outcome")
    if outcome == "FAILED_NO_PROGRESS":
        return "end"
    if outcome in {"PARTIAL", "FAILED"}:
        if assemble and outcome == "PARTIAL":
            return "assemble"
        return "end"
    if qc:
        return f"qc_score_{beat}"
    return _next_after_shot(beat, assemble)


def _after_qc(state: VideoAgentState, *, beat: int, assemble: bool) -> str:
    outcome = state.get("outcome")
    if outcome == "FAILED_NO_PROGRESS":
        return "end"
    if outcome == "PARTIAL" and assemble:
        return "assemble"
    if outcome in {"PARTIAL", "FAILED"}:
        return "end"
    if state.get("qc_passed"):
        return _next_after_shot(beat, assemble)
    if int(state.get("repair_count") or 0) < MAX_REPAIR_ATTEMPTS:
        return f"repair_shot_{beat}"
    return f"flag_degraded_{beat}"


def _after_flag(state: VideoAgentState, *, beat: int, assemble: bool) -> str:
    return _next_after_shot(beat, assemble)


def _next_after_shot(beat: int, assemble: bool) -> str:
    if beat >= 4:
        return "assemble" if assemble else "end"
    return f"generate_shot_{beat + 1}"


@asynccontextmanager
async def postgres_checkpointer(
    settings: Settings,
) -> AsyncIterator[TenantAwarePostgresSaver]:
    connection_string = to_psycopg_url(settings.database_url)
    async with TenantAwarePostgresSaver.from_conn_string(
        connection_string
    ) as saver:
        yield saver
