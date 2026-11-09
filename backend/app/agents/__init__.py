"""Hub-and-spoke agents.

Hub: the Gemini Live voice model. It talks to the user and routes each request
to one tool from the registry in app/tools. It never runs anything itself.

Spokes, each behind a tool (so the same confirmation guardrail and audit log
apply to all of them):
* memory researcher (this package, Google ADK + text model): `ask_memory`
* memory store (app/memory, no LLM): `remember`, `recall`, `search_documents`, ...
* PC (app/tools/pc.py, system.py, screen.py): Windows settings and programs
* Gmail (app/tools/gmail.py): reading, and sending only after voice confirmation
* Claude Code (app/tools/claude.py): coding, screen tasks and learning new skills
"""

from google.adk.models import Gemini
from google.genai import Client

from ..config import Settings
from ..memory import Memory
from .researcher import MemoryResearcher, ResearchResult

__all__ = ["MemoryResearcher", "ResearchResult", "researcher_from_settings"]


def researcher_from_settings(
    settings: Settings, memory: Memory, client: Client
) -> MemoryResearcher:
    model = Gemini(model=settings.agent_model, client=client)
    return MemoryResearcher(memory, model, settings.memory_top_k)
