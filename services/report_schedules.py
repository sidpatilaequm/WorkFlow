"""
Recurring "email this report" sends. Checks report_schedules for anything
due and, for each due row, calls analytics' POST /api/r/<key>/<token>/email
once per recipient — the exact same render+send path a human clicking
"Email report" in NexD Designer goes through, so a scheduled send looks
identical to an on-demand one.

Runs on its own dedicated BackgroundScheduler (started in main.py's startup
event), not services/escalation.py's — that one's start_scheduler() is never
actually called anywhere in this codebase today, so piggybacking on it would
silently also start firing escalation/reminder emails that have apparently
never gone out in production. That's a separate call to make, not a side
effect of this feature.
"""
import logging
import os
from datetime import datetime, timedelta

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

ANALYTICS_BASE_URL = os.getenv("ANALYTICS_BASE_URL", "http://127.0.0.1:5090")
CHECK_INTERVAL_MINUTES = 15

scheduler = BackgroundScheduler()


def send_scheduled_reports(db=None) -> None:
    from database import SessionLocal
    import models

    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    try:
        now = datetime.utcnow()
        due = (
            db.query(models.ReportSchedule)
            .filter(models.ReportSchedule.is_active == True)
            .all()
        )
        due = [
            s for s in due
            if s.last_sent_at is None
            or s.last_sent_at <= now - timedelta(hours=s.interval_hours)
        ]
        if not due:
            return
        logger.info("Found %d due report schedule(s)", len(due))

        for sched in due:
            errors = []
            for email in (sched.recipients or []):
                try:
                    resp = httpx.post(
                        f"{ANALYTICS_BASE_URL}/api/r/{sched.process_key}/{sched.token}/email",
                        json={"email": email, "message": sched.message or ""},
                        timeout=90,  # PDF rendering via headless browser is the slow part
                    )
                    resp.raise_for_status()
                    if not resp.json().get("success"):
                        raise RuntimeError(resp.json().get("error", "unknown error"))
                    logger.info("Schedule %s: sent to %s", sched.id, email)
                except Exception as exc:
                    logger.error("Schedule %s: failed for %s — %s", sched.id, email, exc)
                    errors.append(f"{email}: {exc}")

            # Advance last_sent_at regardless of per-recipient failures, so one bad
            # address doesn't retry every check cycle and spam the working ones —
            # last_error records what happened for the admin to see.
            sched.last_sent_at = now
            sched.last_error = "; ".join(errors)[:500] if errors else None
            if owns_session:
                db.add(sched)

        if owns_session:
            db.commit()
        else:
            db.flush()

    except Exception:
        if owns_session:
            db.rollback()
        logger.exception("Scheduled report check failed")
        if not owns_session:
            raise
    finally:
        if owns_session:
            db.close()


def start_scheduler() -> None:
    scheduler.add_job(
        send_scheduled_reports,
        trigger=IntervalTrigger(minutes=CHECK_INTERVAL_MINUTES),
        id="send_scheduled_reports",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Report-schedule scheduler started (checking every %dm)", CHECK_INTERVAL_MINUTES)


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
