"""Sample rule tests of the fixture area stock (read by toolkit report, never run)."""


class TestStock:
    def test_stock_never_below_zero(self):
        """R-stock-001: a stock take below zero is refused."""
