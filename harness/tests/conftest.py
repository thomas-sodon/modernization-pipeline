"""The runner test_units.py is collected only when the equivalence plugin is active; the
harness's own tests run without it (and the plugin ignores them during cell runs)."""

from pathlib import Path


def pytest_ignore_collect(collection_path, config):
    if Path(collection_path).name == "test_units.py" and not (
        config.pluginmanager.has_plugin("equivalence-runner")
    ):
        return True
    return None
