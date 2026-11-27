"""Explicit failures at the core/platform boundary."""


class CoreError(Exception):
    """A diagnosed failure suitable for display without a traceback."""


class RuntimeRequirementError(CoreError):
    pass


class ProtocolError(CoreError):
    pass


class AdapterError(CoreError):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code
