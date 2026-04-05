"""Gold: Subject-Predicate-Object proposition extraction via LLM."""

from dagster_io import make_proposition_asset

leak_propositions = make_proposition_asset(
    group_name="leaks",
    code_location="open_leaks",
    input_key="leak_chunks",
    asset_name="leak_propositions",
)
