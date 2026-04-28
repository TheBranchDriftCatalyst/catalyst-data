"""Ensemble extraction — run N models, merge by consensus.

EnsembleExtractNode replaces a single ExtractNode when StageConfig
has ensemble_models set. It runs each model sequentially, collects
all results, and merges by consensus voting.

ConsensusVoter implements voting strategies:
- majority: item accepted if >= threshold fraction of models agree
- unanimous: all models must agree
- any: any single model suffices (union)
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from catalyst_exgraph.config import StageConfig
from catalyst_exgraph.protocol import ExtractionClient
from catalyst_exgraph.state import ExGraphState, ExGraphStatus

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Consensus Voter
# ═══════════════════════════════════════════════════════════════════════════


def _normalize_key_ner(item: dict) -> str:
    """Normalize a mention for dedup/voting: lowercase text + type."""
    return f"{item.get('text', '').strip().lower()}|{item.get('mention_type', '').upper()}"


def _normalize_key_spo(item: dict) -> str:
    """Normalize a proposition for dedup/voting."""
    subj = item.get("subject", item.get("subject_text", "")).strip().lower()
    pred = item.get("predicate", "").strip().lower()
    obj = item.get("object", item.get("object_text", "")).strip().lower()
    return f"{subj}|{pred}|{obj}"


class ConsensusVoter:
    """Merges extraction results from N models by consensus voting.

    Args:
        strategy: 'majority', 'unanimous', or 'any'
        threshold: fraction of models that must agree (for majority)
        kind: 'ner' or 'spo' (determines normalization key)
    """

    def __init__(
        self,
        strategy: str = "majority",
        threshold: float = 0.5,
        kind: str = "ner",
    ) -> None:
        self.strategy = strategy
        self.threshold = threshold
        self._normalize = _normalize_key_ner if kind == "ner" else _normalize_key_spo

    def vote(
        self,
        results_per_model: dict[str, list[dict]],
    ) -> list[dict]:
        """Vote on which items to accept.

        Args:
            results_per_model: {model_name: [item_dicts]}

        Returns:
            Accepted items with consensus_score and contributing_models metadata.
        """
        n_models = len(results_per_model)
        if n_models == 0:
            return []

        # Group items by normalized key
        votes: dict[str, dict] = {}  # key -> {item, models, count}
        for model_name, items in results_per_model.items():
            for item in items:
                key = self._normalize(item)
                if key not in votes:
                    votes[key] = {
                        "item": item,
                        "models": [],
                        "count": 0,
                    }
                votes[key]["models"].append(model_name)
                votes[key]["count"] += 1
                # Prefer item with highest confidence
                existing_conf = votes[key]["item"].get("confidence", 0)
                new_conf = item.get("confidence", 0)
                if new_conf > existing_conf:
                    votes[key]["item"] = item

        # Apply voting strategy
        accepted = []
        for _key, info in votes.items():
            consensus_score = info["count"] / n_models
            passes = False

            if self.strategy == "unanimous":
                passes = info["count"] == n_models
            elif self.strategy == "any":
                passes = info["count"] >= 1
            else:  # majority
                passes = consensus_score >= self.threshold

            if passes:
                item = dict(info["item"])
                item["consensus_score"] = round(consensus_score, 3)
                item["contributing_models"] = info["models"]
                item["ensemble_size"] = n_models
                accepted.append(item)

        logger.info(
            "consensus: strategy=%s, threshold=%.2f, models=%d, candidates=%d, accepted=%d",
            self.strategy,
            self.threshold,
            n_models,
            len(votes),
            len(accepted),
        )
        return accepted


# ═══════════════════════════════════════════════════════════════════════════
# Ensemble Extract Node
# ═══════════════════════════════════════════════════════════════════════════


class EnsembleExtractNode:
    """Runs N extraction clients on the same text, merges by consensus.

    Replaces a single ExtractNode when config.ensemble_models is set.
    Runs models sequentially to avoid RAM pressure on local inference.
    """

    def __init__(
        self,
        config: StageConfig,
        clients: dict[str, ExtractionClient],
    ) -> None:
        self.config = config
        self.clients = clients  # model_name -> client

    async def __call__(self, state: ExGraphState) -> dict[str, Any]:
        raw_text = state.get("raw_text", "")
        stage_name = self.config.stage_name
        node_name = f"ensemble_extract_{stage_name}"

        logger.info(
            "%s: start, input_len=%d, models=%d",
            node_name,
            len(raw_text),
            len(self.clients),
        )
        t0 = time.perf_counter()

        # Load prompt
        from catalyst_exgraph.nodes.extract import _load_prompt

        system = _load_prompt(self.config)

        # Build prompt (same as ExtractNode)
        import json

        if self.config.stage_name == "spo":
            upstream = state.get("upstream_context", {})
            accepted_mentions = upstream.get("accepted_mentions", [])
            prompt = f"Accepted mentions:\n{json.dumps(accepted_mentions, indent=2)}\n\nText:\n{raw_text}"
        else:
            prompt = raw_text

        messages = [SystemMessage(content=system), HumanMessage(content=prompt)]

        # Run each model sequentially
        results_per_model: dict[str, list[dict]] = {}
        model_errors: list[str] = []

        for model_name, client in self.clients.items():
            try:
                logger.info("%s: running model %s", node_name, model_name)
                result = await client.structured_output(self.config.extraction_schema, messages)

                candidates = []
                for field_name in ("mentions", "propositions"):
                    items = getattr(result, field_name, None)
                    if items is not None:
                        candidates = [item.model_dump() for item in items]
                        break

                # Tag each candidate with its source model
                for c in candidates:
                    c["_source_model"] = model_name

                results_per_model[model_name] = candidates
                logger.info("%s: model %s produced %d candidates", node_name, model_name, len(candidates))

            except Exception as e:
                logger.warning("%s: model %s failed: %s", node_name, model_name, e)
                model_errors.append(f"{model_name}: {e}")
                results_per_model[model_name] = []

        # Vote on consensus
        voter = ConsensusVoter(
            strategy=self.config.consensus_strategy,
            threshold=self.config.consensus_threshold,
            kind=self.config.stage_name,
        )
        consensus_items = voter.vote(results_per_model)

        elapsed = time.perf_counter() - t0
        logger.info(
            "%s: done, total_candidates=%d, consensus=%d, duration=%.3fs",
            node_name,
            sum(len(v) for v in results_per_model.values()),
            len(consensus_items),
            elapsed,
        )

        # Write to stage state
        stages = dict(state.get("stages", {}))
        stages[stage_name] = {
            "candidates": consensus_items,
            "accepted": [],
            "validation": {},
            "retry_count": 0,
            "status": "validating" if consensus_items else "completed",
            "error": "",
        }

        if not consensus_items:
            stages[stage_name]["accepted"] = []

        audit = {
            "timestamp": datetime.now(UTC).isoformat(),
            "node_name": node_name,
            "status": "completed",
            "duration_s": elapsed,
            "details": {
                "models": list(self.clients.keys()),
                "per_model_counts": {k: len(v) for k, v in results_per_model.items()},
                "consensus_count": len(consensus_items),
                "strategy": self.config.consensus_strategy,
                "threshold": self.config.consensus_threshold,
                "errors": model_errors,
            },
        }

        return {
            "stages": stages,
            "status": ExGraphStatus.VALIDATING.value if consensus_items else ExGraphStatus.COMPLETED.value,
            "audit_events": state.get("audit_events", []) + [audit],
        }


# ═══════════════════════════════════════════════════════════════════════════
# Convenience: run ensemble as a standalone function
# ═══════════════════════════════════════════════════════════════════════════


async def run_ensemble_extraction(
    text: str,
    source_metadata: dict,
    config: StageConfig,
    clients: dict[str, ExtractionClient],
    mcp_client: Any,
) -> list[dict]:
    """Run ensemble extraction on a single text, return consensus items.

    Convenience function for benchmarking and ground truth generation.
    """
    # Run the ensemble extract node directly
    ensemble_node = EnsembleExtractNode(config, clients)
    state: ExGraphState = {
        "raw_text": text,
        "source_metadata": source_metadata,
        "stages": {},
        "upstream_context": {},
        "max_retries": config.max_retries,
        "audit_events": [],
        "status": "pending",
    }
    result = await ensemble_node(state)
    return result.get("stages", {}).get(config.stage_name, {}).get("candidates", [])
