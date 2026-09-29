from unittest import mock

from ultimatespray import cli


def _fake_client():
    client = mock.MagicMock()
    client.create_api.return_value = {
        "api_id": "abc123", "name": "ultimatespray_example",
        "created": "2026-01-01", "region": "us-east-1",
        "target": "https://target.example.com", "proxy_url": "https://abc123/",
    }
    client.list_api.return_value = []
    client.delete_api.return_value = True
    client.cleanup.return_value = ["abc123"]
    return client


def test_create_parses_positional_url(capsys):
    with mock.patch.object(cli, "_client", return_value=_fake_client()) as m:
        rc = cli.main(["create", "https://target.example.com"])
    assert rc == 0
    m.return_value.create_api.assert_called_once_with("https://target.example.com")


def test_create_multi_region():
    fake = _fake_client()
    with mock.patch.object(cli, "_client", return_value=fake):
        rc = cli.main(["create", "https://t.example.com", "--regions", "us-east-1,eu-west-1"])
    assert rc == 0
    assert fake.create_api.call_count == 2


def test_delete_positional_api_id():
    fake = _fake_client()
    with mock.patch.object(cli, "_client", return_value=fake):
        rc = cli.main(["delete", "abc123"])
    assert rc == 0
    fake.delete_api.assert_called_once_with("abc123")


def test_legacy_command_flag_still_routes(capsys):
    fake = _fake_client()
    with mock.patch.object(cli, "_client", return_value=fake):
        rc = cli.main(["--command", "delete", "--api_id", "abc123"])
    assert rc == 0
    fake.delete_api.assert_called_once_with("abc123")


def test_cleanup_requires_confirmation_or_yes():
    fake = _fake_client()
    with mock.patch.object(cli, "_client", return_value=fake):
        rc = cli.main(["cleanup", "--yes"])
    assert rc == 0
    fake.cleanup.assert_called_once()
