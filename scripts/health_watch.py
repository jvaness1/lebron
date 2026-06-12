"""Health watchdog for the live hermes-trading worker. Emits a line ONLY when
something is wrong (worker gone silent, RISK_HALT fired, deployment not Online) and
stays quiet when healthy. Type-keyed dedup so a persistent problem alerts once, not
every cycle. Designed to run under the Monitor tool. Local ops tool (not committed)."""
import subprocess, json, time
from datetime import datetime, timezone

RW = "/Users/jamesvaness/.hermes/node/bin/railway"
POLL_SEC = 900           # 15 min
SILENCE_MIN = 45         # worker ticks every ~30 min; >45 min quiet = problem


def run(args, timeout=60):
    try:
        return subprocess.run([RW] + args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def newest_log_age_min():
    out = run(["logs", "--json", "--lines", "8"])
    ts, halt = [], False
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("timestamp"):
            ts.append(rec["timestamp"])
        if "RISK_HALT" in (rec.get("message") or ""):
            halt = True
    if not ts:
        return None, halt
    try:
        newest = max(ts)[:19]   # YYYY-MM-DDTHH:MM:SS
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(newest).replace(tzinfo=timezone.utc)).total_seconds() / 60
        return age, halt
    except Exception:
        return None, halt


prev = set()
while True:
    alerts = {}   # type -> message
    age, halt = newest_log_age_min()
    if age is None:
        alerts["nolog"] = "no recent logs from worker (railway CLI issue or worker down)"
    elif age > SILENCE_MIN:
        alerts["silent"] = f"worker SILENT for {age:.0f} min (expected a tick within ~30)"
    if halt:
        alerts["halt"] = "RISK_HALT fired — circuit breaker flattened the book; manual reset needed"
    status = run(["status"])
    if status.strip() and "Online" not in status:
        alerts["offline"] = "Railway deployment is NOT Online"

    cur = set(alerts)
    for k in cur - prev:                 # only newly-appeared problems
        print(f"HEALTH ALERT: {alerts[k]}", flush=True)
    for k in prev - cur:                 # recoveries
        print(f"HEALTH OK: '{k}' condition cleared", flush=True)
    prev = cur
    time.sleep(POLL_SEC)
