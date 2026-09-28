"""Production entrypoint: real providers, keys from environment (mounted from Secret Manager)."""
import os

from .app import create_app
from .providers import AnthropicProvider, OpenAICompatProvider

providers = {}
if os.environ.get("ANTHROPIC_API_KEY"):
    providers["anthropic"] = AnthropicProvider()
if os.environ.get("OPENAI_API_KEY"):
    providers["openai_compat"] = OpenAICompatProvider()

app = create_app(providers=providers, env=os.environ.get("ENV", "prod"))
