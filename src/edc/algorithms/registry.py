"""Public plugin metadata and entry-point discovery (interface version 1)."""
from dataclasses import dataclass, field
from importlib.metadata import entry_points
import re


@dataclass(frozen=True)
class AlgorithmSpec:
    id: str
    version: str
    name: str
    author: str
    description: str
    category: str = "quality"
    granularity: str = "episode"
    required_inputs: tuple[str, ...] = ()
    optional_inputs: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    device: str = "cpu"
    calibration: bool = False
    interface_version: int = 1
    parameters: dict = field(default_factory=dict)
    presets: tuple[str, ...] = ()
    # Explicit numeric bindings for plugin preflight; units must be SI.
    input_requirements: tuple[dict, ...] = ()
    parameter_schema: dict = field(default_factory=dict)
    data_requirements: tuple[dict, ...] = ()
    input_descriptions: tuple[str, ...] = ()
    example: bool = False
    episode_requirements: tuple[dict, ...] = ()


class Registry:
    def __init__(self, group):
        self.group = group
        self.plugins = {}
        # Import-time failures are isolated per entry point. Validation errors
        # raised after a module loads remain fatal and visible to the installer.
        self.discovery_errors = []

    def register(self, plugin):
        spec = plugin.spec
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", spec.id):
            raise ValueError(f"Unsafe plugin ID: {spec.id!r}")
        if spec.interface_version != 1:
            raise ValueError(f"Incompatible interface: {spec.id}: {spec.interface_version}")
        if spec.id in self.plugins:
            raise ValueError(f"Duplicate plugin ID: {spec.id}")
        if self.group == "edc.algorithms":
            from edc.algorithms.contracts import validate_schema, validate_requirements
            validate_schema(spec.parameter_schema)
            validate_requirements(spec.data_requirements, spec.parameter_schema)
            validate_requirements(spec.episode_requirements, spec.parameter_schema)
            if spec.episode_requirements and spec.granularity != 'dataset':
                raise ValueError('episode_requirements are for dataset plugin subset screening')
            if any(not isinstance(x, str) or not x.strip() for x in spec.input_descriptions):
                raise ValueError('input_descriptions must contain nonempty strings')
            from edc.data.ir.units import si_unit
            for requirement in spec.input_requirements:
                if (not isinstance(requirement, dict) or set(requirement) != {"channel", "field", "unit"}
                        or any(not isinstance(v, str) or not v for v in requirement.values())):
                    raise ValueError("input_requirements entries require channel, field and SI unit")
                if si_unit(requirement["unit"]) != requirement["unit"]:
                    raise ValueError("input_requirements unit must be SI")
            if spec.category not in {"quality", "value", "editing", "augmentation"}:
                raise ValueError(f"Invalid category: {spec.category}")
            if spec.granularity not in {"episode", "dataset"}:
                raise ValueError("This executor supports episode or dataset granularity")
            if not callable(getattr(plugin, "run", None)):
                raise ValueError(f"Plugin {spec.id} must implement run(context, params)")
            if spec.granularity == "dataset" and not callable(getattr(plugin, "preflight_dataset", None)):
                raise ValueError(f"Dataset plugin {spec.id} must implement preflight_dataset(dataset, params)")
            if spec.granularity == 'dataset' and (spec.required_inputs or spec.input_requirements or spec.data_requirements):
                raise ValueError('Dataset requirements must be checked in preflight_dataset; episode-scoped declarations are not supported')
        else:
            if not callable(getattr(plugin, "can_open", None)) or not callable(getattr(plugin, "open_dataset", None)):
                raise ValueError(f"Reader {spec.id} must implement can_open/open_dataset")
        self.plugins[spec.id] = plugin

    def discover(self):
        for point in sorted(entry_points(group=self.group), key=lambda p: p.name):
            try:
                plugin = point.load()
            except Exception as exc:
                self.discovery_errors.append({
                    "entry_point": point.name,
                    "status": "missing_dependency" if isinstance(exc, ImportError) else "processing_error",
                    "error_type": type(exc).__name__,
                    "reason": str(exc),
                })
                continue
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*__[A-Za-z][A-Za-z0-9_]*", plugin.spec.id):
                raise ValueError(f"External plugin ID must use team__algorithm: {plugin.spec.id}")
            self.register(plugin)
        return self


def algorithms():
    from edc.algorithms.quality.adapters.cpu import builtins, indicator_builtins
    registry = Registry("edc.algorithms")
    for plugin in builtins() + indicator_builtins():
        registry.register(plugin)
    return registry.discover()
