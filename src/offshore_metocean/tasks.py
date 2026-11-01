"""Cooperative cancellation at safe boundaries; never terminate a Qt worker forcibly."""


class TaskCancelled(Exception):
    """The user requested cancellation before the next safe processing step."""
