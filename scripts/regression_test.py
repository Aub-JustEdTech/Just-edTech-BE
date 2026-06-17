#!/usr/bin/env python3
"""
Just-EdTech Regression Test Runner
====================================
Drives the live API end-to-end over HTTP through the core flows:
  1. Authentication  (register, OTP from Redis, verify, setup-tenant, login, me, refresh)
  2. Documents       (upload PDF / MD / TXT / DOCX, poll ingestion, list, jobs, chunks)
  3. Chatbot setup   (list or create a chatbot for the test tenant)
  4. Conversations   (create, send follow-up, list, get, messages)
  5. Report download (PDF report for a conversation)
  6. Cleanup         (delete conversation, documents, logout)

Prerequisites
--------------
- Full docker-compose stack running: API, Postgres, Redis, Qdrant, Celery worker
  docker-compose up -d
- Valid OPENAI_API_KEY and S3 credentials in .env (required for real RAG)

Usage
------
  # Use values from .env automatically
  poetry run python scripts/regression_test.py

  # Override server URL
  BASE_URL=http://localhost:8000 poetry run python scripts/regression_test.py

  # Skip fresh signup and use an existing tenant (skips auth phase, saves time)
  EXISTING_EMAIL=admin@example.com EXISTING_PASSWORD=Pass123! \\
  EXISTING_API_KEY=<key> poetry run python scripts/regression_test.py

  # Skip cleanup of created resources
  poetry run python scripts/regression_test.py --no-cleanup

  # Increase ingestion timeout (default 120 s)
  INGEST_TIMEOUT_S=300 poetry run python scripts/regression_test.py

Environment Variables
----------------------
  BASE_URL              API base URL          (default: http://localhost:8000)
  API_V1_STR            API prefix            (default: /api/v1)
  REDIS_HOST            Redis host            (default: localhost)
  REDIS_PORT            Redis port            (default: 6379)
  REDIS_PASSWORD        Redis password        (default: empty)
  REDIS_DB              Redis DB number       (default: 0)
  EXISTING_EMAIL        Skip signup, use existing user email
  EXISTING_PASSWORD     Password for existing user
  EXISTING_API_KEY      Tenant API key for existing user
  INGEST_TIMEOUT_S      Max seconds to wait for document ingestion (default: 120)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import json
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import redis as redis_lib

# ---------------------------------------------------------------------------
# Helpers to load .env without python-dotenv dependency
# ---------------------------------------------------------------------------

_ENV_FILE = Path(__file__).parent.parent / ".env"


def _load_dotenv(path: Path) -> dict[str, str]:
    """Very small .env parser (no dependency on python-dotenv)."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        result[key] = value
    return result


_dotenv = _load_dotenv(_ENV_FILE)


def _env(key: str, default: str = "") -> str:
    """Read from process env, then .env file, then default."""
    return os.environ.get(key, _dotenv.get(key, default))


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = _env("BASE_URL", "http://localhost:8000").rstrip("/")
API_V1 = _env("API_V1_STR", "/api/v1")

REDIS_HOST = _env("REDIS_HOST", "localhost")
REDIS_PORT = int(_env("REDIS_PORT", "6379"))
REDIS_PASSWORD = _env("REDIS_PASSWORD", "") or None
REDIS_DB = int(_env("REDIS_DB", "0"))

EXISTING_EMAIL = _env("EXISTING_EMAIL", "")
EXISTING_PASSWORD = _env("EXISTING_PASSWORD", "")
EXISTING_API_KEY = _env("EXISTING_API_KEY", "")

INGEST_TIMEOUT_S = int(_env("INGEST_TIMEOUT_S", "120"))
INGEST_POLL_INTERVAL_S = 5

FIXTURES_DIR = Path(__file__).parent / "regression_fixtures"
OUTPUT_DIR = Path(__file__).parent.parent / "temp_uploads"

# ---------------------------------------------------------------------------
# ANSI colour helpers
# ---------------------------------------------------------------------------

_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _green(s: str) -> str:
    return f"{_GREEN}{s}{_RESET}"


def _red(s: str) -> str:
    return f"{_RED}{s}{_RESET}"


def _yellow(s: str) -> str:
    return f"{_YELLOW}{s}{_RESET}"


def _cyan(s: str) -> str:
    return f"{_CYAN}{s}{_RESET}"


def _bold(s: str) -> str:
    return f"{_BOLD}{s}{_RESET}"


# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------

@dataclass
class _Result:
    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str = ""
    elapsed_ms: float = 0.0


_results: list[_Result] = []


def _record(name: str, fn, *args, **kwargs) -> Any:
    """
    Run fn(*args, **kwargs), record PASS/FAIL, and return the value.
    If fn raises, records FAIL and re-raises so dependents can be skipped.
    """
    t0 = time.monotonic()
    try:
        value = fn(*args, **kwargs)
        elapsed = (time.monotonic() - t0) * 1000
        _results.append(_Result(name=name, status="PASS", elapsed_ms=elapsed))
        print(f"  {_green('✓ PASS')} {name} ({elapsed:.0f} ms)")
        return value
    except AssertionError as exc:
        elapsed = (time.monotonic() - t0) * 1000
        detail = str(exc)
        _results.append(_Result(name=name, status="FAIL", detail=detail, elapsed_ms=elapsed))
        print(f"  {_red('✗ FAIL')} {name} ({elapsed:.0f} ms) — {detail}")
        raise
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        detail = f"{type(exc).__name__}: {exc}"
        _results.append(_Result(name=name, status="FAIL", detail=detail, elapsed_ms=elapsed))
        print(f"  {_red('✗ FAIL')} {name} ({elapsed:.0f} ms) — {detail}")
        raise


def _skip(name: str, reason: str = "") -> None:
    _results.append(_Result(name=name, status="SKIP", detail=reason))
    print(f"  {_yellow('⊘ SKIP')} {name}{' — ' + reason if reason else ''}")


def _section(title: str) -> None:
    width = 70
    print(f"\n{_bold(_cyan('─' * width))}")
    print(f"{_bold(_cyan(f'  {title}'))}")
    print(f"{_bold(_cyan('─' * width))}")


def _print_summary() -> int:
    """Print final table. Returns exit code (0 = all pass, 1 = any fail)."""
    passed = sum(1 for r in _results if r.status == "PASS")
    failed = sum(1 for r in _results if r.status == "FAIL")
    skipped = sum(1 for r in _results if r.status == "SKIP")
    total = len(_results)

    print(f"\n{'=' * 70}")
    print(_bold("  REGRESSION TEST SUMMARY"))
    print(f"{'=' * 70}")
    print(f"  {'Step':<52} {'Status':>6}  {'ms':>6}")
    print(f"  {'-' * 52} {'-' * 6}  {'-' * 6}")
    for r in _results:
        colour = _green if r.status == "PASS" else (_red if r.status == "FAIL" else _yellow)
        ms = f"{r.elapsed_ms:.0f}" if r.elapsed_ms else "—"
        name_display = r.name[:51]
        print(f"  {name_display:<52} {colour(r.status):>6}  {ms:>6}")

    print(f"{'=' * 70}")
    colour_summary = _green if failed == 0 else _red
    print(
        colour_summary(
            f"  TOTAL {total} | PASS {passed} | FAIL {failed} | SKIP {skipped}"
        )
    )
    print(f"{'=' * 70}\n")
    return 1 if failed > 0 else 0


# ---------------------------------------------------------------------------
# Shared HTTP helpers
# ---------------------------------------------------------------------------

def _url(path: str) -> str:
    return f"{BASE_URL}{API_V1}{path}"


def _data(resp: httpx.Response) -> Any:
    """Extract `.data` from the standard {success, data, error} wrapper."""
    return resp.json().get("data", resp.json())


def _assert_status(resp: httpx.Response, expected: int, label: str = "") -> None:
    if resp.status_code != expected:
        body = resp.text[:300]
        raise AssertionError(
            f"{label}Expected HTTP {expected}, got {resp.status_code}. Body: {body}"
        )


# ---------------------------------------------------------------------------
# Redis OTP helper
# ---------------------------------------------------------------------------

def _get_otp_from_redis(email: str) -> str:
    """Read the signup OTP from Redis key 'email_verify_code:{email}'."""
    r = redis_lib.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        db=REDIS_DB,
        decode_responses=True,
        socket_connect_timeout=5,
    )
    key = f"email_verify_code:{email}"
    code = r.get(key)
    if not code:
        raise AssertionError(
            f"No OTP found in Redis for key '{key}'. "
            "Ensure the API server and Redis are running and the register call succeeded."
        )
    return str(code)


# ---------------------------------------------------------------------------
# State shared across phases
# ---------------------------------------------------------------------------

@dataclass
class _State:
    # Auth
    email: str = ""
    password: str = ""
    access_token: str = ""
    refresh_token: str = ""
    api_key: str = ""
    tenant_id: int = 0
    user_id: int = 0
    # Documents
    uploaded_doc_ids: list[int] = field(default_factory=list)
    completed_doc_id: int = 0   # first document that finished processing
    # Chatbot
    chatbot_id: int = 0
    # Conversations
    conversation_id: int = 0


_state = _State()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_state.access_token}"}


def _auth_and_api_key_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_state.access_token}",
        "X-API-Key": _state.api_key,
    }


# ===========================================================================
# PHASE 1 — Authentication
# ===========================================================================

def _phase_auth(client: httpx.Client, skip_signup: bool) -> None:
    _section("PHASE 1 — Authentication")

    if skip_signup:
        # Reuse existing credentials from env
        _state.email = EXISTING_EMAIL
        _state.password = EXISTING_PASSWORD
        _state.api_key = EXISTING_API_KEY
        _skip("auth:register", "using existing credentials (EXISTING_EMAIL set)")
        _skip("auth:read-otp", "using existing credentials")
        _skip("auth:verify-email", "using existing credentials")
        _skip("auth:setup-tenant", "using existing credentials")
    else:
        timestamp = int(time.time())
        _state.email = f"regression.{timestamp}@example.com"
        _state.password = "Regression123!"

        # 1. Register (OTP flow)
        def _register() -> None:
            resp = client.post(
                _url("/auth/register"),
                json={
                    "email": _state.email,
                    "name": "Regression Tester",
                    "password": _state.password,
                },
            )
            _assert_status(resp, 200, "register: ")
            data = _data(resp)
            assert "message" in data, "register: missing 'message' in response"

        _record("auth:register", _register)

        # 2. Read OTP from Redis
        otp_value: list[str] = []

        def _read_otp() -> None:
            code = _get_otp_from_redis(_state.email)
            assert code and len(code) == 6 and code.isdigit(), (
                f"Invalid OTP format: '{code}'"
            )
            otp_value.append(code)

        _record("auth:read-otp-from-redis", _read_otp)

        # 3. Verify email
        def _verify_email() -> None:
            resp = client.post(
                _url("/auth/verify-email"),
                json={"email": _state.email, "code": otp_value[0]},
            )
            _assert_status(resp, 200, "verify-email: ")

        _record("auth:verify-email", _verify_email)

        # 4. Setup tenant (creates tenant + admin user + API key)
        def _setup_tenant() -> None:
            resp = client.post(
                _url("/auth/setup-tenant"),
                json={
                    "email": _state.email,
                    "tenant_name": f"Regression Tenant {int(time.time())}",
                },
            )
            _assert_status(resp, 200, "setup-tenant: ")
            data = _data(resp)
            assert "api_key" in data, f"setup-tenant: missing 'api_key' in response. Got: {data}"
            assert "tenant_id" in data, f"setup-tenant: missing 'tenant_id'. Got: {data}"
            assert "user_id" in data, f"setup-tenant: missing 'user_id'. Got: {data}"
            _state.api_key = data["api_key"]
            _state.tenant_id = data["tenant_id"]
            _state.user_id = data["user_id"]

        _record("auth:setup-tenant", _setup_tenant)

    # 5. Login (always)
    def _login() -> None:
        resp = client.post(
            _url("/auth/login"),
            json={"username": _state.email, "password": _state.password},
        )
        _assert_status(resp, 200, "login: ")
        data = _data(resp)
        assert "access_token" in data, f"login: missing access_token. Got: {data}"
        assert "refresh_token" in data, f"login: missing refresh_token. Got: {data}"
        _state.access_token = data["access_token"]
        _state.refresh_token = data["refresh_token"]

    _record("auth:login", _login)

    # 6. Get current user
    def _me() -> None:
        resp = client.get(_url("/auth/me"), headers=_auth_headers())
        _assert_status(resp, 200, "me: ")
        data = _data(resp)
        assert "email" in data, f"me: missing 'email'. Got: {data}"
        assert data["email"].lower() == _state.email.lower(), (
            f"me: email mismatch. Expected {_state.email}, got {data['email']}"
        )
        # Capture tenant_id if not set (existing user flow)
        if not _state.tenant_id:
            _state.tenant_id = data.get("tenant_id", 0)
        if not _state.user_id:
            _state.user_id = data.get("id", 0)

    _record("auth:me", _me)

    # 7. Refresh token
    def _refresh() -> None:
        resp = client.post(
            _url("/auth/refresh"),
            json={"refresh_token": _state.refresh_token},
        )
        _assert_status(resp, 200, "refresh: ")
        data = _data(resp)
        assert "access_token" in data, f"refresh: missing access_token. Got: {data}"
        # Rotate to new tokens
        _state.access_token = data["access_token"]
        _state.refresh_token = data.get("refresh_token", _state.refresh_token)

    _record("auth:refresh-token", _refresh)

    # 8. Negative: wrong password -> 401
    def _bad_login() -> None:
        resp = client.post(
            _url("/auth/login"),
            json={"username": _state.email, "password": "WrongPassword999!"},
        )
        _assert_status(resp, 401, "bad-login: ")

    _record("auth:login-wrong-password-expects-401", _bad_login)


# ===========================================================================
# PHASE 2 — Documents
# ===========================================================================

def _poll_document_status(client: httpx.Client, doc_id: int) -> str:
    """Poll GET /documents/{id} until status is not pending/processing, or timeout."""
    deadline = time.monotonic() + INGEST_TIMEOUT_S
    while time.monotonic() < deadline:
        resp = client.get(_url(f"/documents/{doc_id}"), headers=_auth_headers())
        if resp.status_code != 200:
            return "error"
        status_val = _data(resp).get("processing_status", "unknown")
        if status_val in ("completed", "failed"):
            return status_val
        print(
            f"    ⏳  doc {doc_id} status={status_val}, "
            f"waiting {INGEST_POLL_INTERVAL_S}s …"
        )
        time.sleep(INGEST_POLL_INTERVAL_S)
    return "timeout"


def _phase_documents(client: httpx.Client) -> None:
    _section("PHASE 2 — Documents")

    fixtures = [
        ("sample.pdf", "application/pdf"),
        ("sample.md", "text/markdown"),
        ("sample.txt", "text/plain"),
        ("sample.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ]

    for filename, mime_type in fixtures:
        fixture_path = FIXTURES_DIR / filename
        ext = Path(filename).suffix

        # Upload
        def _upload(fp=fixture_path, fn=filename, mt=mime_type, ex=ext) -> int:
            with open(fp, "rb") as fh:
                resp = client.post(
                    _url("/documents/upload"),
                    files={"file": (fn, fh, mt)},
                    headers=_auth_headers(),
                )
            _assert_status(resp, 201, f"upload {fn}: ")
            data = _data(resp)
            assert "id" in data, f"upload {fn}: missing 'id' in response. Got: {data}"
            assert data.get("document_type") == ex, (
                f"upload {fn}: wrong document_type. Expected {ex}, got {data.get('document_type')}"
            )
            doc_id = data["id"]
            _state.uploaded_doc_ids.append(doc_id)
            return doc_id

        try:
            doc_id = _record(f"documents:upload:{filename}", _upload)
        except Exception:
            continue  # Skip remaining checks for this file type

        # Poll processing status
        ingest_completed = False

        def _wait_ingest(did=doc_id, fn=filename) -> str:
            final_status = _poll_document_status(client, did)
            assert final_status != "timeout", (
                f"Ingestion of {fn} timed out after {INGEST_TIMEOUT_S}s. "
                "Is the Celery worker running?"
            )
            assert final_status == "completed", (
                f"Ingestion of {fn} finished with status '{final_status}' (expected 'completed'). "
                "Check Celery logs for errors."
            )
            return final_status

        try:
            _record(f"documents:ingestion-completed:{filename}", _wait_ingest)
            ingest_completed = True
            # Track first completed doc for later RAG test
            if not _state.completed_doc_id:
                _state.completed_doc_id = doc_id
        except Exception:
            pass  # Continue to processing-jobs check even after ingestion failure

        # Processing jobs — always check (jobs exist even for failed ingestion)
        def _processing_jobs(did=doc_id, fn=filename) -> None:
            resp = client.get(
                _url(f"/documents/{did}/processing-jobs"),
                headers=_auth_headers(),
            )
            _assert_status(resp, 200, f"processing-jobs {fn}: ")
            jobs = _data(resp)
            assert isinstance(jobs, list), f"processing-jobs {fn}: expected list, got {type(jobs)}"
            assert len(jobs) > 0, f"processing-jobs {fn}: empty job list"

        try:
            _record(f"documents:processing-jobs:{filename}", _processing_jobs)
        except Exception:
            pass

        # Chunks — only valid once ingestion completed successfully
        if ingest_completed:
            def _chunks(did=doc_id, fn=filename) -> None:
                resp = client.get(
                    _url(f"/documents/{did}/chunks"),
                    headers=_auth_headers(),
                )
                _assert_status(resp, 200, f"chunks {fn}: ")
                data = _data(resp)
                assert "total_chunks" in data, f"chunks {fn}: missing 'total_chunks'"
                assert data["total_chunks"] > 0, f"chunks {fn}: no chunks found after ingestion"

            try:
                _record(f"documents:chunks:{filename}", _chunks)
            except Exception:
                pass
        else:
            _skip(
                f"documents:chunks:{filename}",
                "ingestion did not complete successfully",
            )

    # List documents and verify all uploads appear (include_failed=true to catch all statuses)
    def _list_docs() -> None:
        resp = client.get(
            _url("/documents/"),
            params={"include_failed": "true", "limit": 500},
            headers=_auth_headers(),
        )
        _assert_status(resp, 200, "list-docs: ")
        docs = _data(resp)
        assert isinstance(docs, list), f"list-docs: expected list, got {type(docs)}"
        returned_ids = {d["id"] for d in docs}
        missing = set(_state.uploaded_doc_ids) - returned_ids
        assert not missing, f"list-docs: uploaded doc IDs not found in list: {missing}"

    _record("documents:list-all-appear", _list_docs)

    # Get single document detail
    def _get_doc() -> None:
        if not _state.uploaded_doc_ids:
            raise AssertionError("No uploaded docs to test get-detail against")
        doc_id = _state.uploaded_doc_ids[0]
        resp = client.get(_url(f"/documents/{doc_id}"), headers=_auth_headers())
        _assert_status(resp, 200, f"get-doc {doc_id}: ")
        data = _data(resp)
        assert data["id"] == doc_id, f"get-doc: ID mismatch"

    _record("documents:get-detail", _get_doc)

    # Negative: unsupported file type
    def _bad_extension() -> None:
        fake_content = b"definitely not a supported file"
        resp = client.post(
            _url("/documents/upload"),
            files={"file": ("malware.exe", fake_content, "application/octet-stream")},
            headers=_auth_headers(),
        )
        _assert_status(resp, 400, "bad-extension: ")

    _record("documents:upload-unsupported-extension-expects-400", _bad_extension)

    # Negative: access document from another tenant (404 / 403)
    def _wrong_doc_id() -> None:
        resp = client.get(_url("/documents/999999999"), headers=_auth_headers())
        assert resp.status_code in (404, 403), (
            f"wrong-doc-id: expected 404/403, got {resp.status_code}"
        )

    _record("documents:get-nonexistent-expects-404", _wrong_doc_id)


# ===========================================================================
# PHASE 3 — Chatbot setup
# ===========================================================================

def _phase_chatbot(client: httpx.Client) -> None:
    _section("PHASE 3 — Chatbot Setup")

    def _ensure_chatbot() -> None:
        # List existing chatbots for this tenant
        resp = client.get(
            _url("/chatbots"),
            params={"tenant_id": _state.tenant_id, "per_page": 5},
            headers=_auth_headers(),
        )
        _assert_status(resp, 200, "list-chatbots: ")
        resp_data = _data(resp)

        # Response is paginated: {"items": [...], "total": N, ...}
        items = resp_data.get("items", resp_data) if isinstance(resp_data, dict) else resp_data
        if items and len(items) > 0:
            _state.chatbot_id = items[0]["id"]
            print(f"    ↳ Reusing existing chatbot id={_state.chatbot_id}")
            return

        # No chatbot yet — create one
        resp = client.post(
            _url("/chatbots"),
            json={
                "name": f"regression-bot-{int(time.time())}",
                "title": "Regression Test Bot",
                "welcome_message": "Hello! I am the regression test bot.",
                "tenant_id": _state.tenant_id,
            },
            headers=_auth_headers(),
        )
        _assert_status(resp, 201, "create-chatbot: ")
        data = _data(resp)
        assert "id" in data, f"create-chatbot: missing 'id'. Got: {data}"
        _state.chatbot_id = data["id"]
        print(f"    ↳ Created chatbot id={_state.chatbot_id}")

    _record("chatbot:ensure-exists", _ensure_chatbot)


# ===========================================================================
# PHASE 4 — Conversations
# ===========================================================================

def _phase_conversations(client: httpx.Client) -> None:
    _section("PHASE 4 — Conversations")

    if not _state.chatbot_id:
        _skip("conversations:create", "no chatbot_id available")
        _skip("conversations:send-follow-up", "no chatbot_id available")
        _skip("conversations:list", "no chatbot_id available")
        _skip("conversations:get-detail", "no chatbot_id available")
        _skip("conversations:get-messages", "no chatbot_id available")
        _skip("conversations:no-api-key-expects-401", "no chatbot_id available")
        return

    # Create a new conversation (first message)
    def _create_conv() -> None:
        question = "What is the main topic covered in the uploaded documents?"
        resp = client.post(
            _url("/conversations"),
            json={"chatbot_id": _state.chatbot_id, "content": question},
            headers=_auth_and_api_key_headers(),
        )
        _assert_status(resp, 201, "create-conv: ")
        data = _data(resp)
        assert "user_message" in data, f"create-conv: missing 'user_message'. Got: {data}"
        assert "bot_message" in data, f"create-conv: missing 'bot_message'. Got: {data}"

        user_msg = data["user_message"]
        bot_msg = data["bot_message"]
        assert user_msg.get("conversation_id"), "create-conv: user_message has no conversation_id"
        assert bot_msg.get("content"), "create-conv: bot_message has empty content"

        _state.conversation_id = user_msg["conversation_id"]
        print(f"    ↳ Conversation id={_state.conversation_id}")

    _record("conversations:create-new", _create_conv)

    # Send a follow-up message to the same conversation
    def _send_followup() -> None:
        if not _state.conversation_id:
            raise AssertionError("No conversation_id to send follow-up to")
        resp = client.post(
            _url(f"/conversations/{_state.conversation_id}/messages"),
            json={
                "chatbot_id": _state.chatbot_id,
                "content": "Can you summarize that in one sentence?",
            },
            headers=_auth_and_api_key_headers(),
        )
        _assert_status(resp, 200, "send-followup: ")
        data = _data(resp)
        assert "bot_message" in data, f"send-followup: missing 'bot_message'. Got: {data}"
        assert data["bot_message"].get("content"), "send-followup: bot_message has empty content"

    _record("conversations:send-follow-up-message", _send_followup)

    # List conversations (trailing slash required for GET /conversations/)
    def _list_convs() -> None:
        resp = client.get(
            _url("/conversations/"),
            params={"page": 1, "per_page": 20},
            headers=_auth_and_api_key_headers(),
        )
        _assert_status(resp, 200, "list-convs: ")
        data = _data(resp)
        items = data.get("items", [])
        assert isinstance(items, list), f"list-convs: expected items list. Got: {data}"
        conv_ids = [c["id"] for c in items]
        assert _state.conversation_id in conv_ids, (
            f"list-convs: created conversation {_state.conversation_id} not in list. "
            f"Found: {conv_ids}"
        )

    _record("conversations:list-contains-created", _list_convs)

    # Get conversation detail
    def _get_conv() -> None:
        resp = client.get(
            _url(f"/conversations/{_state.conversation_id}"),
            headers=_auth_and_api_key_headers(),
        )
        _assert_status(resp, 200, "get-conv: ")
        data = _data(resp)
        assert data["id"] == _state.conversation_id, "get-conv: ID mismatch"

    _record("conversations:get-detail", _get_conv)

    # Get messages for the conversation
    def _get_msgs() -> None:
        resp = client.get(
            _url(f"/conversations/{_state.conversation_id}/messages"),
            params={"page": 1, "per_page": 50},
            headers=_auth_and_api_key_headers(),
        )
        _assert_status(resp, 200, "get-messages: ")
        data = _data(resp)
        items = data.get("items", [])
        assert len(items) >= 2, (
            f"get-messages: expected at least 2 messages (user + bot), got {len(items)}"
        )
        roles = {m["role"] for m in items}
        assert "user" in roles, "get-messages: no user message found"
        assert "assistant" in roles, "get-messages: no assistant message found"

    _record("conversations:get-messages", _get_msgs)

    # Negative: no X-API-Key header -> 401
    def _no_api_key() -> None:
        resp = client.get(
            _url(f"/conversations/{_state.conversation_id}"),
            headers=_auth_headers(),  # only JWT, no X-API-Key
        )
        _assert_status(resp, 401, "no-api-key: ")

    _record("conversations:no-api-key-expects-401", _no_api_key)

    # Negative: access another tenant's conversation (nonexistent) -> 404
    def _missing_conv() -> None:
        resp = client.get(
            _url("/conversations/999999999"),
            headers=_auth_and_api_key_headers(),
        )
        assert resp.status_code in (404, 403), (
            f"missing-conv: expected 404/403, got {resp.status_code}"
        )

    _record("conversations:get-nonexistent-expects-404", _missing_conv)


# ===========================================================================
# PHASE 5 — Conversation Report
# ===========================================================================

def _phase_report(client: httpx.Client) -> None:
    _section("PHASE 5 — Conversation Report")

    if not _state.conversation_id:
        _skip("report:download", "no conversation_id available")
        return

    def _download_report() -> None:
        resp = client.get(
            _url(f"/conversations/{_state.conversation_id}/report"),
            headers=_auth_and_api_key_headers(),
        )
        _assert_status(resp, 200, "report: ")
        ct = resp.headers.get("content-type", "")
        assert "application/pdf" in ct, (
            f"report: expected Content-Type application/pdf, got '{ct}'"
        )
        pdf_bytes = resp.content
        assert len(pdf_bytes) > 0, "report: empty response body"
        assert pdf_bytes[:4] == b"%PDF", (
            f"report: body does not start with '%PDF' (got {pdf_bytes[:8]!r})"
        )

        # Save to output directory for inspection
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / f"regression_report_{_state.conversation_id}.pdf"
        output_path.write_bytes(pdf_bytes)
        print(f"    ↳ PDF saved to {output_path} ({len(pdf_bytes)} bytes)")

    _record("report:download-pdf-and-validate", _download_report)

    # Negative: unauthenticated report access -> 401
    def _unauth_report() -> None:
        resp = client.get(_url(f"/conversations/{_state.conversation_id}/report"))
        assert resp.status_code in (401, 403), (
            f"unauth-report: expected 401/403, got {resp.status_code}"
        )

    _record("report:unauthenticated-expects-401", _unauth_report)


# ===========================================================================
# PHASE 6 — Cleanup
# ===========================================================================

def _phase_cleanup(client: httpx.Client) -> None:
    _section("PHASE 6 — Cleanup")

    # Delete conversation
    if _state.conversation_id:
        def _del_conv() -> None:
            resp = client.delete(
                _url(f"/conversations/{_state.conversation_id}"),
                headers=_auth_and_api_key_headers(),
            )
            assert resp.status_code in (200, 204), (
                f"del-conv: expected 200/204, got {resp.status_code}. {resp.text[:200]}"
            )

        _record("cleanup:delete-conversation", _del_conv)
    else:
        _skip("cleanup:delete-conversation", "no conversation to delete")

    # Delete uploaded documents
    for doc_id in _state.uploaded_doc_ids:
        def _del_doc(did=doc_id) -> None:
            resp = client.delete(
                _url(f"/documents/{did}"),
                headers=_auth_headers(),
            )
            assert resp.status_code in (200, 204), (
                f"del-doc {did}: expected 200/204, got {resp.status_code}. {resp.text[:200]}"
            )

        _record(f"cleanup:delete-document-{doc_id}", _del_doc)

    # Logout
    def _logout() -> None:
        resp = client.post(
            _url("/auth/logout"),
            json={"refresh_token": _state.refresh_token},
            headers=_auth_headers(),
        )
        assert resp.status_code in (200, 204), (
            f"logout: expected 200/204, got {resp.status_code}. {resp.text[:200]}"
        )

    _record("cleanup:logout", _logout)


# ===========================================================================
# Main entry point
# ===========================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Just-EdTech regression test runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        default=False,
        help="Skip cleanup phase (leave created resources in place for inspection)",
    )
    args = parser.parse_args()

    skip_signup = bool(EXISTING_EMAIL and EXISTING_PASSWORD)
    if skip_signup and not EXISTING_API_KEY:
        print(
            _yellow(
                "WARNING: EXISTING_EMAIL set but EXISTING_API_KEY is empty. "
                "Conversation phase will likely fail."
            )
        )

    print(_bold(f"\n{'=' * 70}"))
    print(_bold("  Just-EdTech Regression Test Runner"))
    print(_bold(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"))
    print(_bold(f"  Target:  {BASE_URL}{API_V1}"))
    print(_bold(f"  Mode:    {'reuse existing credentials' if skip_signup else 'fresh tenant signup'}"))
    print(_bold(f"{'=' * 70}"))

    with httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        follow_redirects=True,
    ) as client:

        # --- Health check before running any tests ---
        try:
            hresp = client.get(f"{BASE_URL}/health", timeout=5.0)
            if hresp.status_code == 200:
                print(f"\n  {_green('✓')} API health check passed")
            else:
                print(f"\n  {_yellow('!')} API health check returned {hresp.status_code}")
        except Exception as exc:
            print(
                f"\n  {_red('✗')} Cannot reach API at {BASE_URL}: {exc}\n"
                "  Start the stack with: docker-compose up -d\n"
            )
            return 1

        # Run phases; each phase catches its own errors and records them.
        # A failure in one phase does not abort subsequent independent phases.

        _phase_auth(client, skip_signup)

        if not _state.access_token:
            print(
                _red(
                    "\n  Auth phase failed — no access token acquired. "
                    "Aborting remaining phases.\n"
                )
            )
            return _print_summary()

        try:
            _phase_documents(client)
        except Exception as exc:
            print(_yellow(f"  Documents phase aborted early: {exc}"))

        try:
            _phase_chatbot(client)
        except Exception as exc:
            print(_yellow(f"  Chatbot phase aborted early: {exc}"))

        try:
            _phase_conversations(client)
        except Exception as exc:
            print(_yellow(f"  Conversations phase aborted early: {exc}"))

        try:
            _phase_report(client)
        except Exception as exc:
            print(_yellow(f"  Report phase aborted early: {exc}"))

        if not args.no_cleanup:
            try:
                _phase_cleanup(client)
            except Exception as exc:
                print(_yellow(f"  Cleanup phase aborted early: {exc}"))
        else:
            _section("PHASE 6 — Cleanup (skipped via --no-cleanup)")
            print(f"  {_yellow('⊘')} Cleanup skipped. Resources left in place:")
            if _state.conversation_id:
                print(f"      Conversation id={_state.conversation_id}")
            for did in _state.uploaded_doc_ids:
                print(f"      Document id={did}")

    return _print_summary()


if __name__ == "__main__":
    sys.exit(main())
