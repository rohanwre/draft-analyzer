"""
Alerting shared by both DAGs: failure/retry alerts, and slow-stage alerts for any task
that finishes but took longer than its expected duration.

Alerts go to ALERT_WEBHOOK_URL (a Slack or Discord incoming webhook - the payload sets
both "text" and "content", so either accepts it). With no webhook configured they're
still written to the task log at WARNING level, so nothing fails just because alerting
isn't set up.

Why "slow" is checked on completion rather than with Airflow's built-in SLAs: Airflow
2.x SLAs are measured from the DAG run's logical date, which is meaningless for the
dataset-triggered downstream DAG (it runs whenever the crawl finishes, not on a clock).
A per-task duration check measures what actually matters - did this stage take far
longer than usual. A stage that hangs outright never completes, so each task also gets
an execution_timeout (a hard cap, several times its expected duration) that turns a
hang into a failure, which then alerts through the failure callback.
"""
import json
import logging
import os
import urllib.request
from datetime import timedelta

log = logging.getLogger(__name__)


def send_alert(message):
    log.warning("ALERT: %s", message)
    url = os.getenv("ALERT_WEBHOOK_URL")
    if not url:
        return
    body = json.dumps({"text": message, "content": message}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:  # an alerting outage must never fail the pipeline task itself
        log.exception("Failed to deliver alert to ALERT_WEBHOOK_URL")


def _describe(context):
    ti = context["task_instance"]
    return f"{ti.dag_id}.{ti.task_id} (run {context['run_id']}, try {ti.try_number})"


def alert_on_failure(context):
    send_alert(f":red_circle: FAILED {_describe(context)}: {context.get('exception')}\n"
               f"Log: {context['task_instance'].log_url}")


def alert_on_retry(context):
    send_alert(f":warning: RETRYING {_describe(context)}: {context.get('exception')}")


def slow_stage_check(expected: timedelta):
    """on_success_callback that alerts when the task ran longer than `expected`."""
    def callback(context):
        ti = context["task_instance"]
        if ti.duration is not None and ti.duration > expected.total_seconds():
            send_alert(f":turtle: SLOW {_describe(context)}: took {ti.duration / 60:.1f} min, "
                       f"expected under {expected.total_seconds() / 60:.0f} min")
    return callback


def stage_args(expected: timedelta, hard_cap_multiplier=4):
    """Per-task kwargs: slow-stage alert past `expected`, hard timeout past
    `expected * hard_cap_multiplier`."""
    return {
        "on_success_callback": slow_stage_check(expected),
        "execution_timeout": expected * hard_cap_multiplier,
    }


DEFAULT_ALERT_ARGS = {
    "on_failure_callback": alert_on_failure,
    "on_retry_callback": alert_on_retry,
}
