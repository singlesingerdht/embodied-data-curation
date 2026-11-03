"""EDC: Embodied Data Curation. Public API with lazy dependencies."""
from importlib import import_module
__version__ = "0.3.0a1"
_EXPORTS = {'open_dataset': 'edc.data.ir.canonical', 'run': 'edc.execution.runner', 'Context': 'edc.execution.runner',
            'AlgorithmSpec': 'edc.algorithms.registry', 'Registry': 'edc.algorithms.registry',
            'QualityAlgorithm': 'edc.algorithms.base', 'Item': 'edc.algorithms.base',
            'AlgorithmResult': 'edc.algorithms.base', 'decide': 'edc.algorithms.base',
            'compile_card': 'edc.robots.card', 'UnitInputError': 'edc.data.ir.units',
            'recommend': 'edc.algorithms.recommendations', 'channel_catalog': 'edc.algorithms.channel_names',
            'resolve_channel': 'edc.algorithms.channel_names', 'load_channel_registry': 'edc.data.channel_registry',
            'convert_orientation': 'edc.data.transforms.orientation', 'dataset_reference': 'edc.workflows.references',
            'open_source_dataset': 'edc.data.ir.canonical', 'open_dataset_reference': 'edc.data.ir.canonical',
            'OrientationInputError': 'edc.data.ir.canonical'}
__all__ = list(_EXPORTS) + ["__version__"]


def __getattr__(name):
    if name in _EXPORTS:
        value = getattr(import_module(_EXPORTS[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(name)
