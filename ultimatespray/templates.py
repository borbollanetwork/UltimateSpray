"""Swagger/OpenAPI template used to import the pass-through proxy REST API."""

from __future__ import annotations

import datetime
import json

import tldextract

# Base name prefix used for every proxy created by this tool. cleanup() relies
# on it to find and remove only the APIs that UltimateSpray created.
NAME_PREFIX = "ultimatespray"


def _swagger(url: str, title: str, version_date: str) -> dict:
    integration = {
        "responses": {"default": {"statusCode": "200"}},
        "requestParameters": {
            "integration.request.path.proxy": "method.request.path.proxy",
            "integration.request.header.X-Forwarded-For": (
                "method.request.header.X-My-X-Forwarded-For"
            ),
        },
        "passthroughBehavior": "when_no_match",
        "httpMethod": "ANY",
        "cacheNamespace": "irx7tm",
        "cacheKeyParameters": ["method.request.path.proxy"],
        "type": "http_proxy",
    }
    parameters = [
        {"name": "proxy", "in": "path", "required": True, "type": "string"},
        {
            "name": "X-My-X-Forwarded-For",
            "in": "header",
            "required": False,
            "type": "string",
        },
    ]
    root_integration = dict(integration, uri=f"{url}/")
    proxy_integration = dict(integration, uri=f"{url}/{{proxy}}")
    return {
        "swagger": "2.0",
        "info": {"version": version_date, "title": title},
        "basePath": "/",
        "schemes": ["https"],
        "paths": {
            "/": {
                "get": {
                    "parameters": parameters,
                    "responses": {},
                    "x-amazon-apigateway-integration": root_integration,
                }
            },
            "/{proxy+}": {
                "x-amazon-apigateway-any-method": {
                    "produces": ["application/json"],
                    "parameters": parameters,
                    "responses": {},
                    "x-amazon-apigateway-integration": proxy_integration,
                }
            },
        },
    }


def build_template(url: str) -> bytes:
    """Build the API Gateway import body for a pass-through proxy to ``url``."""
    url = url.rstrip("/")
    title = f"{NAME_PREFIX}_{tldextract.extract(url).domain}"
    version_date = f"{datetime.datetime.now():%Y-%m-%dT%XZ}"
    return json.dumps(_swagger(url, title, version_date)).encode()
