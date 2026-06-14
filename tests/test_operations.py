from pathlib import Path


def test_compose_passes_env_file_and_marks_container_runtime() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "env_file:" in compose
    assert "MAF_E2E_RUNTIME_ENVIRONMENT: container" in compose


def test_example_env_documents_windows_compatible_azure_api_key() -> None:
    example = Path(".env.example").read_text(encoding="utf-8")

    assert "MAF_E2E_MODEL_AUTH=entra_id" in example
    assert "MAF_E2E_AZURE_OPENAI_API_KEY=" in example
