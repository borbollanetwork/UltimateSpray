import json

import pytest

from ultimatespray import cli
from ultimatespray.simulate import validate_url


def test_simulate_writes_unverified_private_report_without_network(tmp_path, monkeypatch, capsys):
    users = tmp_path / "users.txt"
    users.write_text("alice@example.test\nbob@example.test\nalice@example.test\n")
    output = tmp_path / "report.json"
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "example-secret")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "_client", lambda _: pytest.fail("AWS was accessed"))
    monkeypatch.setattr(cli, "RotatingProxy", lambda _: pytest.fail("Network was accessed"))

    rc = cli.main([
        "simulate", "--users", str(users), "--url", "https://lab.example.test/login",
        "--output", str(output),
    ])

    assert rc == 0
    report = json.loads(output.read_text())
    assert report["network_requests"] == 0
    assert report["summary"] == {"users": 2, "not_tested": 2}
    assert [item["outcome"] for item in report["results"]] == ["not_tested"] * 2
    assert "example-secret" not in output.read_text() + capsys.readouterr().out
    assert output.stat().st_mode & 0o077 == 0


def test_simulate_prompts_for_missing_fields(tmp_path, monkeypatch):
    users = tmp_path / "users.txt"
    users.write_text("user@example.test\n")
    output = tmp_path / "report.json"
    answers = iter((str(users), "https://lab.example.test", str(output)))
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "example-secret")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    assert cli.main(["simulate"]) == 0
    assert output.exists()


def test_simulate_does_not_overwrite_existing_report(tmp_path, monkeypatch):
    users = tmp_path / "users.txt"
    users.write_text("user@example.test\n")
    output = tmp_path / "report.json"
    output.write_text("existing")
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "example-secret")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    assert cli.main([
        "simulate", "--users", str(users), "--url", "https://lab.example.test",
        "--output", str(output),
    ]) == 1
    assert output.read_text() == "existing"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://user:pass@host.test/",
                                  "https://host.test/path?token=123"])
def test_simulate_rejects_unsafe_url_contents(url):
    with pytest.raises(ValueError):
        validate_url(url)
