"""Unit tests for anomaly detection logic."""

import pytest
from db import _detect_anomalies, PRICE_FLOOR, PRICE_CEILING, MAX_CHANGE_PCT


class TestNormalPrices:
    """Prices within normal range should not be flagged."""

    def test_normal_e10_price(self):
        assert _detect_anomalies(149.9, "E10", None) is None

    def test_normal_diesel_price(self):
        assert _detect_anomalies(155.9, "B7_STANDARD", None) is None

    def test_price_at_floor_boundary(self):
        assert _detect_anomalies(PRICE_FLOOR, "E10", None) is None

    def test_price_at_ceiling_boundary(self):
        assert _detect_anomalies(PRICE_CEILING, "E10", None) is None

    def test_small_price_change(self):
        """A 2p change from 150 is normal (~1.3%)."""
        assert _detect_anomalies(152.0, "E10", 150.0) is None

    def test_moderate_price_change(self):
        """A 15p change from 150 is 10% — within threshold."""
        assert _detect_anomalies(165.0, "E10", 150.0) is None

    def test_first_observation_normal_price(self):
        """No previous price — should not flag a normal price."""
        assert _detect_anomalies(139.9, "E10", None) is None


class TestBelowFloor:
    """Prices below the floor should be flagged."""

    def test_very_low_price(self):
        flags = _detect_anomalies(1.5, "E10", None)
        assert flags is not None
        assert any("price_below_floor" in f for f in flags)

    def test_just_below_floor(self):
        flags = _detect_anomalies(79.9, "E10", None)
        assert flags is not None
        assert any("price_below_floor" in f for f in flags)

    def test_zero_price(self):
        flags = _detect_anomalies(0.0, "E10", None)
        assert flags is not None
        assert any("price_below_floor" in f for f in flags)


class TestAboveCeiling:
    """Prices above the ceiling should be flagged."""

    def test_very_high_price(self):
        flags = _detect_anomalies(1599.0, "B7_STANDARD", None)
        assert flags is not None
        assert any("price_above_ceiling" in f for f in flags)

    def test_just_above_ceiling(self):
        flags = _detect_anomalies(300.1, "E10", None)
        assert flags is not None
        assert any("price_above_ceiling" in f for f in flags)


class TestDecimalErrors:
    """Likely decimal place errors (pounds entered as pence or vice versa)."""

    def test_price_in_pounds_instead_of_pence(self):
        """14.9 looks like £1.49 entered as 14.9p — x10 = 149, in range."""
        flags = _detect_anomalies(14.9, "E10", None)
        assert flags is not None
        assert any("likely_decimal_error" in f for f in flags)
        assert any("x10" in f for f in flags)

    def test_price_ten_times_too_high(self):
        """1499.0 looks like 149.9 entered as 1499.0 — /10 = 149.9, in range."""
        flags = _detect_anomalies(1499.0, "E10", None)
        assert flags is not None
        assert any("likely_decimal_error" in f for f in flags)
        assert any("div10" in f for f in flags)

    def test_very_low_not_decimal_error(self):
        """0.5p — even x10 is only 5p, still below floor. Not a decimal error."""
        flags = _detect_anomalies(0.5, "E10", None)
        assert flags is not None
        assert not any("likely_decimal_error" in f for f in flags)


class TestLargeJumps:
    """Large price changes from previous value."""

    def test_price_doubles(self):
        """100% increase should be flagged."""
        flags = _detect_anomalies(200.0, "E10", 100.0)
        assert flags is not None
        assert any("large_change" in f for f in flags)

    def test_price_halves(self):
        """50% decrease should be flagged."""
        flags = _detect_anomalies(75.0, "E10", 150.0)
        assert flags is not None
        assert any("large_change" in f for f in flags)

    def test_just_over_threshold(self):
        """Change of exactly MAX_CHANGE_PCT + 1% should be flagged."""
        prev = 100.0
        new_price = prev * (1 + (float(MAX_CHANGE_PCT) + 1) / 100)
        flags = _detect_anomalies(new_price, "E10", prev)
        assert flags is not None
        assert any("large_change" in f for f in flags)

    def test_just_under_threshold(self):
        """Change of MAX_CHANGE_PCT - 1% should NOT be flagged."""
        prev = 100.0
        new_price = prev * (1 + (float(MAX_CHANGE_PCT) - 1) / 100)
        flags = _detect_anomalies(new_price, "E10", prev)
        # Should either be None or not contain large_change
        if flags is not None:
            assert not any("large_change" in f for f in flags)

    def test_no_previous_price(self):
        """No previous price means no jump check."""
        assert _detect_anomalies(149.9, "E10", None) is None

    def test_previous_price_zero(self):
        """Previous price of 0 should not cause division by zero."""
        # Should not crash; the function guards against previous_price > 0
        result = _detect_anomalies(149.9, "E10", 0)
        # No large_change flag since division is skipped
        if result is not None:
            assert not any("large_change" in f for f in result)


class TestMultipleFlags:
    """A single price can trigger multiple flags simultaneously."""

    def test_below_floor_and_decimal_error(self):
        """14.9p is below floor AND a likely decimal error."""
        flags = _detect_anomalies(14.9, "E10", None)
        assert flags is not None
        assert any("price_below_floor" in f for f in flags)
        assert any("likely_decimal_error" in f for f in flags)

    def test_below_floor_and_large_jump(self):
        """Price crashes from 150 to 15 — below floor + big jump + decimal error."""
        flags = _detect_anomalies(15.0, "E10", 150.0)
        assert flags is not None
        assert any("price_below_floor" in f for f in flags)
        assert any("large_change" in f for f in flags)
