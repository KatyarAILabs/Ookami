"""Hooks loaded by the managed LiteLLM gateway (see local/runtime.py). They run inside the gateway process.

  custom_auth: ookami.gateway.litellm_hooks.user_api_key_auth
  callbacks:   [ookami.gateway.litellm_hooks.usage_logger]

OOKAMI_STORAGE (set by ookami up) points at the storage directory holding registry.db and the master key.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import Request
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import LitellmUserRoles, ProxyException, UserAPIKeyAuth

from .keys import KeyStore, hash_key

PUBLIC_PATHS = {"/health/liveliness", "/health/readiness", "/health/liveness"}

_store: KeyStore | None = None
_master_hash: str | None = None
_windows: dict[str, deque] = defaultdict(deque)


def store() -> KeyStore:
    global _store, _master_hash
    if _store is None:
        _store = KeyStore(Path(os.environ["OOKAMI_STORAGE"]))
        _master_hash = hash_key(_store.master_key())
    return _store


def _deny(status: int, kind: str, message: str) -> ProxyException:
    return ProxyException(message=message, type=kind, param="api_key", code=status)


def check(api_key: str, now: float | None = None) -> UserAPIKeyAuth:
    """The decision, separate from the request plumbing so it can be tested directly."""
    s = store()
    key = (api_key or "").removeprefix("Bearer ").strip()
    if not key:
        raise _deny(401, "auth_error", "missing API key: send Authorization: Bearer <key>")
    h = hash_key(key)
    if h == _master_hash:
        return UserAPIKeyAuth(api_key=h, token=h, key_alias="master", team_id="admin",
                              user_role=LitellmUserRoles.PROXY_ADMIN)
    rec = s.get(key)
    if rec is None or rec.revoked_at is not None:
        raise _deny(401, "auth_error", "invalid or revoked API key")
    now = now or time.time()
    if rec.rpm:
        w = _windows[h]
        while w and w[0] <= now - 60:
            w.popleft()
        if len(w) >= rec.rpm:
            raise _deny(429, "rate_limit_error", f"rate limit: {rec.rpm} requests per minute for key {rec.name}")
        w.append(now)
    if rec.budget_usd is not None and s.spend_this_month(h) >= rec.budget_usd:
        raise _deny(429, "budget_exceeded", f"monthly budget of ${rec.budget_usd:g} reached for key {rec.name}")
    return UserAPIKeyAuth(api_key=h, token=h, key_alias=rec.name, team_id=rec.team)


async def user_api_key_auth(request: Request, api_key: str) -> UserAPIKeyAuth:
    if request.url.path in PUBLIC_PATHS:
        return UserAPIKeyAuth(user_role=LitellmUserRoles.INTERNAL_USER_VIEW_ONLY)
    return check(api_key)


class UsageLogger(CustomLogger):
    """Records every call per key: tokens, provider cost, latency, success."""

    def _record(self, kwargs, start_time, end_time, ok: bool) -> None:
        slo = kwargs.get("standard_logging_object") or {}
        meta = slo.get("metadata") or {}
        latency = (end_time - start_time).total_seconds() * 1000 if start_time and end_time else 0.0
        store().record(meta.get("user_api_key_hash"), meta.get("user_api_key_team_id"),
                       slo.get("model_group") or kwargs.get("model"), int(slo.get("prompt_tokens") or 0),
                       int(slo.get("completion_tokens") or 0), float(slo.get("response_cost") or 0.0), latency, ok)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._record(kwargs, start_time, end_time, True)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self._record(kwargs, start_time, end_time, False)


usage_logger = UsageLogger()
