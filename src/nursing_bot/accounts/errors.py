"""User-facing account errors. Each carries the HTTP status the API answers with."""


class AccountError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidEmailError(AccountError):
    status_code = 422


class WeakPasswordError(AccountError):
    """The password does not meet the policy; the message says why."""

    status_code = 422


class EmailTakenError(AccountError):
    status_code = 409


class InvalidCredentialsError(AccountError):
    status_code = 401


class AccountLockedError(AccountError):
    status_code = 429

    def __init__(self, message: str, retry_after_seconds: int):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class AccountDisabledError(AccountError):
    status_code = 403


class UserNotFoundError(AccountError):
    status_code = 404
