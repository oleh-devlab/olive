class ScheduleValidationError(Exception):
    """Exception raised for validation errors in schedule tasks."""


class ScheduleLimitError(ScheduleValidationError):
    """The user already has as many items of a kind as they may."""
