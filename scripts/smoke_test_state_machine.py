"""Smoke tests for MeetingStatus enum and transitions.

Run:
    python scripts/smoke_test_state_machine.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.constants.meeting_status import MeetingStatus, ALL_STATUSES, TERMINAL_STATUSES


def main():
    failures = []

    expected = {
        "pending_upload", "uploading", "queued",
        "transcribing", "analyzing",
        "completed", "transcription_empty", "error",
    }
    actual = {s.value for s in MeetingStatus}
    if actual != expected:
        failures.append(f"[1] enum mismatch: missing {expected - actual}, extra {actual - expected}")

    if set(ALL_STATUSES) != expected:
        failures.append("[2] ALL_STATUSES does not match MeetingStatus members")

    if set(TERMINAL_STATUSES) != {"completed", "transcription_empty", "error"}:
        failures.append(f"[3] TERMINAL_STATUSES wrong: {TERMINAL_STATUSES}")

    if MeetingStatus.QUEUED.value != "queued":
        failures.append("[4] MeetingStatus.QUEUED.value != 'queued'")

    if failures:
        for f in failures:
            print("FAIL:", f)
        sys.exit(1)
    print("OK: state machine constants")


if __name__ == "__main__":
    main()
