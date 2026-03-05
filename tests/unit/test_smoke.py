"""Tests for the post-deploy smoke test script."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import the smoke-test script as a module (safe because of __main__ guard)
_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "smoke-test.py"
_spec = importlib.util.spec_from_file_location("smoke_test", _SCRIPT_PATH)
smoke_test = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke_test)

# Eliminate retry delays in tests
smoke_test.RETRY_DELAY = 0


def _mock_urlopen(body=None):
    """Create a mock urllib response with JSON body."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(body or {}).encode()
    return resp


class TestCheckFunction:
    def test_returns_true_on_valid_response(self):
        body = {"status": "ok"}
        with patch.object(
            smoke_test.urllib.request,
            "urlopen",
            return_value=_mock_urlopen(body=body),
        ):
            result = smoke_test.check(
                "https://example.com",
                "/api/health",
                lambda d: d.get("status") == "ok",
            )
        assert result is True

    def test_returns_false_after_retries_on_invalid_data(self):
        body = {"status": "broken"}
        with patch.object(
            smoke_test.urllib.request,
            "urlopen",
            return_value=_mock_urlopen(body=body),
        ):
            result = smoke_test.check(
                "https://example.com",
                "/api/health",
                lambda d: d.get("status") == "ok",
            )
        assert result is False

    def test_returns_false_after_retries_on_network_error(self):
        with patch.object(
            smoke_test.urllib.request,
            "urlopen",
            side_effect=ConnectionError("refused"),
        ):
            result = smoke_test.check("https://example.com", "/api/health", lambda d: True)
        assert result is False

    def test_retries_on_failure_then_succeeds(self):
        ok_resp = _mock_urlopen(body={"status": "ok"})
        with patch.object(
            smoke_test.urllib.request,
            "urlopen",
            side_effect=[ConnectionError("first"), ok_resp],
        ):
            result = smoke_test.check(
                "https://example.com",
                "/api/health",
                lambda d: d.get("status") == "ok",
            )
        assert result is True

    @pytest.mark.parametrize("status", ["ok", "degraded", "error"])
    def test_validates_health_config_statuses(self, status):
        body = {"status": status}
        with patch.object(
            smoke_test.urllib.request,
            "urlopen",
            return_value=_mock_urlopen(body=body),
        ):
            result = smoke_test.check(
                "https://example.com",
                "/api/health/config",
                lambda d: d.get("status") in ("ok", "degraded", "error"),
            )
        assert result is True

    def test_sends_custom_user_agent(self):
        with patch.object(
            smoke_test.urllib.request,
            "urlopen",
            return_value=_mock_urlopen(body={"status": "ok"}),
        ) as mock_open:
            smoke_test.check(
                "https://example.com",
                "/api/health",
                lambda d: True,
            )
        req = mock_open.call_args[0][0]
        assert req.get_header("User-agent") == "Tasche-Smoke/1.0"


class TestMain:
    def test_exits_1_on_failure(self):
        with (
            patch.object(
                smoke_test.urllib.request,
                "urlopen",
                side_effect=ConnectionError("down"),
            ),
            pytest.raises(SystemExit, match="1"),
        ):
            smoke_test.main(base="https://example.com")

    def test_succeeds_when_all_checks_pass(self):
        with patch.object(
            smoke_test.urllib.request,
            "urlopen",
            return_value=_mock_urlopen(body={"status": "ok"}),
        ):
            # Should not raise SystemExit
            smoke_test.main(base="https://example.com")
