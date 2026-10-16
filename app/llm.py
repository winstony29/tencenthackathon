"""Independent OpenAI-compatible JSON audit client. Credentials live in config."""

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

from app import config

AUDIT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["checks"],
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["field", "verdict", "expected", "note"],
                "properties": {
                    "field": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["match", "mismatch", "uncertain"]},
                    "expected": {"type": "string"},
                    "note": {"type": "string"},
                },
            },
        }
    },
}

_client: httpx.AsyncClient | None = None


def _new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(config.VALIDATOR_TIMEOUT_SECONDS, connect=3.0, pool=1.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=60),
    )


async def open_connection() -> None:
    global _client
    if _client is None or _client.is_closed:
        _client = _new_client()


async def close_connection() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class Unavailable(RuntimeError):
    pass


def _decode(text: str) -> dict:
    """Reject oversized, duplicate-key or non-integer numeric model output."""
    if len(text.encode("utf-8")) > config.EVIDENCE_MAX_ITEM_BYTES:
        raise ValueError("Validator output exceeds the evidence bound")

    def unique(pairs: list[tuple]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate validator JSON key")
            result[key] = value
        return result

    def invalid_number(value: str) -> None:
        raise ValueError("Validator financial numbers must be strings")

    result = json.loads(
        text, object_pairs_hook=unique, parse_float=invalid_number, parse_constant=invalid_number
    )
    if not isinstance(result, dict):
        raise ValueError("Validator output must be an object")
    return result


async def audit(system: str, payload: dict) -> dict:
    if config.VALIDATOR_PROVIDER == "codex":
        return await asyncio.to_thread(_codex, system, payload)
    model = config.VALIDATOR_MODEL
    if (
        not config.LLM_BASE_URL
        or not config.LLM_API_KEY
        or not model
        or "jev" in model.lower()
        or model == config.REWRITER_MODEL
    ):
        raise Unavailable("Configure an independent validator model.")
    if config.VALIDATOR_PROVIDER not in {"openai", "compatible-api"}:
        raise Unavailable("Unknown validator provider.")
    body = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            },
        ],
    }
    if config.VALIDATOR_PROVIDER == "openai":
        if config.LLM_BASE_URL.rstrip("/") != "https://api.openai.com/v1":
            raise Unavailable("OpenAI validator requires the official HTTPS endpoint.")
        body.update(
            store=False,
            max_completion_tokens=4096,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "banking_audit",
                    "strict": True,
                    "schema": payload.get("response_schema", AUDIT_SCHEMA),
                },
            },
        )

    async def send(client: httpx.AsyncClient) -> dict:
        response = await client.post(
            config.LLM_BASE_URL.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
            json=body,
        )
        response.raise_for_status()
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
            raise Unavailable("Validator did not complete its audit.")
        result = _decode(choice["message"]["content"])
        if not isinstance(result, dict):
            raise Unavailable("Invalid validator response.")
        return result

    if _client is not None:
        return await send(_client)
    async with _new_client() as client:
        return await send(client)


def _codex(system: str, payload: dict) -> dict:
    """Local signed-in CLI adapter: no shell, MCP, web or project configuration."""
    executable = shutil.which("codex")
    if not executable:
        raise Unavailable("Local Codex is not installed or not on PATH.")
    with tempfile.TemporaryDirectory(prefix="jevolve-audit-") as directory:
        root = Path(directory)
        (root / "schema.json").write_text(
            json.dumps(payload.get("response_schema", AUDIT_SCHEMA)), encoding="utf-8"
        )
        args = [
            executable,
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--cd",
            directory,
            "--output-schema",
            str(root / "schema.json"),
            "--output-last-message",
            str(root / "answer.json"),
            "--color",
            "never",
            "-c",
            'approval_policy="never"',
            "-c",
            'web_search="disabled"',
            "-c",
            "features.shell_tool=false",
            "-c",
            "features.unified_exec=false",
            "-c",
            "features.apps=false",
            "-c",
            "features.multi_agent=false",
            # Every other tool Codex 0.143 enables by default: the audit only reads stdin.
            *(
                arg
                for feature in (
                    "browser_use",
                    "browser_use_external",
                    "browser_use_full_cdp_access",
                    "computer_use",
                    "image_generation",
                    "plugins",
                    "hooks",
                )
                for arg in ("-c", f"features.{feature}=false")
            ),
            "-c",
            'model_reasoning_effort="low"',
        ]
        if config.VALIDATOR_CODEX_MODEL:
            args.extend(["--model", config.VALIDATOR_CODEX_MODEL])
        args.append("-")
        allowed = {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "PATHEXT",
            "USERPROFILE",
            "HOME",
            "APPDATA",
            "LOCALAPPDATA",
            "TEMP",
            "TMP",
            "CODEX_HOME",
            "LANG",
        }
        env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        try:
            result = subprocess.run(
                args,
                input=system + "\n\nAUDIT DATA:\n" + json.dumps(payload),
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=config.VALIDATOR_TIMEOUT_SECONDS - 2,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise Unavailable("Local Codex is unavailable or timed out.") from error
        if result.returncode or not (root / "answer.json").exists():
            raise Unavailable("Local Codex could not complete the audit. Check CLI login.")
        answer = root / "answer.json"
        if answer.stat().st_size > config.EVIDENCE_MAX_ITEM_BYTES:
            raise ValueError("Validator output exceeds the evidence bound")
        return _decode(answer.read_text(encoding="utf-8"))
