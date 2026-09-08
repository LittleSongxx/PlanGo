"""One-time Redis namespace migration after stopping this project's API/worker.

Run against the copied PlanGo Redis volume, retaining the old volume for rollback.
The caller owns Docker resource verification and the maintenance window. This
script neither stops services nor modifies database/browser identities.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid


def _pending(client, stream, group):
    # FULL exposes the absolute delivery timestamp; XPENDING idle milliseconds
    # would lose elapsed time while a large PEL is being copied.
    details = client.execute_command("XINFO STREAM", stream, "FULL", "COUNT", 0, full=True)
    return next(g["pending"] for g in details["groups"] if g["name"] == group)


def migrate_redis(client):
    """Keep payloads, cursor, retry counts, pending owners and delivery times.

    Requires a binary-response redis-py client and quiescent producers/consumers.
    Every source remains intact until the final atomic swap. A process crash can
    leave only unconsumed staging keys; it never partially renames source keys.
    """
    # ponytail: stopped single-node Redis; online/cluster migration needs a
    # separate coordination protocol, not a best-effort scan of moving queues.
    sources = sorted(set(client.scan_iter(match="yoyu:*")))
    if not sources:
        return {"keys": 0, "groups": 0, "pending": 0}
    staging_prefix = f"plango:migration:{uuid.uuid4().hex}:".encode()
    plans = []
    for source in sources:
        original_dump = client.dump(source)
        target = b"plango:" + source.removeprefix(b"yoyu:")
        if target.endswith(b":attempts:yoyu-workers"):
            target = target.removesuffix(b"yoyu-workers") + b"plango-workers"
        if any(p["target"] == target for p in plans):
            raise RuntimeError("Legacy keys map to the same destination; refusing to merge")
        if client.exists(target):
            raise RuntimeError("Redis migration target already exists; refusing to merge queues")
        group = None
        pending = []
        consumers = []
        if client.type(source) == b"stream":
            groups = client.xinfo_groups(source)
            if any(g["name"] == b"plango-workers" for g in groups):
                raise RuntimeError("New consumer group already exists on a legacy stream")
            group = next((g for g in groups if g["name"] == b"yoyu-workers"), None)
            if group:
                pending = _pending(client, source, b"yoyu-workers")
                consumers = client.xinfo_consumers(source, b"yoyu-workers")
                if len(pending) != group["pending"]:
                    raise RuntimeError("Pending entries changed; stop producers and consumers")
                for row in pending:
                    if not client.xrange(source, row[0], row[0]):
                        raise RuntimeError("Deleted pending entry cannot be copied losslessly; original queue untouched")
        plans.append({
            "source": source, "target": target, "stage": staging_prefix + source,
            "dump": original_dump, "expires": client.pexpiretime(source),
            "group": group, "pending": pending, "consumers": consumers,
        })
    try:
        for plan in plans:
            stage = plan["stage"]
            expires = plan["expires"]
            client.restore(stage, max(0, expires), plan["dump"], absttl=expires > 0)
            group = plan["group"]
            if not group:
                continue
            client.xgroup_create(
                stage, b"plango-workers", id=group["last-delivered-id"],
                entries_read=group.get("entries-read"),
            )
            for consumer in plan["consumers"]:
                client.xgroup_createconsumer(stage, b"plango-workers", consumer["name"])
            for row in plan["pending"]:
                claimed = client.xclaim(
                    stage, b"plango-workers", row[1], 0, [row[0]],
                    time=row[2], retrycount=row[3], force=True, justid=True,
                )
                if claimed != [row[0]]:
                    raise RuntimeError("Pending entry copy failed; original queue untouched")
            copied = next(g for g in client.xinfo_groups(stage) if g["name"] == b"plango-workers")
            for field in ("last-delivered-id", "entries-read", "pending", "lag"):
                if copied.get(field) != group.get(field):
                    raise RuntimeError("Consumer group cursor differs; original queue untouched")
            if _pending(client, stage, b"plango-workers") != plan["pending"]:
                raise RuntimeError("Pending entry identity differs; original queue untouched")
            client.xgroup_destroy(stage, b"yoyu-workers")

        # All validation precedes any source removal, inside one Redis command.
        # DUMP includes stream entries/groups/PEL, so a concurrent consumer causes
        # refusal instead of silently losing its acknowledgement or new work.
        client.eval("""
            local count = #KEYS / 3
            if #redis.call('KEYS', 'yoyu:*') ~= count then
                return redis.error_reply('migration source namespace changed')
            end
            for i = 1, count do
                local offset = (i - 1) * 3
                if redis.call('EXISTS', KEYS[offset + 2]) ~= 0 then
                    return redis.error_reply('migration destination appeared')
                end
                if redis.call('DUMP', KEYS[offset + 1]) ~= ARGV[(i - 1) * 2 + 1]
                  or tostring(redis.call('PEXPIRETIME', KEYS[offset + 1])) ~= ARGV[(i - 1) * 2 + 2]
                  or redis.call('EXISTS', KEYS[offset + 3]) ~= 1 then
                    return redis.error_reply('migration source changed or staging expired')
                end
            end
            for i = 1, count do
                local offset = (i - 1) * 3
                redis.call('RENAME', KEYS[offset + 3], KEYS[offset + 2])
                redis.call('DEL', KEYS[offset + 1])
            end
            return count
        """, len(plans) * 3,
            *(p[k] for p in plans for k in ("source", "target", "stage")),
            *(v for p in plans for v in (p["dump"], p["expires"])),
        )
    finally:
        # Only this invocation's private copies; old keys/volumes are not cleanup.
        client.delete(*(p["stage"] for p in plans))
    return {
        "keys": len(plans),
        "groups": sum(bool(p["group"]) for p in plans),
        "pending": sum(len(p["pending"]) for p in plans),
    }


def self_check(client):
    """Controlled, empty Redis only; no real business payloads or credentials."""
    from redis.exceptions import ResponseError

    if client.dbsize():
        raise RuntimeError("Self-check requires an empty disposable Redis database")
    old = b"yoyu:runs"
    client.set(b"unrelated", b"keep")
    ids = [client.xadd(old, {"run_id": f"fixture-{i}"}) for i in range(4)]
    client.xgroup_create(old, b"yoyu-workers", "0-0")
    client.xreadgroup(b"yoyu-workers", b"consumer-a", {old: ">"}, count=2)
    client.xreadgroup(b"yoyu-workers", b"consumer-b", {old: ">"}, count=1)
    client.xack(old, b"yoyu-workers", ids[0])
    client.xclaim(old, b"yoyu-workers", b"consumer-a", 0, [ids[1]], idle=100_000, retrycount=3)
    expected_pel = _pending(client, old, b"yoyu-workers")
    attempts = b"yoyu:runs:attempts:yoyu-workers"
    client.hset(attempts, ids[1], 2)
    client.expire(attempts, 3600)
    expires = client.pexpiretime(attempts)
    client.xadd(b"yoyu:runs:dead-letter", {"run_id": "fixture-dead"})
    client.xgroup_create(b"yoyu:memory-embed", b"yoyu-workers", "0-0", mkstream=True)
    before = client.xrange(old)
    assert migrate_redis(client) == {"keys": 4, "groups": 2, "pending": 2}
    new = b"plango:runs"
    assert client.xrange(new) == before
    assert _pending(client, new, b"plango-workers") == expected_pel
    assert client.hget(b"plango:runs:attempts:plango-workers", ids[1]) == b"2"
    assert client.pexpiretime(b"plango:runs:attempts:plango-workers") == expires
    assert client.get(b"unrelated") == b"keep"
    assert not list(client.scan_iter(match=b"yoyu:*"))
    assert migrate_redis(client)["keys"] == 0
    # The next new delivery is only the never-delivered entry, not old side effects.
    delivered = client.xreadgroup(b"plango-workers", b"after", {new: ">"})
    assert [entry[0] for entry in delivered[0][1]] == [ids[3]]
    client.set(b"yoyu:collision", b"original")
    client.set(b"plango:collision", b"destination")
    try:
        migrate_redis(client)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Name collision was not refused")
    assert client.get(b"yoyu:collision") == b"original"
    client.delete(b"yoyu:collision")
    deleted = client.xadd(b"yoyu:tombstone", {"fixture": "deleted"})
    client.xgroup_create(b"yoyu:tombstone", b"yoyu-workers", "0-0")
    client.xreadgroup(b"yoyu-workers", b"consumer", {b"yoyu:tombstone": ">"})
    client.xdel(b"yoyu:tombstone", deleted)
    snapshot = client.dump(b"yoyu:tombstone")
    try:
        migrate_redis(client)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Tombstone PEL was not refused")
    assert client.dump(b"yoyu:tombstone") == snapshot
    client.delete(b"yoyu:tombstone")
    race = b"yoyu:race"
    message = client.xadd(race, {"fixture": "concurrent-ack"})
    client.xgroup_create(race, b"yoyu-workers", "0-0")
    client.xreadgroup(b"yoyu-workers", b"consumer", {race: ">"})
    real_eval = client.eval

    def concurrent_ack(*args):
        client.xack(race, b"yoyu-workers", message)
        return real_eval(*args)

    client.eval = concurrent_ack
    try:
        migrate_redis(client)
    except ResponseError:
        pass
    else:
        raise AssertionError("Concurrent acknowledgement was silently discarded")
    finally:
        client.eval = real_eval
    assert client.exists(race) and not client.exists(b"plango:race")
    assert not _pending(client, race, b"yoyu-workers")
    assert not list(client.scan_iter(match=b"plango:migration:*"))
    print("Redis migration check passed: cursor, PEL, retries, TTL, isolation, rerun, collision, tombstone, concurrent ack")


if __name__ == "__main__":
    import redis

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--redis-url", default=os.environ.get("PLANGO_REDIS_URL"))
    parser.add_argument("--quiesced", action="store_true", help="Caller verified old/new API and workers are stopped")
    parser.add_argument("--self-check", action="store_true", help="Use only an empty disposable Redis")
    args = parser.parse_args()
    if not args.redis_url or not (args.quiesced or args.self_check):
        parser.error("provide Redis URL and either --quiesced or --self-check")
    with redis.Redis.from_url(args.redis_url) as connection:
        if args.self_check:
            self_check(connection)
        else:
            print(json.dumps(migrate_redis(connection)))
