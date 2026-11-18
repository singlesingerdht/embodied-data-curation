import dataclasses
import pytest
from edc.algorithms.registry import Registry,algorithms
from edc.algorithms.quality.adapters.cpu import builtins as quality_builtins
from edc.algorithms.value import discovery


def test_quality_order_and_two_unique_value_methods():
    expected=[p.spec.id for p in quality_builtins()]
    registry=algorithms()
    assert list(registry.plugins)[:9]==expected
    owned=[p.spec.id for p in discovery.builtins()]
    assert owned==sorted(['Initial_State_Distribution','Progress_Stagnation_Attribution'])
    assert 'edc__Initial_State_Distribution' not in registry.plugins


def test_builtin_directory_mismatch_and_duplicate_fail(monkeypatch):
    from types import SimpleNamespace as NS
    plugin=next(discovery.builtins())
    bad=NS(spec=dataclasses.replace(plugin.spec,id='wrong'))
    monkeypatch.setattr(discovery,'import_module',lambda name:NS(plugin=bad))
    with pytest.raises(ValueError,match='directory/ID/category'):list(discovery.builtins())
    registry=Registry("edc.algorithms");registry.register(plugin)
    with pytest.raises(ValueError,match='Duplicate|duplicate'):registry.register(plugin)
