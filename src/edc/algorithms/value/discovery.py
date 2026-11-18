"""Discover repository-owned value algorithms without loading model runtimes."""
from importlib import import_module
from pathlib import Path


def builtins():
    root = Path(__file__).parent / 'algorithms'
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or not (directory / 'algorithm.py').is_file():
            continue
        plugin = import_module(f'edc.algorithms.value.algorithms.{directory.name}.algorithm').plugin
        if plugin.spec.id != directory.name or plugin.spec.category != 'value':
            raise ValueError(f'Built-in value directory/ID/category mismatch: {directory.name}')
        yield plugin
