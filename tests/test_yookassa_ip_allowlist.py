"""IP-allowlist helpers for the YooKassa webhook.

These tests cover only the pure-function gate (no DB, no HTTP) — they
exercise the CIDR membership logic that decides whether a given
client address may even reach the webhook handler. Integration
behaviour (status re-fetch, idempotency) is verified end-to-end on
prod via the smoke-test in the Week 1 plan.
"""
import pytest

from app.services.yookassa_security import _is_yookassa_ip, client_ip_from_headers


class TestIsYookassaIp:
    @pytest.mark.parametrize(
        "ip",
        [
            "185.71.76.0",
            "185.71.76.15",
            "185.71.76.31",
            "185.71.77.0",
            "185.71.77.31",
            "77.75.153.0",
            "77.75.153.127",
            "77.75.156.11",
            "77.75.156.35",
            "77.75.154.128",
            "77.75.154.255",
            "2a02:5180::1",
            "2a02:5180:ffff::1",
        ],
    )
    def test_published_ranges_are_allowed(self, ip):
        assert _is_yookassa_ip(ip) is True, ip

    @pytest.mark.parametrize(
        "ip",
        [
            "185.71.76.32",
            "185.71.75.255",
            "203.0.113.42",
            "8.8.8.8",
            "127.0.0.1",
            "10.0.0.1",
            "2a02:5181::1",
            "::1",
        ],
    )
    def test_outside_ranges_are_rejected(self, ip):
        assert _is_yookassa_ip(ip) is False, ip

    @pytest.mark.parametrize(
        "garbage",
        [
            "",
            "not-an-ip",
            "999.999.999.999",
            "185.71.76",
            None,
        ],
    )
    def test_invalid_input_does_not_crash(self, garbage):
        assert _is_yookassa_ip(garbage) is False


class TestClientIpFromHeaders:
    def test_real_ip_wins(self):
        assert client_ip_from_headers("203.0.113.5", "10.0.0.1, 8.8.8.8") == "203.0.113.5"

    def test_real_ip_trimmed(self):
        assert client_ip_from_headers("  203.0.113.5  ", None) == "203.0.113.5"

    def test_forwarded_for_fallback_takes_first_entry(self):
        assert client_ip_from_headers(None, "203.0.113.5, 10.0.0.1") == "203.0.113.5"

    def test_no_headers_returns_none(self):
        assert client_ip_from_headers(None, None) is None

    def test_empty_real_ip_falls_through_to_forwarded(self):
        assert client_ip_from_headers("", "203.0.113.5") == "203.0.113.5"
