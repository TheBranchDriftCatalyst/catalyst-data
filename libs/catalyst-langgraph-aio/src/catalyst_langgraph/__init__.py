"""LLM clients + prompt loader for the extraction stack.

The package was originally a hardcoded NER→SPO LangGraph (deprecated and
removed under CD-ys8n). What remains are the reusable building blocks
that ``catalyst-exgraph`` and ``dagster_io`` consume:

- ``catalyst_langgraph.clients.{llm,mcp,gliner,nuextract,universalner}``
- ``catalyst_langgraph.prompts.{load_prompt, parse_prompt_file}``

The package name is kept for now to avoid an across-the-codebase
import-rename. Tracked separately under CD-satm if we want to rehome
these modules into ``dagster_io`` or a new ``catalyst-llm-clients`` lib.
"""
