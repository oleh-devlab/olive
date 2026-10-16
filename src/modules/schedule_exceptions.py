class ScheduleValidationError(Exception):
    """Exception raised for validation errors in schedule tasks."""


class ScheduleLimitError(ScheduleValidationError):
    """The user already has as many items of a kind as they may."""


class ScheduleItemNotFoundError(ValueError):
    """No item with that id. A `ValueError`, which is what the provider raised for it before."""
