import json
import logging

import pytest

from wfk_platform import ServiceSettings, configure_logging, setup_telemetry


def test_json_logs_carry_service_extra_fields_and_trace_ids(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("agent-runtime")
    telemetry = setup_telemetry(ServiceSettings(service_name="agent-runtime"), set_global=False)
    tracer = telemetry.tracer_provider.get_tracer("test")

    with tracer.start_as_current_span("step") as span:
        logging.getLogger("t").info("step done", extra={"run_id": "r-1"})
        expected_trace = format(span.get_span_context().trace_id, "032x")
    logging.getLogger("t").info("outside span")

    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    inside, outside = lines[-2], lines[-1]
    assert inside["service"] == "agent-runtime"
    assert inside["msg"] == "step done"
    assert inside["run_id"] == "r-1"
    assert inside["trace_id"] == expected_trace
    assert "trace_id" not in outside
    telemetry.shutdown()


def test_telemetry_resource_names_the_service() -> None:
    t = setup_telemetry(ServiceSettings(service_name="brain-api", cell_id="eu1"), set_global=False)
    attrs = t.tracer_provider.resource.attributes
    assert attrs["service.name"] == "brain-api"
    assert attrs["wfk.cell_id"] == "eu1"
    t.shutdown()
