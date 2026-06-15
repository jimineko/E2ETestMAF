from __future__ import annotations

import json
from pathlib import Path

from maf_e2e.domain.specification import (
    AssertionSpec,
    LocatorSpec,
    StructuredStep,
    TestSpecification,
)


def sample_spec() -> TestSpecification:
    return TestSpecification(
        scenario_id="login-page-1234567890",
        feature="login",
        name="Login page",
        objective="Show the login form",
        target_url="https://example.com",
        steps=[
            StructuredStep(step_id="navigate", action="navigate", target="/login"),
            StructuredStep(
                step_id="fill-email",
                action="fill",
                locator=LocatorSpec(strategy="label", value="Email"),
                value_ref="user.email",
            ),
        ],
        assertions=[
            AssertionSpec(
                assertion_id="heading-visible",
                type="visible",
                locator=LocatorSpec(strategy="role", role="heading", name="Login"),
                source_expected_result="Login heading is visible",
            )
        ],
        test_data={"user": {"email": "user@example.com"}},
    ).with_hash()


def make_fake_node_repository(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(
        json.dumps({"name": "fixture", "private": True, "scripts": {}}),
        encoding="utf-8",
    )
    binary_root = root / "node_modules" / ".bin"
    binary_root.mkdir(parents=True)
    for binary in ("prettier", "eslint", "tsc"):
        _write_executable(binary_root / binary, "#!/bin/sh\nexit 0\n")
    _write_executable(
        binary_root / "playwright",
        """#!/bin/sh
case " $* " in
  *" --list "*) exit 0 ;;
esac
printf '{"suites":[]}' > "$PLAYWRIGHT_JSON_OUTPUT_NAME"
printf '<testsuite tests="1" failures="0" />' > "$PLAYWRIGHT_JUNIT_OUTPUT_FILE"
mkdir -p "$PLAYWRIGHT_HTML_OUTPUT_DIR"
printf '<html></html>' > "$PLAYWRIGHT_HTML_OUTPUT_DIR/index.html"
exit 0
""",
    )


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
