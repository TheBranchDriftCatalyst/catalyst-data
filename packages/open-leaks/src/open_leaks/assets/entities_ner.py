"""Silver: Named Entity Recognition via LLM."""

from dagster_io import make_ner_asset

leak_entities = make_ner_asset(
    group_name="leaks",
    code_location="open_leaks",
    input_key="leak_chunks",
    asset_name="leak_entities",
)
