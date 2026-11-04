"""Errors retain structured API details and uncertain operation outcomes."""


class AnkiError(RuntimeError):
    """An API request or its completion was not confirmed as successful."""

    def __init__(
        self,
        message: str,
        *,
        method: str | None = None,
        code: int | None = None,
        data: object = None,
        result: object = None,
        operation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.method = method
        self.code = code
        self.data = data
        self.result = result
        self.operation_id = operation_id

    def as_dict(self) -> dict[str, object]:
        output: dict[str, object] = {"error": str(self)}
        for key in ("method", "code", "data", "result", "operation_id"):
            value = getattr(self, key)
            if value is not None:
                output[key] = value
        return output


class OperationTimeout(AnkiError):
    """The operation is still pending; the server has not been cancelled."""
