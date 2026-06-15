from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Any

from agent_framework import MCPStdioTool
from azure.identity.aio import DefaultAzureCredential

from maf_e2e.artifacts import archive_run, blob_uri, upload_artifacts
from maf_e2e.authoring_workflow import AuthoringResult, build_authoring_workflow
from maf_e2e.codeact import (
    CodeActUnavailable,
    ToolAuditLog,
    build_audited_mcp_tools,
    build_codeact_provider,
    normalize_origin,
    preflight_hyperlight,
)
from maf_e2e.config import Settings
from maf_e2e.domain.assets import TrialRunResult
from maf_e2e.domain.failures import RegressionFailureDiagnostic
from maf_e2e.models import (
    E2ETestReport,
    E2ETestRequest,
    FailureKind,
    LiteralStatus,
    RunContext,
    StageFailure,
    TestPlan,
)
from maf_e2e.provider_backend import build_provider_backend
from maf_e2e.regression_analysis_workflow import (
    RegressionDiagnosticRequest,
    build_regression_analysis_workflow,
)
from maf_e2e.workflow import build_browser_resume_workflow, build_e2e_test_workflow

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


class GeminiCompatibleMCPStdioTool(MCPStdioTool):
    async def __aenter__(self) -> GeminiCompatibleMCPStdioTool:
        await super().__aenter__()
        _sanitize_mcp_function_schemas(self)
        return self


class RuntimeResources:
    def __init__(
        self,
        settings: Settings,
        resource_id: str,
        *,
        target_url: str | None,
        request_allowed_origins: list[str] | None = None,
    ) -> None:
        self.settings = settings
        self.resource_id = resource_id
        self.target_url = target_url
        self.run_dir = settings.artifact_root / resource_id
        self.playwright_dir = self.run_dir / "playwright"
        self.playwright_dir.mkdir(parents=True, exist_ok=True)
        self.backend = build_provider_backend(
            settings, _agent_config_dir(settings.agent_config_dir)
        )
        self.agents = self.backend.agents
        self.blob_credential = (
            DefaultAzureCredential() if settings.blob_account_url else None
        )
        self.allowed_origins = {
            normalize_origin(origin)
            for origin in (
                request_allowed_origins
                or settings.playwright_allowed_origins
                or ([target_url] if target_url is not None else [])
            )
        }
        tool_class = (
            GeminiCompatibleMCPStdioTool
            if self.backend.sanitize_mcp_schemas
            else MCPStdioTool
        )
        allowed_tools = set(PLAYWRIGHT_ALLOWED_TOOLS)
        if not settings.codeact_allow_file_upload:
            allowed_tools.discard("browser_file_upload")
        self.mcp = tool_class(
            name="playwright",
            command=settings.playwright_command,
            args=settings.playwright_args(
                self.playwright_dir,
                default_allowed_origin=(normalize_origin(target_url) if target_url else None),
            ),
            request_timeout=max(settings.playwright_navigation_timeout_ms // 1000 + 30, 90),
            allowed_tools=allowed_tools,
        )
        self._mcp_entered = False
        self.codeact_active = False
        self.codeact_error: CodeActUnavailable | None = None
        self.codeact_providers: list[Any] = []
        self.audit_log = ToolAuditLog()

    async def start(self) -> RuntimeResources:
        await self.backend.start()
        await self.mcp.__aenter__()
        self._mcp_entered = True
        if self.settings.codeact_mode != "disabled":
            try:
                preflight_hyperlight(require_kvm=self.settings.codeact_require_kvm)
                self._enable_codeact()
            except CodeActUnavailable as exc:
                self.codeact_error = exc
                if self.settings.codeact_mode == "required":
                    return self
        return self

    def _enable_codeact(self) -> None:
        for stage in ("discovery", "browser"):
            provider = build_codeact_provider(
                stage=stage,
                mcp_functions=self.mcp.functions,
                allowed_origins=self.allowed_origins,
                allow_file_upload=self.settings.codeact_allow_file_upload,
                allow_destructive_actions=self.settings.codeact_allow_destructive_actions,
                max_code_bytes=self.settings.codeact_max_code_bytes,
                max_invocations=self.settings.codeact_max_invocations,
                audit_log=self.audit_log,
                host_loop=asyncio.get_running_loop(),
            )
            agent = getattr(self.agents, stage)
            agent.context_providers.append(provider)
            self.codeact_providers.append(provider)
        self.codeact_active = True

    def workflow(self, *, checkpoint_root: Path, interactive: bool) -> Any:
        discovery_tools, browser_tools = self._workflow_tools()
        return build_e2e_test_workflow(
            self.agents,
            checkpoint_root,
            discovery_tools=discovery_tools,
            browser_tools=browser_tools,
            structured_retries=self.settings.structured_output_retries,
            use_native_response_format=self.backend.use_native_response_format,
            interactive=interactive,
        )

    def browser_resume_workflow(self, *, checkpoint_root: Path) -> Any:
        _, browser_tools = self._workflow_tools()
        return build_browser_resume_workflow(
            self.agents,
            checkpoint_root,
            browser_tools=browser_tools,
            structured_retries=self.settings.structured_output_retries,
            use_native_response_format=self.backend.use_native_response_format,
        )

    def authoring_workflow(self, *, repository_root: Path) -> Any:
        discovery_tools, browser_tools = self._workflow_tools()
        return build_authoring_workflow(
            self.agents,
            repository_root,
            discovery_tools=discovery_tools,
            diagnostic_tools=browser_tools,
            structured_retries=self.settings.structured_output_retries,
            use_native_response_format=self.backend.use_native_response_format,
            validation_timeout_seconds=self.settings.authoring_timeout_seconds,
            trial_timeout_seconds=self.settings.trial_timeout_seconds,
        )

    def _workflow_tools(self) -> tuple[list[Any] | None, list[Any] | None]:
        if self.codeact_active:
            return None, None
        return (
            build_audited_mcp_tools(
                stage="discovery",
                mcp_functions=self.mcp.functions,
                allowed_origins=self.allowed_origins,
                allow_file_upload=self.settings.codeact_allow_file_upload,
                allow_destructive_actions=self.settings.codeact_allow_destructive_actions,
                audit_log=self.audit_log,
            ),
            build_audited_mcp_tools(
                stage="browser",
                mcp_functions=self.mcp.functions,
                allowed_origins=self.allowed_origins,
                allow_file_upload=self.settings.codeact_allow_file_upload,
                allow_destructive_actions=self.settings.codeact_allow_destructive_actions,
                audit_log=self.audit_log,
            ),
        )

    async def close(self) -> None:
        for provider in self.codeact_providers:
            execute_code = getattr(provider, "_execute_code_tool", None)
            close = getattr(execute_code, "close", None)
            if callable(close):
                close()
        if self._mcp_entered or self.mcp.is_connected:
            await self.mcp.close()
        await self.backend.close()
        if self.blob_credential is not None:
            await self.blob_credential.close()


class E2ETestRuntime:
    def __init__(self, settings: Settings, request: E2ETestRequest) -> None:
        self.settings = settings
        self.request = request
        self.resources = RuntimeResources(
            settings,
            request.run_id,
            target_url=request.target_url,
            request_allowed_origins=request.allowed_origins,
        )

    async def __aenter__(self) -> E2ETestRuntime:
        try:
            await self.resources.start()
        except BaseException:
            await self.resources.close()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.resources.close()

    async def run(self, *, resume_plan: TestPlan | None = None) -> E2ETestReport:
        if self.settings.codeact_mode == "required" and self.resources.codeact_error is not None:
            report = _codeact_blocked_report(self.request, self.resources.codeact_error)
            return await self._persist_report(report)
        checkpoint_root = self.settings.checkpoint_root / self.request.run_id
        if resume_plan is None:
            workflow = self.resources.workflow(checkpoint_root=checkpoint_root, interactive=False)
            result = await workflow.run(self.request)
        else:
            if resume_plan.discovery.run.run_id != self.request.run_id:
                raise ValueError("Resume plan run_id does not match the runtime request")
            workflow = self.resources.browser_resume_workflow(checkpoint_root=checkpoint_root)
            result = await workflow.run(resume_plan)
        outputs = result.get_outputs()
        if len(outputs) != 1 or not isinstance(outputs[0], E2ETestReport):
            raise RuntimeError(f"Workflow returned unexpected outputs: {outputs!r}")

        return await self._persist_report(outputs[0])

    async def run_authoring(self) -> AuthoringResult:
        repository_root = self.request.target_repository_root
        if repository_root is None:
            raise ValueError("Authoring requires target_repository_root")
        if not self.request.expected_results:
            raise ValueError("Authoring requires at least one expected result")
        if self.settings.codeact_mode == "required" and self.resources.codeact_error is not None:
            return AuthoringResult(
                run_id=self.request.run_id,
                status="blocked",
                reason="CodeAct is required but unavailable.",
            )
        workflow = self.resources.authoring_workflow(repository_root=repository_root)
        result = await workflow.run(self.request)
        outputs = result.get_outputs()
        if len(outputs) != 1 or not isinstance(outputs[0], AuthoringResult):
            raise RuntimeError(f"Authoring workflow returned unexpected outputs: {outputs!r}")
        return outputs[0]

    async def _persist_report(self, report: E2ETestReport) -> E2ETestReport:
        run_dir = self.resources.run_dir
        report_path = run_dir / "report.json"
        audit_path = run_dir / "tool-audit.json"
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
                blob_uri(
                    self.settings.blob_account_url,
                    self.settings.blob_container,
                    report.run_id,
                    audit_path.name,
                ),
            ]
        else:
            report.artifact_uris = [
                str(report_path.resolve()),
                str(archive_path.resolve()),
                str(audit_path.resolve()),
            ]

        report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        audit_path.write_text(
            json.dumps(self.resources.audit_log.serializable(), indent=2), encoding="utf-8"
        )
        archive = archive_run(run_dir)
        if self.settings.blob_account_url:
            if self.resources.blob_credential is None:
                raise RuntimeError("Blob upload requires an Azure credential")
            await upload_artifacts(
                [report_path, archive, audit_path],
                account_url=self.settings.blob_account_url,
                container_name=self.settings.blob_container,
                credential=self.resources.blob_credential,
                run_id=report.run_id,
            )
        return report


async def execute(
    settings: Settings, request: E2ETestRequest, *, resume_plan: TestPlan | None = None
) -> E2ETestReport:
    async with E2ETestRuntime(settings, request) as runtime:
        return await runtime.run(resume_plan=resume_plan)


async def execute_authoring(settings: Settings, request: E2ETestRequest) -> AuthoringResult:
    async with E2ETestRuntime(settings, request) as runtime:
        return await runtime.run_authoring()


async def execute_regression_diagnostic(
    settings: Settings,
    trial: TrialRunResult,
    *,
    target_url: str,
    allowed_origins: list[str] | None = None,
) -> RegressionFailureDiagnostic:
    resources = RuntimeResources(
        settings,
        f"failure-{trial.run_id}",
        target_url=target_url,
        request_allowed_origins=allowed_origins,
    )
    try:
        await resources.start()
        if settings.codeact_mode == "required" and resources.codeact_error is not None:
            raise resources.codeact_error
        _, browser_tools = resources._workflow_tools()
        workflow = build_regression_analysis_workflow(
            resources.agents.browser,
            tools=browser_tools,
            structured_retries=settings.structured_output_retries,
            use_native_response_format=resources.backend.use_native_response_format,
        )
        result = await workflow.run(
            RegressionDiagnosticRequest(target_url=target_url, trial=trial)
        )
        outputs = result.get_outputs()
        if len(outputs) != 1 or not isinstance(outputs[0], RegressionFailureDiagnostic):
            raise RuntimeError(f"Failure analysis returned unexpected outputs: {outputs!r}")
        return outputs[0]
    finally:
        await resources.close()


def _agent_config_dir(configured: Path) -> Path:
    if configured.is_dir():
        return configured
    if configured == Path("agents"):
        packaged = Path(__file__).with_name("agent_definitions")
        if packaged.is_dir():
            return packaged
    return configured


def _codeact_blocked_report(request: E2ETestRequest, exc: CodeActUnavailable) -> E2ETestReport:
    run = RunContext(request=request, run_id=request.run_id)
    failure = StageFailure(
        run=run,
        stage="codeact",
        attempt=1,
        kind=FailureKind.CONFIGURATION,
        exception_type=type(exc).__name__,
        message="CodeAct runtime is unavailable on this execution host",
        retryable=False,
        input_type="E2ETestRequest",
        stage_input=request.model_dump(mode="json"),
    )
    return E2ETestReport(
        run_id=request.run_id,
        target_url=request.target_url,
        status=LiteralStatus.BLOCKED,
        passed=False,
        score=0,
        attempts=0,
        summary="CodeAct is required but the Hyperlight runtime failed preflight.",
        policy_results=[],
        security_findings=[],
        failures=[failure],
    )


def _sanitize_mcp_function_schemas(tool: MCPStdioTool) -> None:
    for function in tool.functions:
        function.parameters = _sanitized_parameters_factory(  # type: ignore[method-assign]
            function.parameters
        )


def _sanitized_parameters_factory(original_parameters: Any) -> Any:
    def parameters_without_dialect(*args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = original_parameters(*args, **kwargs)
        if not isinstance(schema, Mapping):
            raise ValueError("MCP function parameters must be a JSON schema mapping")
        cleaned = _strip_json_schema_dialect(dict(schema))
        if not cleaned or cleaned.get("type") != "object":
            raise ValueError("MCP function parameters must remain an object schema")
        return cleaned

    return parameters_without_dialect


def _strip_json_schema_dialect(schema: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in schema.items():
        if key in {
            "$schema",
            "additionalProperties",
            "additional_properties",
            "propertyNames",
        }:
            continue
        if isinstance(value, dict):
            cleaned[key] = _strip_json_schema_dialect(value)
        elif isinstance(value, list):
            cleaned[key] = [
                _strip_json_schema_dialect(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            cleaned[key] = value
    return cleaned
