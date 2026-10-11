"""Tests for Session Groups — hook-side logic.

Mirrors the coverage of upstream's test_session_group_origins.py on the
config/hook side:
- No config = seam returns None (no change)
- Same-platform group rejected at load with explicit error
- Group resolution returns correct synthetic source for configured members
- Synthetic source identity is group-named, not member-anchored

These tests do NOT require PR #135404 — they test the plugin's own logic
(config parsing, validation, source construction) in isolation.

Run from the plugin directory:
    cd plugins/session-groups
    python3 -m pytest test_session_groups.py -v
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent dir to path so we can import the plugin module directly
sys.path.insert(0, str(Path(__file__).parent))

# Mock gateway.platforms.base before importing the plugin (it doesn't exist
# in this test environment — that's fine, we're testing the plugin logic, not
# the gateway integration)
sys.modules['gateway'] = MagicMock()
sys.modules['gateway.platforms'] = MagicMock()
sys.modules['gateway.platforms.base'] = MagicMock()
sys.modules['gateway.session'] = MagicMock()

import importlib.util
spec = importlib.util.spec_from_file_location("session_groups", Path(__file__).parent / "__init__.py")
sg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sg)


# --- Config parsing ---

def test_config_empty():
    """No config block = disabled."""
    cfg = sg.SessionGroupsConfig.from_dict({})
    assert cfg.enabled is False
    assert cfg.groups == []


def test_config_with_group():
    """Config with one group parses correctly."""
    data = {
        "enabled": True,
        "groups": [
            {
                "name": "test-group",
                "members": [
                    {"platform": "telegram", "chat_id": "-1001234567890"},
                    {"platform": "discord", "chat_id": "555000111"},
                ],
            }
        ],
    }
    cfg = sg.SessionGroupsConfig.from_dict(data)
    assert cfg.enabled is True
    assert len(cfg.groups) == 1
    assert cfg.groups[0].name == "test-group"
    assert len(cfg.groups[0].members) == 2


# --- Validation ---

def test_validate_clean_config():
    """Cross-platform group passes validation."""
    data = {
        "enabled": True,
        "groups": [
            {
                "name": "ok-group",
                "members": [
                    {"platform": "telegram", "chat_id": "-1001234567890"},
                    {"platform": "discord", "chat_id": "555000111"},
                ],
            }
        ],
    }
    cfg = sg.SessionGroupsConfig.from_dict(data)
    errors = cfg.validate()
    assert errors == []


def test_validate_same_platform_rejected():
    """Two Telegram members in one group = validation error."""
    data = {
        "enabled": True,
        "groups": [
            {
                "name": "bad-group",
                "members": [
                    {"platform": "telegram", "chat_id": "-1001234567890"},
                    {"platform": "telegram", "chat_id": "-1009876543210"},
                ],
            }
        ],
    }
    cfg = sg.SessionGroupsConfig.from_dict(data)
    errors = cfg.validate()
    assert len(errors) == 1
    assert "same platform" in errors[0].lower() or "two members" in errors[0].lower()


# --- Source matching ---

def _make_source(platform_value: str, chat_id: str):
    """Create a mock SessionSource for testing."""
    src = MagicMock()
    src.platform.value = platform_value
    src.chat_id = chat_id
    src.thread_id = None
    src.user_id = "999888777"
    return src


def test_member_matches():
    """GroupMember.matches works correctly."""
    m = sg.GroupMember(platform="telegram", chat_id="-1001234567890")

    assert m.matches(_make_source("telegram", "-1001234567890")) is True
    assert m.matches(_make_source("discord", "-1001234567890")) is False
    assert m.matches(_make_source("telegram", "-1009999999999")) is False


def test_find_group():
    """find_group returns the matching group."""
    data = {
        "enabled": True,
        "groups": [
            {
                "name": "g1",
                "members": [
                    {"platform": "telegram", "chat_id": "-1001234567890"},
                    {"platform": "discord", "chat_id": "555000111"},
                ],
            }
        ],
    }
    cfg = sg.SessionGroupsConfig.from_dict(data)

    src = _make_source("discord", "555000111")
    grp = cfg.find_group(src)
    assert grp is not None
    assert grp.name == "g1"

    src2 = _make_source("telegram", "-1009999999999")
    assert cfg.find_group(src2) is None


# --- Synthetic source construction ---

def test_synthetic_source_is_group_named():
    """The synthetic source uses group name, not any member's identity."""
    data = {
        "enabled": True,
        "groups": [
            {
                "name": "my-group",
                "members": [
                    {"platform": "telegram", "chat_id": "-1001234567890"},
                    {"platform": "discord", "chat_id": "555000111"},
                ],
            }
        ],
    }
    cfg = sg.SessionGroupsConfig.from_dict(data)
    grp = cfg.groups[0]

    incoming = _make_source("telegram", "-1001234567890")

    # _build_synthetic_source constructs a SessionSource — we mock it
    with patch("gateway.session.SessionSource") as MockSource:
        MockSource.return_value = MagicMock()
        synthetic = sg._build_synthetic_source(grp, incoming)

        # Verify the constructor was called with group-named chat_id
        call_kwargs = MockSource.call_args[1]
        assert call_kwargs["chat_id"] == "group:my-group"
        assert call_kwargs["user_id"] == incoming.user_id
        assert call_kwargs["chat_type"] == "group"


def test_synthetic_source_no_member_privileged():
    """Changing which member sends doesn't change the synthetic source identity."""
    data = {
        "enabled": True,
        "groups": [
            {
                "name": "stable-group",
                "members": [
                    {"platform": "telegram", "chat_id": "-1001234567890"},
                    {"platform": "discord", "chat_id": "555000111"},
                ],
            }
        ],
    }
    cfg = sg.SessionGroupsConfig.from_dict(data)
    grp = cfg.groups[0]

    with patch("gateway.session.SessionSource") as MockSource:
        MockSource.return_value = MagicMock()

        # Message from Telegram member
        tg = _make_source("telegram", "-1001234567890")
        sg._build_synthetic_source(grp, tg)
        tg_call = MockSource.call_args[1]

        # Message from Discord member
        MockSource.reset_mock()
        dc = _make_source("discord", "555000111")
        sg._build_synthetic_source(grp, dc)
        dc_call = MockSource.call_args[1]

        # Both produce the same synthetic identity
        assert tg_call["chat_id"] == dc_call["chat_id"] == "group:stable-group"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
