from __future__ import annotations


class ObtainerCliError(Exception):
    """Base error with a stable CLI code and exit code."""

    def __init__(self, error_code: str, message: str, *, exit_code: int = 2, hint: str = ""):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.exit_code = exit_code
        self.hint = hint


class CandidateNotEnoughError(ObtainerCliError):
    def __init__(self, requested: int, actual: int):
        super().__init__(
            "CANDIDATE_NOT_ENOUGH",
            f"Requested {requested} records but only {actual} matched.",
            exit_code=6,
            hint="Relax filters or use --allow-smaller.",
        )
