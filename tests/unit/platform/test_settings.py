from pathlib import Path

import pytest

from qp.core import ConfigError
from qp.platform import Mode, live_trading_env_acknowledged, load_settings


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    d = tmp_path / "configs"
    write(
        d / "base.yaml", "mode: paper\ndb:\n  host: basehost\n  port: 5433\nlog:\n  level: INFO\n"
    )
    write(d / "backtest" / "settings.yaml", "log:\n  level: WARNING\n")
    write(d / "live" / "settings.yaml", "db:\n  host: livehost\n")
    return d


def test_repo_configs_load(config_dir: Path) -> None:
    for mode in Mode:
        s = load_settings(config_dir=config_dir, mode=mode, env_file=None)
        assert s.mode is mode


def test_default_mode_is_paper(cfg: Path) -> None:
    write(cfg / "base.yaml", "db:\n  host: h\n")
    assert load_settings(config_dir=cfg, env_file=None).mode is Mode.PAPER


def test_precedence_base_overlay_env(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = load_settings(config_dir=cfg, mode="live", env_file=None)
    assert s.db.host == "livehost"  # overlay beats base
    assert s.db.port == 5433  # untouched base value survives the deep merge
    monkeypatch.setenv("QP_DB__HOST", "envhost")
    s = load_settings(config_dir=cfg, mode="live", env_file=None)
    assert s.db.host == "envhost"  # env beats overlay
    assert s.db.port == 5433


def test_mode_from_process_env(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QP_MODE", "backtest")
    s = load_settings(config_dir=cfg, env_file=None)
    assert s.mode is Mode.BACKTEST
    assert s.log.level == "WARNING"


def test_dotenv_is_read_and_beats_yaml(cfg: Path, tmp_path: Path) -> None:
    env = write(tmp_path / ".env", "QP_DB__HOST=dotenvhost\nQP_DB__PASSWORD=s3cret-pw\n")
    s = load_settings(config_dir=cfg, env_file=env)
    assert s.db.host == "dotenvhost"
    assert s.db.password.get_secret_value() == "s3cret-pw"


def test_mode_conflict_via_dotenv_is_error(cfg: Path, tmp_path: Path) -> None:
    env = write(tmp_path / ".env", "QP_MODE=live\n")
    with pytest.raises(ConfigError, match="mode conflict"):
        load_settings(config_dir=cfg, env_file=env)


@pytest.mark.parametrize(
    "yaml_text", ["db:\n  password: hunter2\n", "telegram:\n  bot_token: '123:abc'\n"]
)
def test_secrets_in_yaml_are_rejected(cfg: Path, yaml_text: str) -> None:
    write(cfg / "base.yaml", yaml_text)
    with pytest.raises(ConfigError, match="secrets come only from env"):
        load_settings(config_dir=cfg, env_file=None)


def test_secret_in_overlay_rejected(cfg: Path) -> None:
    write(cfg / "live" / "settings.yaml", "db:\n  password: x\n")
    with pytest.raises(ConfigError, match="secret"):
        load_settings(config_dir=cfg, mode="live", env_file=None)


def test_overlay_cannot_switch_mode(cfg: Path) -> None:
    write(cfg / "backtest" / "settings.yaml", "mode: live\n")
    with pytest.raises(ConfigError, match="sets mode"):
        load_settings(config_dir=cfg, mode="backtest", env_file=None)


def test_unknown_keys_and_modes_rejected(cfg: Path) -> None:
    write(cfg / "base.yaml", "dbb:\n  host: typo\n")
    with pytest.raises(ConfigError, match="invalid settings"):
        load_settings(config_dir=cfg, env_file=None)
    with pytest.raises(ConfigError, match="unknown mode"):
        load_settings(config_dir=cfg, mode="yolo", env_file=None)


def test_missing_and_malformed_base(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing base config"):
        load_settings(config_dir=tmp_path, env_file=None)
    write(tmp_path / "base.yaml", "- a list\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_settings(config_dir=tmp_path, env_file=None)
    write(tmp_path / "base.yaml", "a: [unclosed\n")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_settings(config_dir=tmp_path, env_file=None)


def test_fingerprint_stable_and_secret_free(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a = load_settings(config_dir=cfg, env_file=None).fingerprint()
    monkeypatch.setenv("QP_DB__PASSWORD", "changes-nothing")
    b = load_settings(config_dir=cfg, env_file=None)
    assert a == b.fingerprint()
    assert b.secret_values() == ["changes-nothing"]
    monkeypatch.setenv("QP_DB__HOST", "other")
    assert load_settings(config_dir=cfg, env_file=None).fingerprint() != a


def test_live_ack_requires_exact_value() -> None:
    assert not live_trading_env_acknowledged({})
    assert not live_trading_env_acknowledged({"QP_LIVE_TRADING": "yes"})
    assert live_trading_env_acknowledged({"QP_LIVE_TRADING": "I_UNDERSTAND_THE_RISK"})


def test_live_ack_in_dotenv_is_rejected(cfg: Path, tmp_path: Path) -> None:
    env = write(tmp_path / ".env", "QP_LIVE_TRADING=I_UNDERSTAND_THE_RISK\n")
    with pytest.raises(ConfigError, match="only be set in the process environment"):
        load_settings(config_dir=cfg, env_file=env)


def test_live_ack_is_not_a_settings_field(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QP_LIVE_TRADING", "I_UNDERSTAND_THE_RISK")
    s = load_settings(config_dir=cfg, env_file=None)
    assert "live_trading" not in s.model_dump()
