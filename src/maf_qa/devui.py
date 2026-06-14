from __future__ import annotations

import sys

from agent_framework.devui import register_cleanup, serve

from maf_qa.config import Settings
from maf_qa.runtime import RuntimeResources
from maf_qa.telemetry import configure_telemetry


def main() -> None:
    try:
        settings = Settings()
        if settings.devui_host not in {"127.0.0.1", "localhost"}:
            raise ValueError("DevUI is restricted to localhost")
        configure_telemetry(settings)
        resources = RuntimeResources(settings, "devui", target_url=settings.target_url)
        workflow = resources.workflow(
            checkpoint_root=settings.checkpoint_root / "devui",
            interactive=True,
        )
        register_cleanup(workflow, resources.close)
        serve(
            entities=[workflow],
            host=settings.devui_host,
            port=settings.devui_port,
            instrumentation_enabled=False,
            auth_enabled=True,
            auth_token=(
                settings.devui_auth_token.get_secret_value().strip() or None
                if settings.devui_auth_token is not None
                else None
            ),
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
