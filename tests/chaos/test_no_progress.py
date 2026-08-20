import pytest

from app.providers.fake import FakeVideoProvider
from tests.nodes.test_generate_and_chain import make_state


@pytest.mark.asyncio
async def test_same_provider_failure_twice_is_failed_no_progress(node_db, tmp_path):
    from app.nodes.generate_shot import make_generate_shot_node

    _, session_factory, tenant_id, job_id = node_db
    provider = FakeVideoProvider(fail_times=2, fail_code="PROVIDER_UNAVAILABLE")
    node = make_generate_shot_node(
        1, provider=provider, session_factory=session_factory, media_root=str(tmp_path)
    )
    state = make_state(tenant_id, job_id)
    first = await node(state)
    assert first["outcome"] == "FAILED"
    assert first["last_failure_signature"] == "PROVIDER_UNAVAILABLE"
    assert first["last_error_message"]
    second = await node({**state, **first})
    assert second["outcome"] == "FAILED_NO_PROGRESS"
