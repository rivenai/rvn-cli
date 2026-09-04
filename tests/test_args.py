"""Unit tests — argument parsing (no network)."""

import pytest

from rvn.cli import build_parser, main


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0


def test_chat_query():
    args = build_parser().parse_args(["chat", "who is the CEO of NVIDIA"])
    assert args.command == "chat"
    assert args.query == ["who is the CEO of NVIDIA"]
    assert args.model == "riven-research"
    assert args.interactive is False


def test_chat_model_flag():
    args = build_parser().parse_args(["chat", "-m", "riven-core", "hi"])
    assert args.model == "riven-core"


def test_chat_interactive():
    args = build_parser().parse_args(["chat", "-i"])
    assert args.interactive is True
    assert args.query == []


def test_chat_multiword_query_joins():
    args = build_parser().parse_args(["chat", "what", "is", "2+2"])
    assert " ".join(args.query) == "what is 2+2"


def test_models():
    args = build_parser().parse_args(["models"])
    assert args.command == "models"


def test_json_flag_global():
    args = build_parser().parse_args(["--json", "models"])
    assert args.json_mode is True
    args = build_parser().parse_args(["models"])
    assert args.json_mode is False


def test_deep_defaults():
    args = build_parser().parse_args(["deep", "compare rust and zig"])
    assert args.command == "deep"
    assert args.timeout == 300
    assert args.task is None


def test_deep_task_flag():
    args = build_parser().parse_args(["deep", "--task", "abc123"])
    assert args.task == "abc123"


def test_pages_tier_choices():
    args = build_parser().parse_args(["pages", "topic here", "--tier", "pro"])
    assert args.tier == "pro"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["pages", "t", "--tier", "bogus"])


def test_login_logout_need_no_key(monkeypatch):
    monkeypatch.setattr("rvn.config.KEY_FILE", __import__("pathlib").Path("/nonexistent-rvn-test/key"))
    assert main(["logout"]) == 0


def test_missing_command_prints_help(capsys):
    assert main([]) == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_chat_requires_key(monkeypatch, capsys):
    monkeypatch.delenv("RIVEN_API_KEY", raising=False)
    monkeypatch.setattr("rvn.config.KEY_FILE", __import__("pathlib").Path("/nonexistent-rvn-test/key"))
    rc = main(["chat", "hello"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "rvn login" in err
    assert "RIVEN_API_KEY" in err
    # the key hint must never ask to paste secrets into argv output
    assert "rvn_" in err  # prefix hint only, not a value


def test_key_flag_prefix_validation(monkeypatch, capsys):
    monkeypatch.delenv("RIVEN_API_KEY", raising=False)
    rc = main(["--key", "sk-not-a-riven-key", "models"])
    assert rc == 2
    assert "rvn_" in capsys.readouterr().err
