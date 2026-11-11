"""User-correctable errors shared by the HTTP and run lifecycle boundaries."""


class RunError(Exception):
    """Shown verbatim in the application."""
