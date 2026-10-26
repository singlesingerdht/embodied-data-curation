"""Public API only; implementation belongs to the repository framework."""
__version__ = "0.1.0a3"
from framework.ir.dataset import open_dataset
from framework.execution import run
from framework.ir.units import UnitInputError
from framework.recommendations import recommend
from framework.conversion.lerobot import convert_to_lerobot
from framework.channel_names import channel_catalog, resolve_channel
__all__ = ["open_dataset", "convert_to_lerobot", "run", "recommend", "channel_catalog", "resolve_channel", "UnitInputError", "__version__"]

from framework.workflows.validation import validate_workflow
from framework.workflows.engine import run_workflow
from framework.workflows.references import dataset_reference, open_dataset_reference
from framework.workflows.reviews import review
__all__ += ['validate_workflow', 'run_workflow', 'dataset_reference', 'open_dataset_reference', 'review']
