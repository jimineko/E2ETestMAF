from __future__ import annotations

import json
import os
from base64 import b64decode
from pathlib import Path
from typing import Literal
from uuid import uuid4

from maf_e2e.code_validation import write_draft_playwright_config
from maf_e2e.domain.assets import AssertionResult, StepResult, TrialRunResult
from maf_e2e.playwright_codegen import generated_code_hash
from maf_e2e.process import run_process


class TrialRunner:
    def __init__(
        self,
        repository_root: Path,
        *,
        timeout_seconds: int = 300,
        output_limit_bytes: int = 2_000_000,
    ) -> None:
        self.repository_root = repository_root.resolve(strict=True)
        self.timeout_seconds = timeout_seconds
        self.output_limit_bytes = output_limit_bytes

    async def run(
        self,
        scenario_id: str,
        spec_path: Path,
        *,
        artifact_dir: Path,
    ) -> TrialRunResult:
        spec_path = spec_path.resolve(strict=True)
        if not spec_path.is_relative_to(self.repository_root):
            raise ValueError("Trial spec is outside the target repository")
        source = spec_path.read_text(encoding="utf-8")
        run_id = uuid4().hex
        run_dir = artifact_dir.resolve() / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        json_path = run_dir / "report.json"
        junit_path = run_dir / "junit.xml"
        html_path = run_dir / "html"
        test_results_path = run_dir / "test-results"
        executable = self.repository_root / "node_modules" / ".bin" / "playwright"
        if not executable.exists():
            raise RuntimeError("Target repository requires a local Playwright installation")
        command = [
            str(executable),
            "test",
            spec_path.name,
            "--config",
            str(
                write_draft_playwright_config(
                    self.repository_root, spec_path, config_dir=run_dir
                )
            ),
            "--reporter=json,junit,html",
            "--output",
            str(test_results_path),
            "--trace=on",
            "--screenshot=on",
        ]
        env = dict(os.environ)
        env.update(
            {
                "PLAYWRIGHT_JSON_OUTPUT_NAME": str(json_path),
                "PLAYWRIGHT_JUNIT_OUTPUT_FILE": str(junit_path),
                "PLAYWRIGHT_HTML_OUTPUT_DIR": str(html_path),
                "PLAYWRIGHT_HTML_OPEN": "never",
            }
        )
        result = await run_process(
            command,
            cwd=self.repository_root,
            timeout_seconds=self.timeout_seconds,
            output_limit_bytes=self.output_limit_bytes,
            env=env,
        )
        report = _load_json_report(json_path, result.stdout)
        step_results = _step_results(report)
        assertions = _assertion_results(report)
        screenshots = [str(path) for path in run_dir.rglob("*.png")]
        traces = list(run_dir.rglob("trace.zip"))
        console_errors = _attachment_values(report, "maf-console-errors")
        network_errors = _attachment_values(report, "maf-network-errors")
        status: Literal["passed", "failed", "blocked"] = (
            "blocked" if result.timed_out else "passed" if result.exit_code == 0 else "failed"
        )
        error = (
            None
            if status == "passed"
            else (result.stderr or result.stdout or "Playwright failed")
        )
        return TrialRunResult(
            run_id=run_id,
            scenario_id=scenario_id,
            code_hash=generated_code_hash(source),
            status=status,
            step_results=step_results,
            assertion_results=assertions,
            final_url=_final_url(step_results, assertions),
            screenshot_paths=screenshots,
            trace_path=str(traces[0]) if traces else None,
            console_errors=console_errors,
            network_errors=network_errors,
            report_path=str(json_path),
            junit_path=str(junit_path) if junit_path.exists() else None,
            html_report_path=str(html_path) if html_path.exists() else None,
            error=error,
            duration_seconds=result.duration_seconds,
        )


def _load_json_report(path: Path, stdout: str) -> dict[str, object]:
    raw = path.read_text(encoding="utf-8") if path.exists() else stdout
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _assertion_results(report: dict[str, object]) -> list[AssertionResult]:
    structured = _structured_assertion_results(report)
    if structured:
        return structured
    results: list[AssertionResult] = []
    for step in _walk_values(report, "steps"):
        if not isinstance(step, dict):
            continue
        title = str(step.get("title", ""))
        if not title.startswith("assertion:"):
            continue
        error = step.get("error")
        results.append(
            AssertionResult(
                assertion_id=title.removeprefix("assertion:"),
                status="failed" if error else "passed",
                error=json.dumps(error, ensure_ascii=False) if error else None,
            )
        )
    if results:
        return results
    for test in _walk_values(report, "tests"):
        if not isinstance(test, dict):
            continue
        title = str(test.get("title", "playwright-test"))
        test_results = test.get("results", [])
        if not isinstance(test_results, list):
            continue
        for index, result in enumerate(test_results):
            if not isinstance(result, dict):
                continue
            status = str(result.get("status", "failed"))
            mapped: Literal["passed", "failed", "skipped"] = (
                "passed" if status == "passed" else "skipped" if status == "skipped" else "failed"
            )
            error = result.get("error")
            results.append(
                AssertionResult(
                    assertion_id=f"{title}-{index + 1}",
                    status=mapped,
                    error=json.dumps(error, ensure_ascii=False) if error else None,
                )
            )
    return results


def _step_results(report: dict[str, object]) -> list[StepResult]:
    results: list[StepResult] = []
    for item in _json_attachment_items(report, "maf-step-results"):
        if not isinstance(item, dict):
            continue
        step_id = item.get("step_id")
        action = item.get("action")
        if step_id is None or action is None:
            continue
        results.append(
            StepResult(
                step_id=str(step_id),
                action=str(action),
                status=_evidence_status(item.get("status")),
                url=_stringify_evidence(item.get("url")),
                locator=_optional_evidence(item.get("locator")),
                error=_optional_evidence(item.get("error")),
            )
        )
    return results


def _structured_assertion_results(report: dict[str, object]) -> list[AssertionResult]:
    results: list[AssertionResult] = []
    for item in _json_attachment_items(report, "maf-assertion-results"):
        if not isinstance(item, dict):
            continue
        assertion_id = item.get("assertion_id")
        if assertion_id is None:
            continue
        results.append(
            AssertionResult(
                assertion_id=str(assertion_id),
                status=_evidence_status(item.get("status")),
                expected=_stringify_evidence(item.get("expected")),
                actual=_stringify_evidence(item.get("actual")),
                url=_stringify_evidence(item.get("url")),
                locator=_optional_evidence(item.get("locator")),
                error=_optional_evidence(item.get("error")),
            )
        )
    return results


def _evidence_status(value: object) -> Literal["passed", "failed", "skipped"]:
    status = str(value)
    if status == "passed":
        return "passed"
    if status == "skipped":
        return "skipped"
    return "failed"


def _stringify_evidence(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _optional_evidence(value: object) -> str | None:
    if value is None:
        return None
    return _stringify_evidence(value)


def _final_url(
    step_results: list[StepResult], assertion_results: list[AssertionResult]
) -> str:
    for assertion_result in reversed(assertion_results):
        if assertion_result.url:
            return assertion_result.url
    for step_result in reversed(step_results):
        if step_result.url:
            return step_result.url
    return ""


def _walk_values(value: object, key: str) -> list[object]:
    found: list[object] = []
    if isinstance(value, dict):
        candidate = value.get(key)
        if isinstance(candidate, list):
            found.extend(candidate)
        for child in value.values():
            found.extend(_walk_values(child, key))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_values(child, key))
    return found


def _attachment_values(report: dict[str, object], name: str) -> list[str]:
    values: list[str] = []
    for payload in _attachment_payloads(report, name):
        if isinstance(payload, list):
            values.extend(str(item) for item in payload)
        else:
            values.append(_stringify_evidence(payload))
    return values


def _json_attachment_items(report: dict[str, object], name: str) -> list[object]:
    items: list[object] = []
    for payload in _attachment_payloads(report, name):
        if isinstance(payload, list):
            items.extend(payload)
        else:
            items.append(payload)
    return items


def _attachment_payloads(report: dict[str, object], name: str) -> list[object]:
    payloads: list[object] = []
    for attachments in _walk_values(report, "attachments"):
        if not isinstance(attachments, dict) or attachments.get("name") != name:
            continue
        raw: str | None = None
        body = attachments.get("body")
        path = attachments.get("path")
        if isinstance(body, str):
            try:
                raw = b64decode(body).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                raw = body
        elif isinstance(path, str) and Path(path).exists():
            raw = Path(path).read_text(encoding="utf-8")
        if raw is None:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payloads.append(raw)
        else:
            payloads.append(payload)
    return payloads
