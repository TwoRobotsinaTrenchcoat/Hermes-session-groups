# Hermes Session Groups

Cross-platform session routing for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Maps different (platform, chat_id) pairs to a shared session so messages from different platforms — e.g. a Telegram DM and a Discord DM — share one conversation history.

**Status:** Experimental — awaiting [PR #135404](https://github.com/NousResearch/hermes-agent/pulls/135404) (adapter-side `session_group_source` seam)

**Do NOT deploy to a live gateway.** This plugin patches the `gateway.session.session_group_source` seam, which does not exist in the current Hermes build. Registration fails cleanly with a logged warning; the plugin stays inert.

---

## Design

### Synthetic anchor (no member is privileged)

The session key is derived from a synthetic `SessionSource` whose `chat_id` is `"group:<name>"` — the group name *is* the session identity, not any member. Consequences:

- Adding or removing members never resets the group's conversation
- No member is a permanent "anchor"; members are interchangeable
- The synthetic source is only ever *returned by the hook*, never an incoming message — upstream's adapter-built-source security gate is unaffected

### Same-adapter validation

Upstream's `BasePlatformAdapter._pending_messages` holds one pending slot per session per adapter. Two chats of the same platform in one group would collide in that slot. This plugin validates at config load and logs an explicit error for any group with two members on one platform.

### Multi-group support

Each group gets its own synthetic anchor and session key, so multiple groups coexist without contention. A chat can belong to only one group — one key per chat is an architectural property of the durable session model.

---

## When it deploys

1. PR #135404 merges upstream
2. `hermes update` pulls the merged version
3. Verify the seam exists: `python3 -c "from gateway.session import session_group_source; print(session_group_source)"`
4. Copy this plugin to `~/.hermes/plugins/session-groups/`
5. Set `enabled: true` in `plugin.yaml`
6. Restart the gateway
7. Verify: fresh session, grouping resolves, existing sessions survive

If anything goes wrong, remove the plugin and restart — the plugin's absence restores default per-chat session behavior.

---

## Config

```yaml
session_groups:
  enabled: true
  groups:
    - name: family
      members:
        - platform: telegram
          chat_id: "-1001234567890"
        - platform: discord
          chat_id: "555000111"
    - name: friends
      members:
        - platform: telegram
          chat_id: "-1005555555555"
        - platform: discord
          chat_id: "666222333"
```

(All IDs above are synthetic examples.)

**Multi-group:** Each group is an independent session with its own key. `family` and `friends` both involve Telegram and Discord — different chats, different groups, no collision.

**One chat per platform per group:** Two Telegram chats — or two Discord chats — in one group are rejected at config load until the [per-adapter pending-slot limitation](https://github.com/NousResearch/hermes-agent/issues/79198) is resolved upstream. Use separate groups instead.

---

## Tests

```
python3 -m pytest test_session_groups.py -v
```

8 tests: config parsing, same-platform validation, member matching, group resolution, synthetic-source construction (group-named identity, no member privileged).



