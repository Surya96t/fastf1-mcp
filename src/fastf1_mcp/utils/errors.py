from enum import Enum


class ErrorCode(Enum):
    """MCP-compatible error codes."""

    SESSION_NOT_FOUND = "session_not_found"
    SESSION_NOT_HELD = "session_not_held"
    DRIVER_NOT_FOUND = "driver_not_found"
    DATA_UNAVAILABLE = "data_unavailable"
    INVALID_PARAMETER = "invalid_parameter"
    YEAR_OUT_OF_RANGE = "year_out_of_range"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"


class FastF1MCPError(Exception):
    """Base exception for FastF1 MCP errors."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        suggestions: list[str] | None = None,
    ):
        self.code = code
        self.message = message
        self.suggestions = suggestions or []
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "error": True,
            "code": self.code.value,
            "message": self.message,
            "suggestions": self.suggestions,
        }
