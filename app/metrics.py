"""Prometheus metrics for processing_jobs queue.

Already wired via prometheus_fastapi_instrumentator in main.py — these
are application-level Counter/Gauge/Histogram alongside the auto-collected
HTTP metrics.
"""
from prometheus_client import Counter, Gauge, Histogram

# How many jobs are pending right now, by priority
processing_jobs_pending = Gauge(
    "processing_jobs_pending",
    "Number of processing jobs in pending state",
    labelnames=["priority"],
)

# How long a job spent in each stage (set on /complete)
processing_jobs_duration_seconds = Histogram(
    "processing_jobs_duration_seconds",
    "Time spent in each processing stage",
    labelnames=["stage"],  # 'queued', 'transcribing', 'analyzing', 'total'
    buckets=(30, 60, 120, 300, 600, 1200, 1800, 3600, 7200),
)

# Failure counter for alerting
processing_jobs_failures_total = Counter(
    "processing_jobs_failures_total",
    "Number of jobs that ended in failed state",
    labelnames=["reason"],  # 'worker_timeout', 'whisper', 'pyannote', 'deepseek', 'unknown'
)

# Retry counter — flap indicator
processing_jobs_retries_total = Counter(
    "processing_jobs_retries_total",
    "Number of times a job was bounced from claimed to pending",
)
