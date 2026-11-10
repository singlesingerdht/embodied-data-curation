"""Current EDC wire identities. Legacy schemas are intentionally unsupported."""

from collections.abc import Mapping

RECORD = "edc_record_v1"
CATALOG = "edc_catalog_v1"
RUN = "edc_run_v1"
RECOMMENDATIONS = "edc_recommendations_v1"
RECOMMENDATION_POOL = "edc_recommendation_pool_v1"
REVIEW = "edc_review_v1"
DATASET_REFERENCE = "edc_dataset_ref_v1"
MEDIA_CACHE = "edc_media_cache_v1"
CHANNEL_CATALOG = "edc_channel_catalog_v1"
UNITS = "edc_units_v1"
REGENERATE_MANIFEST = "edc_regenerate_manifest_v1"
SOURCE_MAPPING = "edc_source_mapping_v1"
NUMERIC_COVERAGE = "edc_numeric_coverage_v1"


def require_schema(value, expected):
    """Reject old, unknown, or absent wire identities before consuming an artifact."""
    actual = value.get("schema_version") if isinstance(value, Mapping) else None
    if actual != expected:
        raise ValueError(f"Unsupported schema {actual!r}; expected {expected}")
    return value
