class ProcessingError(Exception):
    """
    A user-facing processing failure. The message on this exception is safe
    to display directly in the UI - never put a raw traceback or internal
    path into it. Anything unexpected should be caught, logged with
    exc_info, and re-raised as a generic ProcessingError instead.
    """
    pass
