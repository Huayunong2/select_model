"""Public exception hierarchy for callers and the CLI."""


class SelectModelError(Exception):
    """Base class for expected user-facing failures."""


class ValidationError(SelectModelError):
    """Input or configuration validation failed."""


class RoutingError(SelectModelError):
    """No safe route could be produced."""


class DispatchError(SelectModelError):
    """A handoff could not be dispatched safely."""
