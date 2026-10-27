"""EDC: Embodied Data Curation. Public API with lazy dependencies."""
from importlib import import_module
__version__ = "0.2.0a2"
_EXPORTS = {'open_dataset': 'edc.data.ir.dataset', 'run': 'edc.execution.runner', 'Context': 'edc.execution.runner', 'UnitInputError': 'edc.data.ir.units', 'AlgorithmSpec': 'edc.algorithms.registry', 'Registry': 'edc.algorithms.registry', 'recommend': 'edc.algorithms.recommendations', 'convert_to_lerobot': 'edc.data.conversion.lerobot', 'channel_catalog': 'edc.algorithms.channel_names', 'resolve_channel': 'edc.algorithms.channel_names', 'validate_workflow': 'edc.workflows.validation', 'run_workflow': 'edc.workflows.engine', 'dataset_reference': 'edc.workflows.references', 'open_dataset_reference': 'edc.workflows.references', 'review': 'edc.workflows.reviews'}
_EXPORTS.update({name: 'edc.configuration' for name in ('load_config', 'export_config', 'open_config_dataset', 'recommend_config')})
_EXPORTS['load_channel_registry'] = 'edc.data.channel_registry'
_EXPORTS['convert_orientation'] = 'edc.data.transforms.orientation'
_EXPORTS.update({name: 'edc.data.ir.canonical' for name in
                 ('open_dataset', 'open_source_dataset', 'open_dataset_reference', 'OrientationInputError')})
__all__ = list(_EXPORTS) + ["__version__"]

def __getattr__(name):
    if name in _EXPORTS:
        value = getattr(import_module(_EXPORTS[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(name)
