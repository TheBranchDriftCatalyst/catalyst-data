"""Dynamic partition definition for per-document media processing."""

from dagster import DynamicPartitionsDefinition

media_partitions = DynamicPartitionsDefinition(name="media_document")
