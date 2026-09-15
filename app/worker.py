"""Background job worker: python -m app.worker [--drain] [--poll SECONDS]"""

import argparse
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.models import Submission, Widget
from app.providers.email import send_email
from app.repositories import jobs as jobs_repo

logger = logging.getLogger("app.worker")
MAX_BACKOFF_SECONDS = 300
MAX_LOOP_BACKOFF_SECONDS = 30
DRAIN_MAX_CONSECUTIVE_ERRORS = 3


def handle_send_confirmation_email(db: Session, payload: dict) -> None:
    submission = db.get(Submission, uuid.UUID(payload["submission_id"]))
    if submission is None:
        logger.info("submission %s no longer exists; nothing to send", payload["submission_id"])
        return
    recipient = submission.data.get("email")
    if not recipient:
        logger.info("submission %s has no email field; nothing to send", submission.id)
        return
    widget = db.get(Widget, submission.widget_id)
    title = (widget.title if widget else "our form").replace("\r", " ").replace("\n", " ")
    send_email(
        to=recipient,
        subject=f"Thanks - we received your submission to {title}",
        body=f"Hi,\n\nThanks for submitting '{title}'. We have received your details.\n",
    )


HANDLERS = {"send_confirmation_email": handle_send_confirmation_email}


def process_one() -> bool:
    """Process one due job. Returns False when nothing was due."""
    with SessionLocal() as db:
        job = jobs_repo.claim_next(db)
        if job is None:
            db.rollback()
            return False
        job.attempts += 1
        try:
            handler = HANDLERS.get(job.kind)
            if handler is None:
                raise LookupError(f"no handler for job kind '{job.kind}'")
            with db.begin_nested():
                handler(db, job.payload)
        except Exception as exc:
            job.last_error = f"{type(exc).__name__}: {exc}"[:1000]
            if job.attempts >= job.max_attempts:
                job.status = "dead"
                logger.error(
                    "ALERT job_dead id=%s kind=%s attempts=%s error=%s",
                    job.id, job.kind, job.attempts, job.last_error,
                )
            else:
                delay = min(2 ** job.attempts, MAX_BACKOFF_SECONDS)
                job.run_after = datetime.now(timezone.utc) + timedelta(seconds=delay)
                logger.warning(
                    "job_retry id=%s kind=%s attempt=%s/%s retry_in=%ss error=%s",
                    job.id, job.kind, job.attempts, job.max_attempts, delay, job.last_error,
                )
        else:
            job.status = "done"
            job.last_error = None
            logger.info("job_done id=%s kind=%s attempts=%s", job.id, job.kind, job.attempts)
        db.commit()
        return True


def queue_is_empty() -> bool:
    with SessionLocal() as db:
        return jobs_repo.count_pending(db) == 0


def run(drain: bool, poll_seconds: float) -> None:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logger.info(
        "worker started email_mode=%s force_email_failure=%s drain=%s",
        settings.email_mode, settings.force_email_failure, drain,
    )
    consecutive_errors = 0
    while True:
        try:
            worked = process_one()
            if not worked and drain and queue_is_empty():
                logger.info("queue drained; exiting")
                return
            consecutive_errors = 0
        except Exception as exc:
            consecutive_errors += 1
            if isinstance(exc, OperationalError):
                logger.error(
                    "database unavailable (%s); attempt %s",
                    type(exc.orig).__name__ if exc.orig is not None else "OperationalError",
                    consecutive_errors,
                )
            else:
                logger.exception("worker loop error; attempt %s", consecutive_errors)
            if drain and consecutive_errors >= DRAIN_MAX_CONSECUTIVE_ERRORS:
                logger.error("ALERT worker giving up drain after %s consecutive errors", consecutive_errors)
                raise SystemExit(1)
            time.sleep(min(poll_seconds * 5 * consecutive_errors, MAX_LOOP_BACKOFF_SECONDS))
            continue
        if not worked:
            time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Background job worker")
    parser.add_argument("--drain", action="store_true", help="exit once no pending jobs remain")
    parser.add_argument("--poll", type=float, default=1.0, help="seconds between polls")
    args = parser.parse_args()
    try:
        run(args.drain, args.poll)
    except KeyboardInterrupt:
        logger.info("worker stopped")


if __name__ == "__main__":
    main()