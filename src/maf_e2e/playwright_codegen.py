from __future__ import annotations

import json
from datetime import UTC, datetime

from maf_e2e.domain.hashing import sha256_text
from maf_e2e.domain.specification import (
    AssertionSpec,
    LocatorSpec,
    StructuredStep,
    TestSpecification,
)

GENERATOR_VERSION = "maf-e2e-playwright-ts-v1"


def generate_playwright_test(
    spec: TestSpecification,
    *,
    spec_hash_override: str | None = None,
    generated_at: datetime | str | None = None,
) -> str:
    hashed = spec.with_hash()
    rendered_spec_hash = spec_hash_override or hashed.spec_hash
    rendered_generated_at = _render_generated_at(generated_at)
    test_data = json.dumps(hashed.test_data, ensure_ascii=False, sort_keys=True)
    lines = [
        "import { test, expect } from '@playwright/test';",
        "",
        f"// scenario_id: {hashed.scenario_id}",
        f"// spec_version: {hashed.version}",
        f"// spec_hash: {rendered_spec_hash}",
        f"// generator_version: {GENERATOR_VERSION}",
        f"// generated_at: {rendered_generated_at}",
        f"const BASE_URL = {json.dumps(hashed.target_url, ensure_ascii=False)};",
        f"const TEST_DATA: unknown = {test_data};",
        "",
        "function data(path: string): unknown {",
        "  return path.split('.').reduce<unknown>((value, key) => {",
        "    if (typeof value !== 'object' || value === null || !(key in value)) {",
        "      throw new Error(`Missing test data: ${path}`);",
        "    }",
        "    return (value as Record<string, unknown>)[key];",
        "  }, TEST_DATA);",
        "}",
        "",
        f"test({json.dumps(hashed.name, ensure_ascii=False)}, async ({{ page }}, testInfo) => {{",
        "  const consoleErrors: string[] = [];",
        "  const networkErrors: string[] = [];",
        "  const mafStepResults: Array<Record<string, unknown>> = [];",
        "  const mafAssertionResults: Array<Record<string, unknown>> = [];",
        "  function mafError(error: unknown): string {",
        "    return error instanceof Error ? error.message : String(error);",
        "  }",
        "  page.on('console', message => {",
        "    if (message.type() === 'error') consoleErrors.push(message.text());",
        "  });",
        "  page.on('requestfailed', request => {",
        "    const failure = request.failure()?.errorText ?? 'failed';",
        "    networkErrors.push(`${request.method()} ${request.url()}: ${failure}`);",
        "  });",
        "",
        "  try {",
    ]
    for step in hashed.steps:
        lines.extend(_indent(_render_step(step)))
    for assertion in hashed.assertions:
        lines.extend(_indent(_render_assertion(assertion)))
    lines.extend(
        [
            "  } finally {",
            "    await testInfo.attach('maf-console-errors', {",
            "      body: JSON.stringify(consoleErrors),",
            "      contentType: 'application/json',",
            "    });",
            "    await testInfo.attach('maf-network-errors', {",
            "      body: JSON.stringify(networkErrors),",
            "      contentType: 'application/json',",
            "    });",
            "    await testInfo.attach('maf-step-results', {",
            "      body: JSON.stringify(mafStepResults),",
            "      contentType: 'application/json',",
            "    });",
            "    await testInfo.attach('maf-assertion-results', {",
            "      body: JSON.stringify(mafAssertionResults),",
            "      contentType: 'application/json',",
            "    });",
            "  }",
            "});",
            "",
        ]
    )
    return "\n".join(lines)


def generated_code_hash(source: str) -> str:
    return sha256_text(_hashable_source(source))


def _render_generated_at(value: datetime | str | None) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, str):
        return value
    return "1970-01-01T00:00:00Z"


def _hashable_source(source: str) -> str:
    return "\n".join(
        line for line in source.splitlines() if not line.startswith("// generated_at:")
    )


def _render_step(step: StructuredStep) -> list[str]:
    prefix = f"  // step: {step.step_id}"
    if step.action == "navigate":
        target = json.dumps(step.target, ensure_ascii=False)
        statement = f"await page.goto(new URL({target}, BASE_URL).toString());"
        return _test_step(
            prefix,
            f"step:{step.step_id}",
            _step_body(step, statement, locator_summary=None),
        )
    if step.locator is None:
        raise ValueError(f"Step {step.step_id} requires a locator")
    locator = _locator_expression(step.locator)
    if step.action == "click":
        statement = f"await {locator}.click();"
    elif step.action == "fill":
        statement = f"await {locator}.fill(String({_step_value(step)}));"
    elif step.action == "select":
        statement = f"await {locator}.selectOption(String({_step_value(step)}));"
    elif step.action == "press":
        statement = f"await {locator}.press(String({_step_value(step)}));"
    elif step.action == "check":
        statement = f"await {locator}.check();"
    elif step.action == "uncheck":
        statement = f"await {locator}.uncheck();"
    elif step.action == "upload":
        statement = f"await {locator}.setInputFiles(String({_step_value(step)}));"
    elif step.action == "wait":
        statement = f"await {locator}.waitFor();"
    else:
        raise ValueError(f"Unsupported action: {step.action}")
    return _test_step(
        prefix,
        f"step:{step.step_id}",
        _step_body(step, statement, locator_summary=_locator_summary(step.locator)),
    )


def _render_assertion(assertion: AssertionSpec) -> list[str]:
    prefix = f"  // assertion: {assertion.assertion_id}"
    expected = json.dumps(assertion.expected, ensure_ascii=False)
    if assertion.type == "url_matches":
        statement = f"await expect(page).toHaveURL(new RegExp(String({expected})));"
        return _test_step(
            prefix,
            f"assertion:{assertion.assertion_id}",
            _assertion_body(
                assertion,
                statement,
                actual_expression="page.url()",
                locator_summary=None,
            ),
        )
    if assertion.locator is None:
        raise ValueError(f"Assertion {assertion.assertion_id} requires a locator")
    locator = _locator_expression(assertion.locator)
    match assertion.type:
        case "visible":
            statement = f"await expect({locator}).toBeVisible();"
            actual_expression = f"await {locator}.isVisible()"
        case "hidden":
            statement = f"await expect({locator}).toBeHidden();"
            actual_expression = f"!(await {locator}.isVisible())"
        case "enabled":
            statement = f"await expect({locator}).toBeEnabled();"
            actual_expression = f"await {locator}.isEnabled()"
        case "disabled":
            statement = f"await expect({locator}).toBeDisabled();"
            actual_expression = f"!(await {locator}.isEnabled())"
        case "text_equals":
            statement = f"await expect({locator}).toHaveText(String({expected}));"
            actual_expression = f"await {locator}.textContent()"
        case "text_contains":
            statement = f"await expect({locator}).toContainText(String({expected}));"
            actual_expression = f"await {locator}.textContent()"
        case "value_equals":
            statement = f"await expect({locator}).toHaveValue(String({expected}));"
            actual_expression = f"await {locator}.inputValue()"
        case "count_equals":
            statement = f"await expect({locator}).toHaveCount(Number({expected}));"
            actual_expression = f"await {locator}.count()"
        case _:
            raise ValueError(f"Unsupported assertion: {assertion.type}")
    return _test_step(
        prefix,
        f"assertion:{assertion.assertion_id}",
        _assertion_body(
            assertion,
            statement,
            actual_expression=actual_expression,
            locator_summary=_locator_summary(assertion.locator),
        ),
    )


def _locator_expression(locator: LocatorSpec) -> str:
    if locator.strategy == "role":
        options = ""
        if locator.name is not None:
            options = f", {{ name: {json.dumps(locator.name, ensure_ascii=False)} }}"
        return f"page.getByRole({json.dumps(locator.role, ensure_ascii=False)}{options})"
    if locator.strategy == "label":
        return f"page.getByLabel({json.dumps(locator.value, ensure_ascii=False)})"
    if locator.strategy == "text":
        return f"page.getByText({json.dumps(locator.value, ensure_ascii=False)})"
    if locator.strategy == "test_id":
        return f"page.getByTestId({json.dumps(locator.value, ensure_ascii=False)})"
    if locator.strategy == "css":
        return f"page.locator({json.dumps(locator.value, ensure_ascii=False)})"
    if locator.strategy == "xpath":
        return f"page.locator({json.dumps('xpath=' + str(locator.value), ensure_ascii=False)})"
    raise ValueError(f"Unsupported locator strategy: {locator.strategy}")


def _step_value(step: StructuredStep) -> str:
    if step.value_ref:
        return f"data({json.dumps(step.value_ref, ensure_ascii=False)})"
    return json.dumps(step.value, ensure_ascii=False)


def _step_body(
    step: StructuredStep, statement: str, *, locator_summary: str | None
) -> list[str]:
    locator = json.dumps(locator_summary, ensure_ascii=False)
    return [
        "try {",
        f"  {statement}",
        "  mafStepResults.push({",
        f"    step_id: {json.dumps(step.step_id, ensure_ascii=False)},",
        f"    action: {json.dumps(step.action, ensure_ascii=False)},",
        "    status: 'passed',",
        "    url: page.url(),",
        f"    locator: {locator},",
        "  });",
        "} catch (error) {",
        "  mafStepResults.push({",
        f"    step_id: {json.dumps(step.step_id, ensure_ascii=False)},",
        f"    action: {json.dumps(step.action, ensure_ascii=False)},",
        "    status: 'failed',",
        "    url: page.url(),",
        f"    locator: {locator},",
        "    error: mafError(error),",
        "  });",
        "  throw error;",
        "}",
    ]


def _assertion_body(
    assertion: AssertionSpec,
    statement: str,
    *,
    actual_expression: str,
    locator_summary: str | None,
) -> list[str]:
    expected = json.dumps(assertion.expected, ensure_ascii=False)
    locator = json.dumps(locator_summary, ensure_ascii=False)
    return [
        "let actual: unknown = '';",
        "try {",
        f"  actual = {actual_expression};",
        f"  {statement}",
        "  mafAssertionResults.push({",
        f"    assertion_id: {json.dumps(assertion.assertion_id, ensure_ascii=False)},",
        "    status: 'passed',",
        f"    expected: {expected},",
        "    actual,",
        "    url: page.url(),",
        f"    locator: {locator},",
        "  });",
        "} catch (error) {",
        "  mafAssertionResults.push({",
        f"    assertion_id: {json.dumps(assertion.assertion_id, ensure_ascii=False)},",
        "    status: 'failed',",
        f"    expected: {expected},",
        "    actual,",
        "    url: page.url(),",
        f"    locator: {locator},",
        "    error: mafError(error),",
        "  });",
        "  throw error;",
        "}",
    ]


def _locator_summary(locator: LocatorSpec) -> str:
    return json.dumps(
        locator.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
    )


def _indent(lines: list[str]) -> list[str]:
    return [f"  {line}" for line in lines]


def _test_step(comment: str, title: str, body: list[str]) -> list[str]:
    return [
        comment,
        f"  await test.step({json.dumps(title)}, async () => {{",
        *[f"    {line}" for line in body],
        "  });",
    ]
