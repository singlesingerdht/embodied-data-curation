"""Algorithm discovery is available in the NumPy/YAML base install, before CPU extras."""
import subprocess
import sys


def test_builtin_specs_do_not_import_optional_execution_dependencies():
    result = subprocess.run([sys.executable, '-c', '''
import builtins
original = builtins.__import__
def checked(name, *args, **kwargs):
    if name.split('.')[0] in {'scipy', 'cv2', 'xxhash', 'torch'}:
        raise ModuleNotFoundError('Optional execution dependency unavailable: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = checked
from edc.algorithms.quality.adapters.cpu import builtins as plugins
assert len(plugins()) == 9
'''], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
