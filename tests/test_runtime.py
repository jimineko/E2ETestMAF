from agent_framework.openai import OpenAIChatClient
from agent_framework_gemini import GeminiChatClient
from azure.identity.aio import DefaultAzureCredential

from maf_qa.config import Settings
from maf_qa.runtime import build_chat_client


async def test_builds_azure_openai_client() -> None:
    credential = DefaultAzureCredential()
    try:
        settings = Settings(
            _env_file=None,
            model_provider="azure_openai",
            azure_openai_endpoint="https://example.openai.azure.com",
            azure_openai_deployment="test-model",
        )

        client = build_chat_client(settings, credential)

        assert isinstance(client, OpenAIChatClient)
    finally:
        await credential.close()


async def test_builds_gemini_client() -> None:
    settings = Settings(
        _env_file=None,
        model_provider="gemini",
        gemini_api_key="secret-key",
        gemini_model="gemini-2.5-flash-lite",
    )

    client = build_chat_client(settings, None)

    assert isinstance(client, GeminiChatClient)
    assert client.model == "gemini-2.5-flash-lite"
