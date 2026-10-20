class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    def payload(self) -> dict:
        result = {"error_code": self.code, "message": self.message}
        if self.details is not None:
            result["details"] = self.details
        return result


def invalid_field(field: str, message: str):
    raise APIError(
        400, "VALIDATION_ERROR", "Invalid request", {"violations": [{"field": field, "message": message}]}
    )
