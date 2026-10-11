# Session Groups Plugin v0.2.0f — EXPERIMENTAL

**Status:** Experimental — awaiting upstream merge

**Blocked on:** [PR #135404](https://github.com/NousResearch/hermes-agent/pulls/135404) (ZK-Snarky's adapter-side session-group implementation)

**Do NOT deploy to a live gateway.** This version patches the `gateway.session.session_group_source` seam, which does not exist in the current Hermes build. It will fail at registration time with a clear warning and fall back to disabled.

For production use today, deploy **v0.1.1** (hook-based via `session_key_override`, works with the current patcher).

---

## What v0.2.0f changes vs v0.1.1

| | v0.1.1 (current) | v0.2.0f (experimental) |
|---|---|---|
| **Mechanism** | `ctx.register_hook("session_key_override", ...)` | Patches `gateway.session.session_group_source` directly |
| **Key derivation** | Group-name key via `f"{ns}:conv:{name}"` | Synthetic `SessionSource` with `chat_id=f"group:{name}"` |
| **Session identity** | String key | `SessionSource` object (upstream contract) |
| **Anchor** | None (group name = identity) | Synthetic source (group name = identity, no real member privileged) |
| **Validation** | None | Same-adapter multi-chat groups rejected at load |
| **PII** | Real IDs in docstring examples | Synthetic IDs only |
| **Requires PR #135404** | No | Yes |

## Design decisions

### Synthetic anchor (no real member is privileged)

v0.1.1 built the session key from the group name directly. v0.2.0f achieves the same result through the upstream seam by returning a synthetic `SessionSource` whose `chat_id` is `"group:<name>"`. This means:

- Adding/removing members never resets the group session
- No member is the "anchor" — all members are interchangeable
- The synthetic source is only ever returned by the hook, never an incoming message — upstream's weakref security gate is unaffected

### Same-adapter validation

Upstream's `BasePlatformAdapter._pending_messages` holds one slot per session per adapter. Two chats on the same platform in one group would collide. v0.2.0f validates this at config load and logs explicit errors.

### Multi-group support

Multiple groups are fully supported — each gets its own synthetic anchor and session key. A chat can only be in one group (one key per chat, architectural limit of the durable model).

## When to deploy

1. PR #135404 merges upstream
2. `hermes update` pulls the merged version
3. Verify the `session_group_source` seam exists: `python3 -c "from gateway.session import session_group_source; print(session_group_source)"`
4. Delete the old patcher (`session_patcher.py`) — its 2 lines are replaced by the real seam
5. Copy this plugin to `~/.hermes/plugins/session-groups/`
6. Set `enabled: true` in plugin.yaml
7. Restart gateway
8. Verify: fresh session, grouping works, existing sessions survive

## Rollback

If anything goes wrong: revert to v0.1.1, re-enable the patcher, restart. The old system is the fallback until the new one is proven.

## Config example

```yaml
session_groups:
  enabled: true
  groups:
    - name: family
      members:
        - platform: telegram
          chat_id: "-1001234567890"  # synthetic example
        - platform: discord
          chat_id: "555000111"        # synthetic example
    - name: friends
      members:
        - platform: telegram
          chat_id: "-1005555555555"  # synthetic example
        - platform: discord
          chat_id: "666222333"        # synthetic example
```

**Multi-group note:** Each group gets its own synthetic anchor and session key — multiple groups are fully supported. Here, `family` and `friends` are two independent sessions, both involving Telegram and Discord: different chats, different groups, no collision. A chat can only be in one group (architectural limit).

**One-chat-per-platform constraint:** Within a single group, at most one member per platform. Two Telegram chats — or two Discord chats — in one group will be rejected at config load with an explicit error until the per-adapter pending-slot limitation is resolved upstream. Use separate groups instead.

**Schema compatibility:** v0.2.0f uses the exact same config schema as v0.1.1 (config layer carried over unchanged). Your existing `session_groups` config works as-is — no migration needed.

## Migration from v0.1.1

No config changes needed. When #135404 merges:

1. Delete the patcher (`session_patcher.py`) — its 2 lines are replaced by the real seam
2. Copy this plugin to `~/.hermes/plugins/session-groups/`
3. Set `enabled: true` in plugin.yaml
4. Restart gateway
5. Verify: fresh session, grouping works, existing sessions survive

The existing `session_groups:` block in your config.yaml stays exactly as-is.
