"""Backward-compatible import for the canonical core review queue module."""

from core.patient_review_queue import (  # noqa: F401
    PatientReviewQueue,
    ReviewItem,
    ReviewReason,
    ReviewStatus,
    review_queue,
)


if __name__ == "__main__":
    from core.activity_logger import logger

    logger.info(
        "Patient Review Queue is available from core.patient_review_queue"
    )
