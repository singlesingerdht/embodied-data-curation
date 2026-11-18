import json
from types import SimpleNamespace

import pytest

from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution import model_config


@pytest.mark.parametrize('tie', [False, True])
def test_checkpoint_controls_head_without_class_or_other_instance_mutation(tmp_path, tie):
    class TextConfig:
        tie_word_embeddings = not tie
    (tmp_path / 'config.json').write_text(json.dumps({'tie_word_embeddings': tie, 'text_config': {}}))
    config = SimpleNamespace(text_config=TextConfig())
    unrelated = TextConfig()
    override = model_config.checkpoint_overrides(tmp_path)
    assert override(config) is config
    assert config.tie_word_embeddings is tie
    assert config.text_config.tie_word_embeddings is tie
    assert unrelated.tie_word_embeddings is not tie
    assert TextConfig.tie_word_embeddings is not tie


@pytest.mark.parametrize('config', [{}, {'tie_word_embeddings': 'false'},
    {'tie_word_embeddings': False, 'text_config': {'tie_word_embeddings': True}}])
def test_missing_or_conflicting_checkpoint_identity_fails_before_loading(tmp_path, config):
    (tmp_path / 'config.json').write_text(json.dumps(config))
    with pytest.raises(ValueError, match='unambiguous'):
        model_config.checkpoint_overrides(tmp_path)


def test_known_incompatible_versions_fail_without_patching(monkeypatch):
    monkeypatch.setattr(model_config.metadata, 'version',
                        lambda name: {'transformers': '5.5.4', 'vllm': '0.11.0'}[name])
    with pytest.raises(ImportError, match='Transformers 5 class patches'):
        model_config.check_versions()


def test_grm_passes_checkpoint_override_to_engine(tmp_path, monkeypatch):
    import sys
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.grm_inference import GRMInference
    calls = []
    (tmp_path / 'config.json').write_text(json.dumps({'tie_word_embeddings': False}))
    monkeypatch.setattr(model_config, 'check_versions', lambda: None)
    monkeypatch.setitem(sys.modules, 'vllm', SimpleNamespace(
        LLM=lambda **kw: calls.append(kw), SamplingParams=lambda **kw: kw))
    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(
        AutoProcessor=SimpleNamespace(from_pretrained=lambda *a, **kw: SimpleNamespace())))
    GRMInference(str(tmp_path))
    config = SimpleNamespace(text_config=SimpleNamespace(tie_word_embeddings=True))
    calls[0]['hf_overrides'](config)
    assert config.text_config.tie_word_embeddings is False
