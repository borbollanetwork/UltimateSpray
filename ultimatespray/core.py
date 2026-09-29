"""Core UltimateSpray class: manage AWS API Gateway pass-through proxies.

Each proxy rotates the source IP address on every request. Credentials are
resolved from (in order): explicit access key/secret, a named AWS profile, or
the ambient boto3 chain (env vars, instance profile, SSO, etc.).
"""

from __future__ import annotations

import configparser
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from ultimatespray.templates import NAME_PREFIX, build_template

logger = logging.getLogger("ultimatespray")

DEFAULT_REGION = "us-east-1"
STAGE_NAME = "ultimatespray"


class CredentialError(RuntimeError):
    """Raised when AWS credentials cannot be resolved."""


class UltimateSpray:
    def __init__(
        self,
        profile_name: str | None = None,
        access_key: str | None = None,
        secret_access_key: str | None = None,
        session_token: str | None = None,
        region: str | None = None,
    ) -> None:
        self.profile_name = profile_name
        self.access_key = access_key
        self.secret_access_key = secret_access_key
        self.session_token = session_token
        self.region = region
        self.client = None

        if (access_key and not secret_access_key) or (
            secret_access_key and not access_key
        ):
            raise CredentialError(
                "Provide both --access-key and --secret-access-key together"
            )
        if access_key and secret_access_key and not region:
            self.region = DEFAULT_REGION

        if not self._load_creds():
            raise CredentialError("Unable to load AWS credentials")

    def __repr__(self) -> str:
        return f"UltimateSpray(region={self.region!r})"

    # ------------------------------------------------------------------ creds
    def _client(self, **kwargs: Any):
        return boto3.client("apigateway", **kwargs)

    def _try_ambient_profile(self) -> bool:
        """Try the ambient boto3 credential chain (env, SSO, instance role)."""
        try:
            kwargs = {"region_name": self.region} if self.region else {}
            self.client = self._client(**kwargs)
            self.client.get_account()
            self.region = self.client._client_config.region_name
            return True
        except (BotoCoreError, ClientError, Exception) as exc:  # noqa: BLE001
            logger.debug("Ambient credential chain failed: %s", exc)
            return False

    def _load_creds(self) -> bool:
        if not any([self.access_key, self.secret_access_key, self.profile_name]):
            return self._try_ambient_profile()

        credentials = configparser.ConfigParser()
        credentials.read(os.path.expanduser("~/.aws/credentials"))
        config = configparser.ConfigParser()
        config.read(os.path.expanduser("~/.aws/config"))
        config_profile_section = f"profile {self.profile_name}"

        if self.profile_name and self.profile_name in credentials:
            if config_profile_section not in config:
                logger.error(
                    "Create a section for %s in your ~/.aws/config file",
                    self.profile_name,
                )
                return False
            self.region = config[config_profile_section].get("region", DEFAULT_REGION)
            try:
                self.client = boto3.session.Session(
                    profile_name=self.profile_name, region_name=self.region
                ).client("apigateway")
                self.client.get_account()
                return True
            except (BotoCoreError, ClientError) as exc:
                logger.debug("Profile %s failed: %s", self.profile_name, exc)

        if self.access_key and self.secret_access_key:
            try:
                self.client = self._client(
                    aws_access_key_id=self.access_key,
                    aws_secret_access_key=self.secret_access_key,
                    aws_session_token=self.session_token,
                    region_name=self.region,
                )
                self.client.get_account()
                self.region = self.client._client_config.region_name
                if self.profile_name:
                    self._persist_profile(config, credentials, config_profile_section)
                return True
            except (BotoCoreError, ClientError) as exc:
                logger.error("Access key credentials failed: %s", exc)
                return False
        return False

    def _persist_profile(self, config, credentials, section: str) -> None:
        """Save supplied keys under the given profile in ~/.aws for reuse."""
        if section not in config:
            config.add_section(section)
        config[section]["region"] = self.region
        with open(os.path.expanduser("~/.aws/config"), "w") as fh:
            config.write(fh)
        if self.profile_name not in credentials:
            credentials.add_section(self.profile_name)
        credentials[self.profile_name]["aws_access_key_id"] = self.access_key
        credentials[self.profile_name]["aws_secret_access_key"] = self.secret_access_key
        if self.session_token:
            credentials[self.profile_name]["aws_session_token"] = self.session_token
        elif credentials.has_option(self.profile_name, "aws_session_token"):
            credentials.remove_option(self.profile_name, "aws_session_token")
        with open(os.path.expanduser("~/.aws/credentials"), "w") as fh:
            credentials.write(fh)

    # --------------------------------------------------------------- proxy ops
    def _proxy_url(self, api_id: str) -> str:
        return (
            f"https://{api_id}.execute-api.{self.region}"
            f".amazonaws.com/{STAGE_NAME}/"
        )

    def create_api(self, url: str) -> dict:
        """Create a proxy that forwards to ``url``; returns proxy metadata."""
        if not url:
            raise ValueError("A valid target URL is required")
        logger.info("Creating proxy => %s", url)
        response = self.client.import_rest_api(
            parameters={"endpointConfigurationTypes": "REGIONAL"},
            body=build_template(url),
        )
        api_id = response["id"]
        self._create_deployment(api_id)
        return {
            "api_id": api_id,
            "name": response["name"],
            "created": str(response["createdDate"]),
            "region": self.region,
            "target": url.rstrip("/"),
            "proxy_url": self._proxy_url(api_id),
        }

    def update_api(self, api_id: str, url: str) -> bool:
        if not api_id or not url:
            raise ValueError("Both API ID and target URL are required")
        url = url.rstrip("/")
        resource_id = self.get_resource(api_id)
        if not resource_id:
            raise ValueError(f"No valid resource found for {api_id}")
        logger.info("Updating %s => %s", api_id, url)
        response = self.client.update_integration(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod="ANY",
            patchOperations=[
                {"op": "replace", "path": "/uri", "value": f"{url}/{{proxy}}"}
            ],
        )
        return response["uri"].replace("/{proxy}", "") == url

    def delete_api(self, api_id: str) -> bool:
        if not api_id:
            raise ValueError("A valid API ID is required")
        for item in self._get_apis():
            if item["id"] == api_id:
                self.client.delete_rest_api(restApiId=api_id)
                return True
        return False

    def list_api(self) -> list[dict]:
        """Return metadata for every UltimateSpray proxy in the region."""
        result = []
        for item in self._get_apis():
            try:
                api_id = item["id"]
                target = self.get_integration(api_id).replace("{proxy}", "")
                result.append(
                    {
                        "api_id": api_id,
                        "name": item["name"],
                        "created": str(item["createdDate"]),
                        "region": self.region,
                        "target": target,
                        "proxy_url": self._proxy_url(api_id),
                    }
                )
            except (BotoCoreError, ClientError, KeyError) as exc:
                logger.debug("Skipping %s: %s", item.get("id"), exc)
        return result

    def cleanup(self) -> list[str]:
        """Delete every proxy this tool created (name prefix match)."""
        deleted = []
        for item in self._get_apis():
            if item.get("name", "").startswith(NAME_PREFIX):
                self.client.delete_rest_api(restApiId=item["id"])
                deleted.append(item["id"])
                logger.info("Deleted %s (%s)", item["id"], item["name"])
        return deleted

    # ------------------------------------------------------------------ helpers
    def _get_apis(self) -> list[dict]:
        return self.client.get_rest_apis().get("items", [])

    def _create_deployment(self, api_id: str) -> str:
        response = self.client.create_deployment(
            restApiId=api_id,
            stageName=STAGE_NAME,
            stageDescription="UltimateSpray Prod",
            description="UltimateSpray Production Deployment",
        )
        return response["id"]

    def get_resource(self, api_id: str) -> str | None:
        for item in self.client.get_resources(restApiId=api_id)["items"]:
            if item["path"] == "/{proxy+}":
                return item["id"]
        return None

    def get_integration(self, api_id: str) -> str:
        resource_id = self.get_resource(api_id)
        response = self.client.get_integration(
            restApiId=api_id, resourceId=resource_id, httpMethod="ANY"
        )
        return response["uri"]
