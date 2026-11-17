"""Model calls through the Model Gateway (LiteLLM in the local stack, just up)."""

import os
from datetime import timedelta

import httpx
import pytest
from agent_harness.llm_providers import CompletionRequest
from agent_harness.types import Message
from temporalio.client import Client

from agent_runtime.adapters.model_gateway import gateway_provider
from agent_runtime.settings import Settings
from agent_runtime.workflows.employee_run import EmployeeRun
from agent_runtime.workflows.types import RunInput

from .conftest import TENANT, World, new_id, running_workers

pytestmark = pytest.mark.integration


@pytest.fixture
def settings() -> Settings:
    s = Settings()
    try:
        httpx.get(s.model_gateway_url.removesuffix("/v1") + "/health/liveliness", timeout=2)
    except httpx.HTTPError as exc:
        pytest.skip(f"Model Gateway not running (just up): {exc}")
    return s


async def test_gateway_answers_through_the_harness_provider(settings: Settings) -> None:
    provider = gateway_provider(settings)
    request = CompletionRequest.model_validate(
        {"model": "local-mock", "messages": [Message.user("Say hello.")], "max_tokens": 50}
    )
    response = await provider.complete(request)
    assert response.text == "Hello from the Model Gateway."
    await provider.aclose()


async def test_gateway_refuses_a_wrong_key_and_it_is_not_retried(settings: Settings) -> None:
    """Without a database, local LiteLLM answers an unknown key with 400 ("No connected db");
    the production gateway, which has one, answers 401. Either way: refused, never retried."""
    from agent_harness.errors import ProviderError

    from agent_runtime.harness.model import classify

    provider = gateway_provider(settings.model_copy(update={"model_gateway_key": "sk-wrong"}))
    request = CompletionRequest.model_validate(
        {"model": "local-mock", "messages": [Message.user("hi")], "max_tokens": 10}
    )
    with pytest.raises(ProviderError) as caught:
        await provider.complete(request)
    assert classify(caught.value) == "non_retryable"
    await provider.aclose()


async def run_through_gateway(client: Client, settings: Settings, model: str, task: str) -> str:
    world = World()
    async with running_workers(
        client,
        world,
        gateway_provider(settings),
        employee=lambda: world.employee(model=model),
    ) as queue:
        inp = RunInput(run_id=new_id(), tenant_id=TENANT, employee_id="sam", task=task)
        out = await client.execute_workflow(
            EmployeeRun.run,
            inp,
            id=inp.run_id,
            task_queue=queue,
            execution_timeout=timedelta(seconds=120),
        )
    assert out.state.status == "completed", out.state.output
    return out.state.output or ""


async def test_a_whole_run_through_the_gateway(client: Client, settings: Settings) -> None:
    out = await run_through_gateway(client, settings, "local-mock", "Say hello.")
    assert out == "Hello from the Model Gateway."


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
async def test_a_real_model_answers(client: Client, settings: Settings) -> None:
    out = await run_through_gateway(
        client, settings, "claude-haiku", "Reply with exactly the word: ready"
    )
    assert "ready" in out.lower()
