from unittest import mock

import pytest

from ultimatespray.core import UltimateSpray


def _core():
    core = UltimateSpray.__new__(UltimateSpray)
    core.region = "us-east-1"
    core.client = mock.MagicMock()
    return core


def test_get_apis_follows_every_page():
    core = _core()
    core.client.get_rest_apis.side_effect = [
        {"items": [{"id": "first"}], "position": "next"},
        {"items": [{"id": "second"}]},
    ]
    assert [item["id"] for item in core._get_apis()] == ["first", "second"]
    core.client.get_rest_apis.assert_any_call(position="next")


def test_cleanup_requires_management_tag():
    core = _core()
    core.client.get_rest_apis.return_value = {
        "items": [
            {"id": "owned", "name": "ultimatespray_owned"},
            {"id": "foreign", "name": "ultimatespray_foreign"},
            {"id": "other", "name": "unrelated"},
        ]
    }
    core.client.get_tags.side_effect = [
        {"tags": {"ultimatespray:managed": "true"}},
        {"tags": {}},
    ]
    assert core.cleanup() == ["owned"]
    core.client.delete_rest_api.assert_called_once_with(restApiId="owned")


def test_create_rolls_back_if_deployment_fails():
    core = _core()
    core.client.import_rest_api.return_value = {"id": "created"}
    core.client.create_deployment.side_effect = RuntimeError("deployment failed")
    with mock.patch("ultimatespray.core.build_template", return_value=b"{}"):
        with pytest.raises(RuntimeError, match="deployment failed"):
            core.create_api("https://example.com")
    core.client.tag_resource.assert_called_once_with(
        resourceArn="arn:aws:apigateway:us-east-1::/restapis/created",
        tags={"ultimatespray:managed": "true"},
    )
    core.client.delete_rest_api.assert_called_once_with(restApiId="created")


def test_explicit_aws_credentials_take_precedence_over_profile():
    fake = mock.MagicMock()
    fake._client_config.region_name = "us-east-1"
    with mock.patch.object(UltimateSpray, "_client", return_value=fake) as make_client:
        core = UltimateSpray(
            profile_name="unused-profile",
            access_key="example-access-key",
            secret_access_key="example-secret-key",
            region="us-east-1",
        )
    assert core.client is fake
    make_client.assert_called_once_with(
        aws_access_key_id="example-access-key",
        aws_secret_access_key="example-secret-key",
        aws_session_token=None,
        region_name="us-east-1",
    )
