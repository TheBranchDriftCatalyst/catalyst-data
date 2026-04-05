"""Gold: Subject-Predicate-Object proposition extraction via LLM."""

from dagster_io import make_proposition_asset

congress_propositions = make_proposition_asset(
    group_name="congress",
    code_location="congress_data",
    input_key="congress_chunks",
    asset_name="congress_propositions",
)
