import io
import json

from pydantic import SecretStr

from qp.platform import SecretRedactor, configure_logging, get_logger
from qp.platform.settings import LogSettings


def run(redactor: SecretRedactor, **event: object) -> dict[str, object]:
    return dict(redactor(None, "info", dict(event)))


def test_redacts_by_key_type_and_value() -> None:
    r = SecretRedactor(known_secrets=["hunter22", "x"])
    out = run(
        r,
        event="connecting with hunter22",
        password="hunter22",
        api_key="abc",
        token=SecretStr("t"),
        nested={"db_password": "p", "host": "h"},
        items=["hunter22", 1],
        harmless="ok",
    )
    assert out["event"] == "connecting with ***"
    assert out["password"] == "***"
    assert out["api_key"] == "***"
    assert out["token"] == "***"
    assert out["nested"] == {"db_password": "***", "host": "h"}
    assert out["items"] == ["***", 1]
    assert out["harmless"] == "ok"


def test_short_known_secrets_are_ignored() -> None:
    # Secrets shorter than 4 chars would shred normal text; they are not value-scrubbed.
    assert run(SecretRedactor(["x"]), event="xyz")["event"] == "xyz"


def test_json_output_with_utc_timestamp() -> None:
    stream = io.StringIO()
    configure_logging(LogSettings(level="INFO"), known_secrets=["s3cr3t-value"], stream=stream)
    get_logger("t").info("hello", password="p", note="has s3cr3t-value inside")
    get_logger("t").debug("filtered out")
    lines = stream.getvalue().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "hello"
    assert record["level"] == "info"
    assert record["password"] == "***"
    assert record["note"] == "has *** inside"
    assert record["ts"].endswith("Z")
