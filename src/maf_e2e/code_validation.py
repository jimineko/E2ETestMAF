from __future__ import annotations

import json
import shutil
from pathlib import Path

from maf_e2e.domain.assets import ValidationCheck, ValidationResult
from maf_e2e.process import run_process


class CodeValidationError(RuntimeError):
    pass


class CodeValidator:
    def __init__(
        self,
        repository_root: Path,
        *,
        timeout_seconds: int = 120,
        output_limit_bytes: int = 1_000_000,
    ) -> None:
        self.repository_root = repository_root.resolve(strict=True)
        self.timeout_seconds = timeout_seconds
        self.output_limit_bytes = output_limit_bytes

    async def validate(self, spec_path: Path) -> ValidationResult:
        spec_path = spec_path.resolve(strict=True)
        if not spec_path.is_relative_to(self.repository_root):
            raise CodeValidationError("Generated test is outside the target repository")
        commands = self._commands(spec_path)
        checks: list[ValidationCheck] = []
        for name, command in commands:
            result = await run_process(
                command,
                cwd=self.repository_root,
                timeout_seconds=self.timeout_seconds,
                output_limit_bytes=self.output_limit_bytes,
            )
            checks.append(
                ValidationCheck(
                    name=name,  # type: ignore[arg-type]
                    command=result.command,
                    passed=result.exit_code == 0 and not result.timed_out,
                    exit_code=result.exit_code,
                    stdout=result.stdout,
                    stderr=("Timed out\n" if result.timed_out else "") + result.stderr,
                    duration_seconds=result.duration_seconds,
                )
            )
            if result.exit_code != 0 or result.timed_out:
                break
        return ValidationResult(
            passed=len(checks) == 4 and all(check.passed for check in checks),
            checks=checks,
        )

    def _commands(self, spec_path: Path) -> list[tuple[str, list[str]]]:
        package_manager = detect_package_manager(self.repository_root)
        package = _load_package_json(self.repository_root)
        relative = str(spec_path.relative_to(self.repository_root))
        scripts = package.get("scripts", {})
        if not isinstance(scripts, dict):
            scripts = {}

        formatter = _required_binary(self.repository_root, "prettier")
        format_command = [str(formatter), "--write", relative]
        lint_command = _script_or_binary(
            self.repository_root,
            package_manager,
            scripts,
            script_names=("lint",),
            binary="eslint",
            binary_args=[relative],
            label="lint",
        )
        type_config = write_draft_typescript_config(self.repository_root, spec_path)
        typescript = _required_binary(self.repository_root, "tsc")
        type_command = [str(typescript), "--project", str(type_config)]
        playwright = _required_binary(self.repository_root, "playwright")
        playwright_config = write_draft_playwright_config(self.repository_root, spec_path)
        discovery_command = [
            str(playwright),
            "test",
            spec_path.name,
            "--config",
            str(playwright_config),
            "--list",
        ]
        return [
            ("format", format_command),
            ("lint", lint_command),
            ("type_check", type_command),
            ("discovery", discovery_command),
        ]


def detect_package_manager(repository_root: Path) -> str:
    if (repository_root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (repository_root / "yarn.lock").exists():
        return "yarn"
    return "npm"


def _load_package_json(repository_root: Path) -> dict[str, object]:
    path = repository_root / "package.json"
    if not path.exists():
        raise CodeValidationError("Target repository requires package.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CodeValidationError("package.json must contain an object")
    return payload


def _script_or_binary(
    repository_root: Path,
    package_manager: str,
    scripts: dict[object, object],
    *,
    script_names: tuple[str, ...],
    binary: str,
    binary_args: list[str],
    label: str,
) -> list[str]:
    script = next((name for name in script_names if name in scripts), None)
    if script is not None:
        return _run_script(package_manager, script)
    try:
        executable = _required_binary(repository_root, binary)
    except CodeValidationError as exc:
        raise CodeValidationError(
            f"Target repository has no {label} script or local {binary} executable"
        ) from exc
    return [str(executable), *binary_args]


def _run_script(package_manager: str, script: str) -> list[str]:
    executable = shutil.which(package_manager)
    if executable is None:
        raise CodeValidationError(f"Package manager is unavailable: {package_manager}")
    if package_manager == "yarn":
        return [executable, script]
    return [executable, "run", script]


def _required_binary(repository_root: Path, binary: str) -> Path:
    suffix = ".cmd" if shutil.which("cmd.exe") else ""
    path = repository_root / "node_modules" / ".bin" / f"{binary}{suffix}"
    if not path.exists():
        raise CodeValidationError(f"Required local executable is unavailable: {binary}")
    return path


def write_draft_playwright_config(
    repository_root: Path, spec_path: Path, *, config_dir: Path | None = None
) -> Path:
    config_root = config_dir or spec_path.parent
    config_root.mkdir(parents=True, exist_ok=True)
    config_path = config_root / "maf-playwright.config.ts"
    test_dir = spec_path.parent.as_posix()
    test_match = spec_path.name
    existing = next(
        (
            repository_root / name
            for name in (
                "playwright.config.ts",
                "playwright.config.mts",
                "playwright.config.js",
                "playwright.config.mjs",
                "playwright.config.cjs",
            )
            if (repository_root / name).exists()
        ),
        None,
    )
    if existing is None:
        content = (
            "import { defineConfig } from '@playwright/test';\n"
            "export default defineConfig({ "
            f"testDir: {json.dumps(test_dir)}, testMatch: {json.dumps(test_match)} "
            "});\n"
        )
    else:
        relative = existing.relative_to(config_path.parent, walk_up=True).as_posix()
        if not relative.startswith("."):
            relative = f"./{relative}"
        content = (
            f"import baseConfig from {json.dumps(relative)};\n"
            "export default { ...baseConfig, "
            f"testDir: {json.dumps(test_dir)}, testMatch: {json.dumps(test_match)} "
            "};\n"
        )
    config_path.write_text(content, encoding="utf-8")
    return config_path


def write_draft_typescript_config(repository_root: Path, spec_path: Path) -> Path:
    config_path = spec_path.parent / "maf-tsconfig.json"
    existing = repository_root / "tsconfig.json"
    payload: dict[str, object] = {
        "compilerOptions": {
            "noEmit": True,
            "skipLibCheck": True,
            "target": "ES2022",
            "module": "Node16",
            "moduleResolution": "Node16",
        },
        "include": [spec_path.name],
    }
    if existing.exists():
        payload["extends"] = existing.relative_to(spec_path.parent, walk_up=True).as_posix()
    config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return config_path
