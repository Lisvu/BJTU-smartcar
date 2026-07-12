"""ROS-independent battery status classification."""


def classify_battery(voltage, low_voltage, critical_voltage):
    """Return the alert level and message for a measured battery voltage."""
    if critical_voltage > low_voltage:
        raise ValueError('critical voltage must not exceed low voltage')

    if voltage <= critical_voltage:
        return (
            'critical',
            'Battery critical: %.2f volts. Stop and charge now.' % voltage,
        )
    if voltage <= low_voltage:
        return 'low', 'Battery low: %.2f volts. Please charge soon.' % voltage
    return 'ok', 'Battery voltage normal: %.2f volts.' % voltage
