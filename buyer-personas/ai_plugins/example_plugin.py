"""Example custom AI plugin.

Run with:  python -m personas run ... --ai file:ai_plugins/example_plugin.py

A plugin receives a system prompt and a prompt that contains ONLY aggregate
persona facts (no customer names, IDs, or emails). Return the narrative text,
or None to fall back to the built-in template.

This example points at any OpenAI-compatible server on your machine, such as
LM Studio (default port 1234) or vLLM, and uses a model name you choose.
Swap in any other backend: a different API, a fine-tuned model, a rules engine.
"""

from personas.plugins import AIProvider, OpenAICompatibleProvider


class LocalLLMPlugin(AIProvider):
    name = "local-llm-example"

    def __init__(self):
        self._backend = OpenAICompatibleProvider(model="your-local-model-name")

    def generate(self, system: str, prompt: str) -> str | None:
        return self._backend.generate(system, prompt)


PROVIDER = LocalLLMPlugin()
