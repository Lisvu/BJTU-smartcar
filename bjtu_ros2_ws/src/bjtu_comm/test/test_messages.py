"""Tests for ROS-independent message formatting."""

import pytest

from bjtu_comm.messages import format_chatter_message


@pytest.mark.parametrize(
    ('counter', 'expected'),
    [
        (0, 'bjtu_chatter message 0'),
        (42, 'bjtu_chatter message 42'),
    ],
)
def test_format_chatter_message(counter, expected):
    assert format_chatter_message(counter) == expected


@pytest.mark.parametrize('counter', [-1, -100])
def test_format_chatter_message_rejects_negative_counter(counter):
    with pytest.raises(ValueError, match='non-negative'):
        format_chatter_message(counter)


@pytest.mark.parametrize('counter', [True, 1.5, '1', None])
def test_format_chatter_message_requires_integer(counter):
    with pytest.raises(TypeError, match='integer'):
        format_chatter_message(counter)
