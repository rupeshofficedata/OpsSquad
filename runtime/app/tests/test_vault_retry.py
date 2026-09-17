"""Dev-mode Vault (k8s/06-vault.yaml) is in-memory — any Vault pod restart
wipes its auth config, and k8s/07-vault-init-job.yaml self-heals it via a
CronJob on a 2-minute cycle. A runtime pod that boots inside that window
must ride out transient 403s from /v1/auth/kubernetes/login rather than
crash — that's the one behavior worth pinning down here."""

import asyncio

import httpx
import pytest

from app import vault


def _install(monkeypatch, transport, sa_jwt_path):
    monkeypatch.setattr(vault, "SA_TOKEN_PATH", sa_jwt_path)
    monkeypatch.setattr(vault.settings, "vault_addr", "http://vault:8200")
    monkeypatch.setattr(vault.settings, "vault_role", "opssquad-runtime")
    monkeypatch.setattr(vault, "_client_kwargs", {"transport": transport})
    monkeypatch.setattr(vault, "LOGIN_RETRY_SECONDS", 0)


def _secret_response(request: httpx.Request, path: str, data: dict) -> httpx.Response:
    if request.url.path == path:
        return httpx.Response(200, json={"data": {"data": data}})
    raise AssertionError(f"unexpected request: {request.url.path}")


def test_retries_login_until_vault_init_catches_up(tmp_path, monkeypatch):
    sa_jwt_path = tmp_path / "token"
    sa_jwt_path.write_text("fake-sa-jwt")

    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/auth/kubernetes/login":
            attempts["n"] += 1
            if attempts["n"] < 3:
                return httpx.Response(403, json={"errors": ["permission denied"]})
            return httpx.Response(200, json={"auth": {"client_token": "t"}})
        if request.url.path == "/v1/secret/data/opssquad/jwt":
            return _secret_response(request, request.url.path, {"secret": "s"})
        if request.url.path == "/v1/secret/data/opssquad/llm":
            return _secret_response(request, request.url.path, {})
        if request.url.path == "/v1/secret/data/opssquad/webhooks":
            return _secret_response(request, request.url.path, {})
        if request.url.path == "/v1/secret/data/opssquad/integrations":
            return _secret_response(request, request.url.path, {})
        raise AssertionError(f"unexpected request: {request.url.path}")

    _install(monkeypatch, httpx.MockTransport(handler), sa_jwt_path)

    asyncio.run(vault.load_secrets_from_vault())

    assert attempts["n"] == 3
    assert vault.settings.jwt_secret == "s"


def test_gives_up_after_max_attempts(tmp_path, monkeypatch):
    sa_jwt_path = tmp_path / "token"
    sa_jwt_path.write_text("fake-sa-jwt")

    def always_403(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errors": ["permission denied"]})

    _install(monkeypatch, httpx.MockTransport(always_403), sa_jwt_path)
    monkeypatch.setattr(vault, "MAX_LOGIN_ATTEMPTS", 3)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(vault.load_secrets_from_vault())
