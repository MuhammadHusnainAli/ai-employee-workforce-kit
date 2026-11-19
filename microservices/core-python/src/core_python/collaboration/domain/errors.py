"""What can go wrong with a conversation, independent of transport."""


class NotFound(LookupError):
    pass


class Invalid(ValueError):
    pass


class Closed(Exception):
    """The conversation is closed: nothing more is said in it."""


class NotAParticipant(Exception):
    pass
