from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_nightly_template_installs_e2etestmaf_without_agent_secrets() -> None:
    workflow = (
        REPOSITORY_ROOT / "templates" / "github-actions" / "e2e-nightly.yml"
    ).read_text(encoding="utf-8")

    assert "actions/setup-python@v5" in workflow
    assert "python-version: \"3.13\"" in workflow
    assert "python -m pip install \"git+https://github.com/jimineko/E2ETestMAF.git\"" in workflow
    assert "maf-e2e regression --target-repo . --environment staging" in workflow
    assert "MAF_E2E_MODEL_" not in workflow
    assert "AZURE_OPENAI" not in workflow
    assert "GEMINI" not in workflow


def test_container_defaults_run_regression_not_legacy_autonomous_flow() -> None:
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")
    launcher = (REPOSITORY_ROOT / "scripts" / "e2e-compose").read_text(encoding="utf-8")

    assert (
        'CMD ["maf-e2e", "regression", "--target-repo", "/app", "--environment", "staging"]'
        in dockerfile
    )
    assert 'CMD ["maf-e2e"]' not in dockerfile
    assert "e2e:/app/.maf-e2e/regression/." in launcher


def test_azure_systemd_timer_runs_regression_command() -> None:
    template = (REPOSITORY_ROOT / "infra" / "main.bicep").read_text(encoding="utf-8")

    assert "Description=MAF fixed-code E2E regression testing" in template
    assert "maf-e2e regression --target-repo /app --environment staging" in template
