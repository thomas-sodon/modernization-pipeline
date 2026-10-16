"""The equivalence runner: one test per selected manifest unit, parametrised by the plugin
(`-p equivalence.plugin`). Collected only when the plugin is active (see conftest.py)."""


def test_unit(eq_runner, eq_case, eq_adapter, eq_golden, request):
    eq_runner.check(eq_case, eq_adapter, eq_golden, request)
