"""Compatibility export; implementation lives in Quality_Assessment.adapters.cpu."""
from importlib import import_module as _import_module
import sys as _sys
_sys.modules[__name__] = _import_module("Quality_Assessment.adapters.cpu")
