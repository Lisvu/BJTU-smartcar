"""Message formatting shared by the BJTU communication nodes."""


def format_chatter_message(counter):
    """Return the chatter payload for a non-negative message counter."""
    if not isinstance(counter, int) or isinstance(counter, bool):
        raise TypeError('counter must be an integer')
    if counter < 0:
        raise ValueError('counter must be non-negative')
    return f'bjtu_chatter message {counter}'
