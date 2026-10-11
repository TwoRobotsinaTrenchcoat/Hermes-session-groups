"""
Session Groups Plugin — EXPERIMENTAL

Cross-platform session routing. Maps different (platform, chat_id) pairs
to a shared session so messages from different platforms share one
conversation history.

Designed against ZK-Snarky's upstream adapter-side PR #135404
(https://github.com/NousResearch/hermes-agent/pulls/135404), which has NOT
merged yet. This plugin patches the ``gateway.session.session_group_source``
seam directly and will only work once that PR lands upstream. Do not deploy
to a live gateway until the PR merges.

Design: synthetic anchor (group name = session identity, no member
privileged), same-adapter validation, seam patch gated on the upstream merge.

Config schema, examples, and deployment notes: see README.md.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from gateway.platforms.base import SessionSource

logger = logging.getLogger("session-groups")


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class GroupMember:
    platform: str
    chat_id: str
    thread_id: Optional[str] = None

    def matches(self, source: SessionSource) -> bool:
        if source.platform.value != self.platform:
            return False
        if str(source.chat_id) != str(self.chat_id):
            return False
        if self.thread_id and str(source.thread_id) != str(self.thread_id):
            return False
        return True


@dataclass
class SessionGroup:
    name: str
    members: List[GroupMember] = field(default_factory=list)

    def matches_source(self, source: SessionSource) -> bool:
        return any(m.matches(source) for m in self.members)

    def platforms(self) -> set:
        return {m.platform for m in self.members}


@dataclass
class SessionGroupsConfig:
    enabled: bool = False
    groups: List[SessionGroup] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionGroupsConfig":
        enabled = bool(data.get("enabled", False))
        groups = []
        for grp_data in data.get("groups", []):
            name = grp_data.get("name", "")
            members = []
            for m in grp_data.get("members", []):
                members.append(GroupMember(
                    platform=str(m.get("platform", "")),
                    chat_id=str(m.get("chat_id", "")),
                    thread_id=m.get("thread_id"),
                ))
            groups.append(SessionGroup(name=name, members=members))
        return cls(enabled=enabled, groups=groups)

    def find_group(self, source: SessionSource) -> Optional[SessionGroup]:
        if not self.enabled:
            return None
        for grp in self.groups:
            if grp.matches_source(source):
                return grp
        return None

    def validate(self) -> List[str]:
        """Return a list of validation errors. Empty list = valid config."""
        errors = []
        for grp in self.groups:
            platforms = grp.platforms()
            if len(platforms) < len(grp.members):
                # Same platform appearing twice in one group
                seen = {}
                for m in grp.members:
                    if m.platform in seen:
                        errors.append(
                            f"Group '{grp.name}': two members on platform "
                            f"'{m.platform}' (chat_ids {seen[m.platform]} and "
                            f"{m.chat_id}). Upstream BasePlatformAdapter has "
                            f"one pending slot per session per adapter — "
                            f"same-adapter multi-chat groups are mechanically "
                            f"rejected by PR #135404. Remove one member or "
                            f"split into separate groups."
                        )
                    else:
                        seen[m.platform] = m.chat_id
        return errors


# ---------------------------------------------------------------------------
# Plugin state
# ---------------------------------------------------------------------------

_config: Optional[SessionGroupsConfig] = None


def _load_config(ctx) -> SessionGroupsConfig:
    """Load session_groups config from config.yaml via cfg_get."""
    try:
        from hermes_cli.config import load_config, cfg_get
        cfg = load_config() or {}
        block = cfg_get(cfg, "session_groups", default=None)
        if not isinstance(block, dict):
            return SessionGroupsConfig(enabled=False)
        return SessionGroupsConfig.from_dict(block)
    except Exception as e:
        logger.error("session-groups: failed to load config: %s", e)
        return SessionGroupsConfig(enabled=False)


def _build_synthetic_source(group: SessionGroup, incoming: SessionSource) -> SessionSource:
    """
    Build a synthetic SessionSource whose identity is the group name.

    This is the key design decision: instead of anchoring the session on a
    real member (which makes that member privileged and creates a re-anchor
    reset footgun), the group name is the session identity.

    The synthetic source is only ever RETURNED by the hook — it is never
    an incoming message source. Upstream's security gate checks that the
    INCOMING source is adapter-built; the synthetic source is config-gated
    and not attacker-controllable.

    user_id is copied from the incoming event so the profile namespace
    resolves correctly.
    """
    from gateway.session import SessionSource as _SessionSource
    # Platform doesn't matter for the key — it's part of the group identity
    # but we use a neutral placeholder since the key is synthetic
    return _SessionSource(
        platform=incoming.platform,
        chat_id=f"group:{group.name}",
        chat_type="group",
        user_id=incoming.user_id,
        thread_id=None,
    )


def _on_session_group_source(source: SessionSource) -> Optional[SessionSource]:
    """
    Replacement for gateway.session.session_group_source (patched in).

    Returns the synthetic key source for a configured group member, or None
    to fall through to default behavior.

    Upstream contract (PR #135404):
    - Returns a SessionSource or None
    - Only called through key_source_for()
    - key_source_for gates on _transport_adapter_ref (weakref security)
    """
    if _config is None:
        return None

    grp = _config.find_group(source)
    if grp is None:
        return None

    synthetic = _build_synthetic_source(grp, source)
    logger.debug(
        "session-groups: %s/%s -> synthetic source for group '%s' (chat_id=%s)",
        source.platform.value, source.chat_id, grp.name, synthetic.chat_id,
    )
    return synthetic


def register(ctx):
    """
    Register the plugin.

    The gateway.session.session_group_source seam is a module-level function
    in gateway/session.py (added by PR #135404), not a registered hook, so
    the plugin patches it directly.
    """
    global _config
    _config = _load_config(ctx)

    # Validate config — log errors but don't crash
    errors = _config.validate()
    for err in errors:
        logger.error("session-groups config error: %s", err)

    if not _config.enabled or not _config.groups:
        logger.info("session-groups: disabled or no groups configured")
        return

    # Patch the seam (only works once PR #135404 merges)
    try:
        import gateway.session
        original = gateway.session.session_group_source

        def patched(source):
            result = _on_session_group_source(source)
            if result is not None:
                return result
            return original(source)

        gateway.session.session_group_source = patched
        logger.info(
            "session-groups: registered (enabled=%s, groups=%d, "
            "patched session_group_source seam)",
            _config.enabled, len(_config.groups),
        )
    except (ImportError, AttributeError) as e:
        logger.warning(
            "session-groups: cannot patch session_group_source — "
            "PR #135404 has not merged yet (%s).",
            e,
        )
