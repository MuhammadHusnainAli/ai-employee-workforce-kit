import pytest
from pydantic import ValidationError

from wfk_platform import ServiceSettings


def test_reads_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WFK_SERVICE_NAME", "agent-runtime")
    monkeypatch.setenv("WFK_HTTP_PORT", "9000")
    monkeypatch.setenv("WFK_ENVIRONMENT", "test")
    s = ServiceSettings()  # type: ignore[call-arg]
    assert (s.service_name, s.http_port, s.environment) == ("agent-runtime", 9000, "test")


def test_defaults_point_at_local_stack() -> None:
    s = ServiceSettings(service_name="x")
    assert s.temporal_address == "127.0.0.1:7001"
    assert s.database_url.startswith("postgresql://")
    assert s.otlp_endpoint == ""


def test_rejects_invalid_values() -> None:
    with pytest.raises(ValidationError):
        ServiceSettings(service_name="x", http_port=0)
    with pytest.raises(ValidationError):
        ServiceSettings(service_name="x", environment="prod")  # type: ignore[arg-type]
