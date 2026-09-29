import json

from ultimatespray.templates import NAME_PREFIX, build_template


def test_build_template_is_valid_json_with_target():
    body = build_template("https://target.example.com/")
    doc = json.loads(body)
    assert doc["swagger"] == "2.0"
    assert doc["info"]["title"] == f"{NAME_PREFIX}_example"
    root = doc["paths"]["/"]["get"]["x-amazon-apigateway-integration"]
    assert root["uri"] == "https://target.example.com/"
    proxy = doc["paths"]["/{proxy+}"]["x-amazon-apigateway-any-method"][
        "x-amazon-apigateway-integration"
    ]
    assert proxy["uri"] == "https://target.example.com/{proxy}"


def test_build_template_strips_trailing_slash():
    body = build_template("https://a.b.com///")
    doc = json.loads(body)
    root = doc["paths"]["/"]["get"]["x-amazon-apigateway-integration"]
    assert root["uri"] == "https://a.b.com/"
