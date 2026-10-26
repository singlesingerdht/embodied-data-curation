"""Compatibility class backed by the shared structured reader."""
from .single import SingleEpisodeReader

class Hdf5Reader(SingleEpisodeReader):
    format = "hdf5"
