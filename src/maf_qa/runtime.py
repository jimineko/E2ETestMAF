from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any

from agent_framework import MCPStdioTool, SupportsChatGetResponse
from agent_framework.openai import OpenAIChatClient
from agent_framework_gemini import GeminiChatClient
from azure.identity.aio import DefaultAzureCredential

from maf_qa.agent_config import load_agent_set
from maf_qa.artifacts import archive_run, blob_uri, upload_artifacts
from maf_qa.config import Settings
from maf_qa.models import QAReport, QARequest
from maf_qa.workflow import AgentSet, build_qa_workflow

PLAYWRIGHT_ALLOWED_TOOLS = {
    "browser_click",
    "browser_close",
    "browser_console_messages",
    "browser_drag",
    "browser_drop",
    "browser_file_upload",
    "browser_fill_form",
    "browser_handle_dialog",
    "browser_hover",
    "browser_navigate",
    "browser_navigate_back",
    "browser_network_request",
    "browser_network_requests",
    "browser_press_key",
    "browser_resize",
    "browser_select_option",
    "browser_snapshot",
    "browser_start_tracing",
    "browser_stop_tracing",
    "browser_tabs",
    "browser_take_screenshot",
    "browser_type",
    "browser_wait_for",
}


def build_chat_client(
    settings: Settings,
    credential: DefaultAzureCredential | None,
) -> SupportsChatGetResponse[Any]:
    if settings.model_provider == "gemini":
        api_key = (
            settings.gemini_api_key.get_secret_value()
            if settings.gemini_api_key is not None
            else None
        )
        return GeminiChatClient(
            api_key=api_key,
            model=settings.gemini_model,
            vertexai=settings.gemini_use_vertex_ai,
            project=settings.gemini_vertex_project,
            location=settings.gemini_vertex_location,
        )

    if credential is None:
        raise RuntimeError("Azure OpenAI requires an Azure credential")
    return OpenAIChatClient(
        model=settings.azure_openai_deployment,
        credential=credential,
        azure_endpoint=settings.azure_openai_endpoint,
        api_version=settings.azure_openai_api_version,
    )


class RuntimeResources:
    def __init__(self, settings: Settings, resource_id: str) -> None:
        self.settings = settings
        self.resource_id = resource_id
        self.run_dir = settings.artifact_root / resource_id
        self.playwright_dir = self.run_dir / "playwright"
        self.playwright_dir.mkdir(parents=True, exist_ok=True)
        needs_azure_credential = settings.model_provider == "azure_openai" or bool(
            settings.blob_account_url
        )
        self.credential = DefaultAzureCredential() if needs_azure_credential else None
        self.client = build_chat_client(settings, self.credential)
        self.mcp = MCPStdioTool(
            name="playwright",
            command=settings.playwright_command,
            args=settings.playwright_args(self.playwright_dir),
            request_timeout=max(settings.playwright_navigation_timeout_ms // 1000 + 30, 90),
            allowed_tools=PLAYWRIGHT_ALLOWED_TOOLS,
        )
        self.agents: AgentSet = load_agent_set(
            _agent_config_dir(settings.agent_config_dir),
            self.client,
            skill_paths=settings.skill_paths,
            model_retries=settings.model_retries,
            trace_content=settings.trace_content,
        )
        self._mcp_entered = False

    async def start(self) -> RuntimeResources:
        await self.mcp.__aenter__()
        self._mcp_entered = True
        return self

    def workflow(self, *, checkpoint_root: Path, interactive: bool) -> Any:
        return build_qa_workflow(
            self.agents,
            checkpoint_root,
            tools=[self.mcp],
            structured_retries=self.settings.structured_output_retries,
            interactive=interactive,
        )

    async def close(self) -> None:
        if self._mcp_entered or self.mcp.is_connected:
            await self.mcp.close()
        if self.credential is not None:
            await self.credential.close()


class QARuntime:
    def __init__(self, settings: Settings, request: QARequest) -> None:
        self.settings = settings
        self.request = request
        self.resources = RuntimeResources(settings, request.run_id)

    async def __aenter__(self) -> QARuntime:
        await self.resources.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.resources.close()

    async def run(self) -> QAReport:
        workflow = self.resources.workflow(
            checkpoint_root=self.settings.checkpoint_root / self.request.run_id,
            interactive=False,
        )
        result = await workflow.run(self.request)
        outputs = result.get_outputs()
        if len(outputs) != 1 or not isinstance(outputs[0], QAReport):
            raise RuntimeError(f"Workflow returned unexpected outputs: {outputs!r}")

        report = outputs[0]
        run_dir = self.resources.run_dir
        report_path = run_dir / "report.json"
        archive_path = run_dir.parent / f"{run_dir.name}.zip"
        if self.settings.blob_account_url:
            report.artifact_uris = [
                blob_uri(
                    self.settings.blob_account_url,
                    self.settings.blob_container,
                    report.run_id,
                    report_path.name,
                ),
                blob_uri(
                    self.settings.blob_account_url,
                    self.settings.blob_container,
                    report.run_id,
                    archive_path.name,
                ),
            ]
        else:
            report.artifact_uris = [str(report_path.resolve()), str(archive_path.resolve())]

        report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        archive = archive_run(run_dir)
        if self.settings.blob_account_url:
            if self.resources.credential is None:
                raise RuntimeError("Blob upload requires an Azure credential")
            await upload_artifacts(
                [report_path, archive],
                account_url=self.settings.blob_account_url,
                container_name=self.settings.blob_container,
                credential=self.resources.credential,
                run_id=report.run_id,
            )
        return report


async def execute(settings: Settings, request: QARequest) -> QAReport:
    async with QARuntime(settings, request) as runtime:
        return await runtime.run()


def _agent_config_dir(configured: Path) -> Path:
    if configured.is_dir():
        return configured
    if configured == Path("agents"):
        packaged = Path(__file__).with_name("agent_definitions")
        if packaged.is_dir():
            return packaged
    return configured
