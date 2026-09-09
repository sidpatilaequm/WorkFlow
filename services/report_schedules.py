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
            successes = 0
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
                    successes += 1
                except Exception as exc:
                    logger.error("Schedule %s: failed for %s — %s", sched.id, email, exc)
                    errors.append(f"{email}: {exc}")

            sched.last_error = "; ".join(errors)[:500] if errors else None
            if successes > 0:
                # At least one recipient actually got it — advance so we don't resend to them;
                # last_error still shows which addresses failed, for the admin to see.
                sched.last_sent_at = now
            else:
                # Total failure (e.g. analytics unreachable) — deliberately do NOT advance
                # last_sent_at. Previously this always advanced, which meant one transient
                # failure silently pushed the retry a full interval_hours into the future
                # instead of the next 15-minute check — indistinguishable from "goes out late"
                # with no error ever surfaced (nothing here was even logged until the
                # logging config fix in main.py). Leaving it unset makes this schedule due
                # again next check, same as a fresh schedule.
                logger.error("Schedule %s: every recipient failed, will retry next check", sched.id)
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
