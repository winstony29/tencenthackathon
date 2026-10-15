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


class Unavailable(RuntimeError):
    pass


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
    async with httpx.AsyncClient(timeout=config.VALIDATOR_TIMEOUT_SECONDS) as client:
        response = await client.post(
            config.LLM_BASE_URL.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
            json={
                "model": model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
            },
        )
        response.raise_for_status()
        return json.loads(response.json()["choices"][0]["message"]["content"])


def _codex(system: str, payload: dict) -> dict:
    """Local signed-in CLI adapter: no shell, MCP, web or project configuration."""
    executable = shutil.which("codex")
    if not executable:
        raise Unavailable("Local Codex is not installed or not on PATH.")
    schema = {
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
    with tempfile.TemporaryDirectory(prefix="jevolve-audit-") as directory:
        root = Path(directory)
        (root / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
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
        return json.loads((root / "answer.json").read_text(encoding="utf-8"))
