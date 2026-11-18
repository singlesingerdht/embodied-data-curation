"""Checkpoint-local configuration for vLLM; never mutate Transformers classes."""
from functools import partial
from importlib import metadata
import json
from pathlib import Path


def check_versions():
    from packaging.version import Version
    transformers = Version(metadata.version('transformers'))
    vllm = Version(metadata.version('vllm'))
    if vllm.release[:2] == (0, 11) and transformers.major >= 5:
        raise ImportError('vLLM 0.11 requires the GRM Transformers 4.57 worker; '
                          'use edc env create vision --with progress. '
                          'Transformers 5 class patches are not supported.')


def _apply_tie(config, *, tie):
    config.tie_word_embeddings = tie
    config.text_config.tie_word_embeddings = tie
    return config


def checkpoint_overrides(model_path):
    raw = json.loads((Path(model_path) / 'config.json').read_text())
    values = [obj['tie_word_embeddings'] for obj in (raw, raw.get('text_config', {}))
              if 'tie_word_embeddings' in obj]
    if not values or any(type(value) is not bool for value in values) or len(set(values)) != 1:
        raise ValueError('Checkpoint must declare an unambiguous boolean tie_word_embeddings')
    # vLLM 0.11 reads the text config when deciding whether to skip lm_head weights.
    # Both shared and independent heads are valid, according to the checkpoint.
    return partial(_apply_tie, tie=values[0])
