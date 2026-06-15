from __future__ import annotations

from regression_helpers import sample_spec

from maf_e2e.domain.specification import LocatorSpec, StructuredStep
from maf_e2e.playwright_codegen import generate_playwright_test, generated_code_hash


def test_codegen_is_deterministic_and_maps_role_and_label_locators() -> None:
    spec = sample_spec()

    first = generate_playwright_test(spec)
    second = generate_playwright_test(spec)

    assert first == second
    assert generated_code_hash(first) == generated_code_hash(second)
    assert "page.getByLabel(\"Email\").fill" in first
    assert 'page.getByRole("heading", { name: "Login" })' in first
    assert f"// spec_hash: {spec.spec_hash}" in first
    assert "// generated_at: 1970-01-01T00:00:00Z" in first
    assert "maf-step-results" in first
    assert "maf-assertion-results" in first
    assert "@playwright/test" in first


def test_generated_at_header_is_excluded_from_code_hash() -> None:
    spec = sample_spec()

    first = generate_playwright_test(spec, generated_at="2026-06-16T00:00:00Z")
    second = generate_playwright_test(spec, generated_at="2026-06-17T00:00:00Z")

    assert first != second
    assert generated_code_hash(first) == generated_code_hash(second)


def test_structured_step_rejects_missing_locator() -> None:
    try:
        StructuredStep(step_id="click", action="click")
    except ValueError as exc:
        assert "requires locator" in str(exc)
    else:
        raise AssertionError("missing locator should be rejected")


def test_locator_rejects_missing_strategy_value() -> None:
    try:
        LocatorSpec(strategy="css")
    except ValueError as exc:
        assert "requires value" in str(exc)
    else:
        raise AssertionError("missing CSS value should be rejected")
