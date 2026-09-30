from jarvis import main
from jarvis.brain import subscription_env


def test_empty_oauth_token_is_dropped(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-api")
    env = subscription_env()
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env and "ANTHROPIC_API_KEY" not in env
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-x")
    assert subscription_env()["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-x"


def test_set_env_value(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("A=1\nCLAUDE_CODE_OAUTH_TOKEN=\nB=2\nCLAUDE_CODE_OAUTH_TOKEN=alt\n")
    main.set_env_value("CLAUDE_CODE_OAUTH_TOKEN", "neu")
    assert (tmp_path / ".env").read_text() == "A=1\nCLAUDE_CODE_OAUTH_TOKEN=neu\nB=2\n"
    main.set_env_value("C", "3")
    assert (tmp_path / ".env").read_text().endswith("C=3\n")
