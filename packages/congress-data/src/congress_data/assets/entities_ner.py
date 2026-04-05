"""Silver: Named Entity Recognition (NER) extraction via LLM."""

from dagster_io import make_ner_asset

congress_entities = make_ner_asset(
    group_name="congress",
    code_location="congress_data",
    input_key="congress_chunks",
    asset_name="congress_entities",
)
