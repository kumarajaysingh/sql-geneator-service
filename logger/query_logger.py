from logger.logger_config import get_logger

_logger = get_logger()


def log_query_details(
    *,
    query: str,
    sql: str | None,
    explanation: str | None,
    status: str | None,
    validation_feedback: str | None,
    validation_score: int | None,
    attempts: int,
) -> None:
    """Logs the full detail of a generation run for later debugging/auditing."""
    _logger.info(
        "user_query=%r | sql=%r | explanation=%r | status=%r | "
        "validation_feedback=%r | validation_score=%r | attempts=%r",
        query,
        sql,
        explanation,
        status,
        validation_feedback,
        validation_score,
        attempts,
    )
