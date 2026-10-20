"""Day 1 gate: does Microsoft Foundry answer?

One prompt, one answer. This is the smallest possible end-to-end check of the
whole Azure plan -- if Foundry cannot be reached, the Grand Prize, the Foundry
prize and the Cloud Native prize all come off the board and the hosting and
provider decisions have to be re-opened. It is worth running before anything
else is built.

Run it::

    python -m touchline.providers.smoke

The script is deliberately loud about *why* it failed, because the most likely
failures are not model failures:

* no endpoint configured          -> the provisioning script has not been run
* no Azure credential              -> ``az login`` has not been run
* 401/403                         -> the managed identity or the local role
                                     assignment is wrong
* 404 / unknown deployment         -> ``FOUNDRY_MODEL`` is not the deployment
                                     *name* (it is not the model name)

There is no API key anywhere in this path. A Foundry project endpoint does not
accept one; authentication is Microsoft Entra ID -- ``az login`` locally, a
managed identity in Azure Container Apps.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import os
import sys
from pathlib import Path
from typing import Any

# The prompt is deliberately boring: one factual sentence, so a mangled answer
# is visibly the model's problem and not a formatting problem.
PROMPT = "In one sentence, what is a football match event log?"

ENV_FILE = Path.home() / ".touchline-secrets.env"


def _load_env_file() -> None:
    """Load ``~/.touchline-secrets.env`` if it exists.

    The file holds configuration only -- ``FOUNDRY_PROJECT_ENDPOINT`` and
    ``FOUNDRY_MODEL`` -- despite the historical name. Nothing secret is read
    from it, because nothing secret exists.
    """
    if not ENV_FILE.exists():
        return
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        # Real environment variables win over the file.
        os.environ.setdefault(key, value)


def _report_config() -> tuple[str | None, str | None]:
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    model = os.environ.get("FOUNDRY_MODEL")
    return endpoint, model


def _credential() -> Any:
    """Build the credential.

    ``DefaultAzureCredential`` is the whole point: it resolves the ``az login``
    credential on a laptop and the Container App's system-assigned managed
    identity in Azure, from the same line of code.
    """
    try:
        from azure.identity.aio import DefaultAzureCredential
    except ImportError:  # pragma: no cover - dependency missing
        try:
            from azure.identity import DefaultAzureCredential  # type: ignore[assignment]
        except ImportError:
            print(
                "azure-identity is not installed. Run:\n"
                "    pip install azure-identity",
                file=sys.stderr,
            )
            raise SystemExit(2) from None
    return DefaultAzureCredential()


def _client(endpoint: str, model: str, credential: Any) -> Any:
    """Construct the Foundry chat client.

    ``agent_framework.foundry`` is a lazy re-export shim; the concrete class
    lives in ``agent_framework_foundry``. Both spellings are tried so the
    script works across the 1.x package versions.
    """
    try:
        from agent_framework.foundry import FoundryChatClient
    except ImportError:
        try:
            from agent_framework_foundry import FoundryChatClient
        except ImportError:
            print(
                "agent-framework-foundry is not installed. Run:\n"
                "    pip install agent-framework-foundry agent-framework-openai",
                file=sys.stderr,
            )
            raise SystemExit(2) from None

    return FoundryChatClient(
        project_endpoint=endpoint,
        model=model,
        credential=credential,
    )


async def _close(credential: object) -> None:
    """Close the credential, releasing its HTTP session.

    The async ``DefaultAzureCredential`` keeps an aiohttp session open for
    the instance metadata probe. Without this the interpreter prints
    "Unclosed client session" on exit, which reads as a failure next to a
    successful answer.
    """
    close = getattr(credential, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def _ask(client: object, prompt: str) -> str:
    """Send one prompt and return the text of the reply."""
    get_response = getattr(client, "get_response", None)
    if get_response is not None:
        response = await get_response(prompt)
    else:
        response = await client.send(prompt)  # type: ignore[attr-defined]

    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    # Fall back to the message list if the response object does not expose
    # .text directly.
    messages = getattr(response, "messages", None) or []
    parts: list[str] = []
    for message in messages:
        content = getattr(message, "text", None)
        if isinstance(content, str):
            parts.append(content)
        elif content is not None:
            parts.append(str(content))
    return " ".join(p for p in parts if p).strip()


def _explain(exc: BaseException, endpoint: str, model: str) -> None:
    """Print the most likely cause of the failure, and what to do about it."""
    text = str(exc)
    lowered = text.lower()

    print("\nThe smoke test failed.", file=sys.stderr)
    print(f"  endpoint : {endpoint}")
    print(f"  model    : {model}")
    print(f"  error    : {type(exc).__name__}: {text}\n", file=sys.stderr)

    denied = "401" in text or "403" in text
    denied = denied or "unauthorized" in lowered or "forbidden" in lowered
    if denied:
        print(
            "Most likely: the credential has no permission on the Foundry account.\n"
            "  - Locally: check that `az account show` is the subscription that owns\n"
            "    the Foundry account, and that you hold a role on it.\n"
            "  - Deployed: the role is 'Foundry User' on the Foundry account. If you\n"
            "    just created the role assignment, the managed-identity back end can\n"
            "    keep serving a cached token for up to ~24h -- wait, then restart the\n"
            "    revision before trying again.",
            file=sys.stderr,
        )
    elif "404" in text or "deployment" in lowered or "model" in lowered:
        print(
            "Most likely: FOUNDRY_MODEL is wrong. It is the *deployment*\n"
            "name, not the model name. Check it with:\n"
            "    az cognitiveservices account deployment list \\\n"
            "        -n touchlinefoundry -g touchline-rg -o table",
            file=sys.stderr,
        )
    elif "credential" in lowered or "login" in lowered:
        print(
            "Most likely: no usable Azure credential. Run:\n"
            "    az login --use-device-code",
            file=sys.stderr,
        )
    else:
        print(
            "Check the Foundry resource is provisioned and healthy:\n"
            "    az cognitiveservices account show \\\n"
            "        -n touchlinefoundry -g touchline-rg\n"
            "    az cognitiveservices account deployment list -n touchlinefoundry \\\n"
            "        -g touchline-rg -o table",
            file=sys.stderr,
        )


async def _run(endpoint: str, model: str) -> int:
    print("→ Microsoft Foundry")
    print(f"  endpoint : {endpoint}")
    print(f"  model    : {model}")
    print("  asking   :", PROMPT)
    print()

    credential = _credential()
    try:
        client = _client(endpoint, model, credential)
        answer = await _ask(client, PROMPT)
    except SystemExit:
        raise
    except Exception as exc:
        _explain(exc, endpoint, model)
        return 1
    finally:
        await _close(credential)

    if not answer:
        print("Foundry returned an empty response.", file=sys.stderr)
        return 1

    print("→ answer")
    print(f"  {answer}")
    print()
    print("Gate PASSED. Foundry is reachable; the Azure plan can proceed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m touchline.providers.smoke",
        description="Day 1 gate: does Microsoft Foundry answer?",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="only report the resolved configuration; make no network call",
    )
    args = parser.parse_args(argv)

    _load_env_file()
    endpoint, model = _report_config()

    if not endpoint or not model:
        print(
            "FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL are not both set.\n"
            "Provision the project first:\n"
            "    scripts/provision-foundry.sh\n"
            "then put the endpoint and the deployment name in "
            f"{ENV_FILE} (mode 600),\n"
            "or export them in this shell.",
            file=sys.stderr,
        )
        return 2

    if args.check_config:
        print(f"FOUNDRY_PROJECT_ENDPOINT = {endpoint}")
        print(f"FOUNDRY_MODEL            = {model}")
        return 0

    return asyncio.run(_run(endpoint, model))


if __name__ == "__main__":
    raise SystemExit(main())
