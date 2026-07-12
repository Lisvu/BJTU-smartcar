"""Tests for ROS-independent battery status classification."""

import pytest

from battery_status import classify_battery


@pytest.mark.parametrize(
    ('voltage', 'expected_level', 'expected_text'),
    [
        (12.0, 'ok', 'Battery voltage normal: 12.00 volts.'),
        (11.0, 'low', 'Battery low: 11.00 volts. Please charge soon.'),
        (10.8, 'low', 'Battery low: 10.80 volts. Please charge soon.'),
        (
            10.6,
            'critical',
            'Battery critical: 10.60 volts. Stop and charge now.',
        ),
        (
            9.5,
            'critical',
            'Battery critical: 9.50 volts. Stop and charge now.',
        ),
    ],
)
def test_classify_battery(voltage, expected_level, expected_text):
    assert classify_battery(voltage, 11.0, 10.6) == (
        expected_level,
        expected_text,
    )


def test_classify_battery_rejects_reversed_thresholds():
    with pytest.raises(ValueError, match='must not exceed'):
        classify_battery(11.0, 10.5, 11.0)
