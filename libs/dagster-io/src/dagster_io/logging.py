import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional


class JsonFormatter(logging.Formatter):
    """JSON log formatter compatible with Loki/Grafana.

    Outputs structured JSON with fields:
    timestamp, level, logger, message, module, function, line
    Plus optional: dagster_run_id, asset_key, code_location, step_key, exc_info
    """

    def format(self, record):
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        # Add Dagster context fields if present
        for key in ("dagster_run_id", "asset_key", "code_location", "step_key"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        # Add any extra fields passed via logger.info("msg", extra={...})
        for key, val in record.__dict__.items():
            if key.startswith("_") or key in log_entry or key in (
                "name", "msg", "args", "created", "relativeCreated",
                "exc_info", "exc_text", "stack_info", "levelname", "levelno",
                "pathname", "filename", "module", "funcName", "lineno",
                "msecs", "thread", "threadName", "process", "processName",
                "taskName", "message",
            ):
                continue
            log_entry[key] = val
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, default=str)


class ModuleFilter(logging.Filter):
    """Filter that enables/disables logging by module path.

    Supports:
    - Disabling entire module trees (e.g., 'open_leaks.assets.extraction')
    - Per-module level overrides (e.g., 'dagster_io.llm' -> WARNING)
    """

    def __init__(self, disabled_modules: list[str] | None = None,
                 module_levels: dict[str, int] | None = None):
        super().__init__()
        self.disabled_modules = disabled_modules or []
        self.module_levels = module_levels or {}

    def filter(self, record: logging.LogRecord) -> bool:
        # Check disabled modules (most specific match wins)
        for mod in self.disabled_modules:
            if record.name == mod or record.name.startswith(mod + "."):
                return False

        # Check per-module level overrides (most specific match wins)
        best_match = ""
        best_level = None
        for mod, level in self.module_levels.items():
            if (record.name == mod or record.name.startswith(mod + ".")) and len(mod) > len(best_match):
                best_match = mod
                best_level = level

        if best_level is not None:
            return record.levelno >= best_level

        return True


def _parse_disabled_modules(env_val: str | None) -> list[str]:
    if not env_val:
        return []
    return [m.strip() for m in env_val.split(",") if m.strip()]


def _parse_module_levels(env_val: str | None) -> dict[str, int]:
    if not env_val:
        return {}
    result = {}
    for pair in env_val.split(","):
        pair = pair.strip()
        if "=" not in pair:
            continue
        mod, level_str = pair.split("=", 1)
        level = getattr(logging, level_str.strip().upper(), None)
        if level is not None:
            result[mod.strip()] = level
    return result


_configured = False


def configure_logging(
    level: str | None = None,
    log_format: str | None = None,
    disabled_modules: str | None = None,
    module_levels: str | None = None,
) -> None:
    """Configure structured logging for catalyst-data pipelines.

    All parameters fall back to environment variables:
    - LOG_LEVEL: Global log level (default: DEBUG)
    - LOG_FORMAT: 'json' or 'text' (default: json when KUBERNETES_SERVICE_HOST set, text otherwise)
    - LOG_DISABLED_MODULES: Comma-separated module paths to silence
      Example: open_leaks.assets.extraction,dagster_io.s3_client
    - LOG_MODULE_LEVELS: Comma-separated module=LEVEL pairs
      Example: open_leaks.assets=WARNING,dagster_io.llm=INFO

    Can be called multiple times safely (idempotent after first call unless forced).
    """
    global _configured
    if _configured:
        return
    _configured = True

    # Resolve config from args or env
    level = level or os.getenv("LOG_LEVEL", "DEBUG")
    is_k8s = os.getenv("KUBERNETES_SERVICE_HOST") is not None
    log_format = log_format or os.getenv("LOG_FORMAT", "json" if is_k8s else "text")
    disabled = _parse_disabled_modules(disabled_modules or os.getenv("LOG_DISABLED_MODULES"))
    mod_levels = _parse_module_levels(module_levels or os.getenv("LOG_MODULE_LEVELS"))

    # Create root handler
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.DEBUG))

    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Create handler with appropriate formatter
    handler = logging.StreamHandler(sys.stderr)
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-8s] %(name)-40s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))

    # Add module filter
    if disabled or mod_levels:
        handler.addFilter(ModuleFilter(disabled_modules=disabled, module_levels=mod_levels))

    root_logger.addHandler(handler)

    # Quiet noisy third-party loggers
    for noisy in ("urllib3", "botocore", "boto3", "s3transfer", "httpx", "httpcore",
                   "openai", "langchain", "chromadb", "fsspec", "aiobotocore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger for the given module name.

    Usage:
        logger = get_logger(__name__)
        logger.debug("Processing record %s", record_id)
        logger.info("Asset materialized", extra={"asset_key": "my_asset", "record_count": 42})
    """
    return logging.getLogger(name)
