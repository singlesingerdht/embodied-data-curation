"""Compatibility class backed by the shared structured reader."""
from .single import SingleEpisodeReader

class ParquetReader(SingleEpisodeReader):
    format = "parquet"
