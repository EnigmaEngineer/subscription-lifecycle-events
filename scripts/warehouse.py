"""Land the wire stream the way the sink would, then fold it in SQL.

Two things happen here and the second one is the point.

First the generated delivery stream is written into a duckdb database in the
shape the Snowflake sink produces, which is one row per Kafka record with the
envelope in one variant column and the topic, the partition and the offset in
another. Nothing is deduplicated on the way in, because the sink does not
deduplicate. Its exactly once guarantee is about Kafka offsets.

Then dbt builds the models over it and this script compares the state they
produce against the state `subevents.state` produces in Python, field by field
over every subscription. Two implementations of one rule get compared on the
finest grain either of them produces.

Needs duckdb and dbt, which are in requirements-warehouse.txt and are not on
every machine this repository is checked out on. It refuses to write a manifest
where they are absent rather than writing one that says nothing.

    export SUBEVENTS_DUCKDB=/tmp/subevents.duckdb
    python scripts/warehouse.py --out measurements
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zlib
from dataclasses import asdict, fields

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from subevents import generator as gen  # noqa: E402
from subevents import state  # noqa: E402

DBT_DIR = os.path.join(ROOT, "dbt")
SEED_PATH = os.path.join(DBT_DIR, "seeds", "event_type_rank.csv")
TOPIC = "subscription-events"
PARTITIONS = 4


def write_seed(path: str = SEED_PATH) -> int:
    """Generate the rank seed from the handler's own table.

    The ranks have one home and it is `subevents.state.TYPE_RANK`. A seed typed
    by hand would be a second place the precedence lives, and the first time
    somebody changed one of them the models and the Python fold would disagree
    for a reason nobody could see in either file.
    """
    rows = sorted(state.TYPE_RANK.items(), key=lambda pair: (pair[1], pair[0]))
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["event_type", "type_rank", "is_invoice"])
        for event_type, rank in rows:
            writer.writerow([event_type, rank, str(event_type in state.INVOICE_EVENTS).lower()])
    return len(rows)


def kafka_records(deliveries: list[dict]) -> list[tuple]:
    """Turn each delivery into the row the sink writes.

    Partitioned on the subscription the event is about, which is what a real
    producer would key on and which keeps one subscription's events in one
    partition. The offset is per partition and increases, so two copies of one
    event id have a real total order over them.
    """
    offsets = {partition: 0 for partition in range(PARTITIONS)}
    rows = []
    for event in deliveries:
        subject, _ = state.subject_of(event)
        key = subject or event["id"]
        partition = zlib.crc32(key.encode("utf-8")) % PARTITIONS
        offset = offsets[partition]
        offsets[partition] = offset + 1
        content = json.dumps(event, separators=(",", ":"), sort_keys=True)
        metadata = json.dumps(
            {
                "topic": TOPIC,
                "partition": partition,
                "offset": offset,
                "key": key,
                "CreateTime": int(event["created"]) * 1000,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        rows.append((content, metadata))
    return rows


def land(db_path: str, rows: list[tuple]) -> int:
    import duckdb

    if os.path.exists(db_path):
        os.remove(db_path)
    conn = duckdb.connect(db_path)
    conn.execute("CREATE SCHEMA IF NOT EXISTS raw")
    conn.execute("DROP TABLE IF EXISTS raw.raw_subscription_events")
    # Two columns, because that is the table the sink creates when
    # schematization is off. VARCHAR rather than JSON so the models have to go
    # through the dialect macro, same as they would against a variant.
    conn.execute(
        "CREATE TABLE raw.raw_subscription_events"
        " (record_content VARCHAR NOT NULL, record_metadata VARCHAR NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO raw.raw_subscription_events VALUES (?, ?)", rows
    )
    landed = conn.execute("SELECT COUNT(*) FROM raw.raw_subscription_events").fetchone()[0]
    conn.close()
    return landed


def run_dbt(db_path: str) -> dict:
    """Build the project and report what the run did as counts.

    The last line dbt prints is a sentence and a wall time, and a manifest
    carrying either of those does not re-derive on another machine. What does
    re-derive is how many nodes ran and how many of them passed, which comes
    out of run_results.json.
    """
    env = dict(os.environ)
    env["SUBEVENTS_DUCKDB"] = db_path
    env["DBT_PROFILES_DIR"] = DBT_DIR
    target = os.path.join(DBT_DIR, "target")
    if os.path.isdir(target):
        shutil.rmtree(target)
    result = subprocess.run(
        ["dbt", "build", "--target", "duckdb"],
        cwd=DBT_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    out = {"command": "dbt build --target duckdb", "exit_code": result.returncode}
    results_path = os.path.join(target, "run_results.json")
    if os.path.exists(results_path):
        with open(results_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        by_status: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        for node in payload["results"]:
            by_status[node["status"]] = by_status.get(node["status"], 0) + 1
            kind = node["unique_id"].split(".")[0]
            by_kind[kind] = by_kind.get(kind, 0) + 1
        out["nodes_by_status"] = dict(sorted(by_status.items()))
        out["nodes_by_kind"] = dict(sorted(by_kind.items()))
        out["dbt_version"] = payload["metadata"]["dbt_version"]
    if result.returncode != 0:
        # A failed build has to say why in the file. A manifest recording only
        # an exit code sends the next reader back to a terminal that has gone.
        failures = [
            line
            for line in (result.stdout + result.stderr).splitlines()
            if "Error" in line or "error" in line
        ]
        out["error_lines"] = failures[:4]
    return out


def rebuild_with_a_flat_precedence(db_path: str) -> dict:
    """Give every event type the same rank and build again.

    This is the control on the seed. The precedence lives in one place and the
    models reach it through a join, and a join that silently returns nothing
    would leave every rank at the unranked fallback with no complaint
    anywhere. Flattening the ranks has to move the answer. If it does not, the
    models are not reading the seed and the agreement above means nothing.
    """
    original = open(SEED_PATH, encoding="utf-8").read()
    try:
        with open(SEED_PATH, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["event_type", "type_rank", "is_invoice"])
            for event_type in sorted(state.TYPE_RANK):
                writer.writerow([event_type, 0, str(event_type in state.INVOICE_EVENTS).lower()])
        build = run_dbt(db_path)
        states = sql_state(db_path) if build["exit_code"] == 0 else {}
    finally:
        with open(SEED_PATH, "w", encoding="utf-8", newline="") as handle:
            handle.write(original)
    return {"build": build, "states": states}


def row_counts(db_path: str) -> dict:
    import duckdb

    conn = duckdb.connect(db_path, read_only=True)
    counts = {}
    for label, relation in (
        ("staging", "STAGING.stg_subscription_events"),
        ("ordered", "STAGING.int_event_order"),
    ):
        counts[label + "_rows"] = conn.execute(
            f"SELECT COUNT(*) FROM {relation}"  # noqa: S608
        ).fetchone()[0]
    conn.close()
    return counts


def sql_state(db_path: str) -> dict[str, state.SubscriptionState]:
    import duckdb

    conn = duckdb.connect(db_path, read_only=True)
    names = [field.name for field in fields(state.SubscriptionState)]
    columns = ", ".join(names)
    rows = conn.execute(
        f"SELECT {columns} FROM ANALYTICS.subscription_state"  # noqa: S608
    ).fetchall()
    conn.close()
    out = {}
    for row in rows:
        values = dict(zip(names, row))
        values["quantity"] = int(values["quantity"])
        values["current_period_start"] = int(values["current_period_start"])
        values["current_period_end"] = int(values["current_period_end"])
        values["first_event_at"] = int(values["first_event_at"])
        values["last_event_at"] = int(values["last_event_at"])
        values["events_applied"] = int(values["events_applied"])
        values["invoices_paid"] = int(values["invoices_paid"])
        values["invoices_failed"] = int(values["invoices_failed"])
        values["ambiguous_seconds"] = int(values["ambiguous_seconds"])
        values["cancel_at_period_end"] = bool(values["cancel_at_period_end"])
        out[values["subscription_id"]] = state.SubscriptionState(**values)
    return out


def compare(python_side: dict, sql_side: dict) -> dict:
    """Field by field over the union of both answers.

    Reported per field as well as per subscription, because a disagreement
    concentrated in one column is a different defect from one spread over
    every row.
    """
    names = [field.name for field in fields(state.SubscriptionState)]
    ids = sorted(set(python_side) | set(sql_side))
    per_field = {name: 0 for name in names}
    disagreeing = []
    for sub_id in ids:
        left = python_side.get(sub_id)
        right = sql_side.get(sub_id)
        if left is None or right is None:
            disagreeing.append(sub_id)
            continue
        bad = [
            name
            for name in names
            if getattr(left, name) != getattr(right, name)
        ]
        for name in bad:
            per_field[name] += 1
        if bad:
            disagreeing.append(sub_id)
    example = {}
    if disagreeing:
        sub_id = disagreeing[0]
        example = {
            "subscription_id": sub_id,
            "python": asdict(python_side[sub_id]) if sub_id in python_side else None,
            "sql": asdict(sql_side[sub_id]) if sub_id in sql_side else None,
        }
    return {
        "subscriptions_python": len(python_side),
        "subscriptions_sql": len(sql_side),
        "subscriptions_compared": len(ids),
        "subscriptions_disagreeing": len(disagreeing),
        "only_in_python": sorted(set(python_side) - set(sql_side)),
        "only_in_sql": sorted(set(sql_side) - set(python_side)),
        "fields_disagreeing": {k: v for k, v in sorted(per_field.items()) if v},
        "python_state_sha256": state.state_digest(python_side),
        "sql_state_sha256": state.state_digest(sql_side),
        "folds_agree": state.state_digest(python_side) == state.state_digest(sql_side),
        "first_disagreement": example,
    }


def resolved_relations(target: str, db_path: str) -> dict:
    """Parse the project against one target and read the names it resolved.

    This is the only warehouse claim a run with no account can make. A parse
    resolves a relation without opening a connection, so a schema that does
    not exist resolves exactly like one that does. What it proves is that the
    names this project writes to are the names the configuration creates, and
    the reconciliation in `subevents.names` compares those two lists.

    Both targets write the same manifest, so the directory is cleared between
    parses and each answer is read before the next parse starts.
    """
    env = dict(os.environ)
    env["SUBEVENTS_DUCKDB"] = db_path
    env["DBT_PROFILES_DIR"] = DBT_DIR
    manifest = os.path.join(DBT_DIR, "target")
    if os.path.isdir(manifest):
        shutil.rmtree(manifest)
    result = subprocess.run(
        ["dbt", "parse", "--target", target],
        cwd=DBT_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    out = {"exit_code": result.returncode}
    path = os.path.join(manifest, "manifest.json")
    if not os.path.exists(path):
        out["error_lines"] = [
            line
            for line in (result.stdout + result.stderr).splitlines()
            if "Error" in line
        ][:3]
        return out
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    out["models"] = sorted(
        f"{node['database']}.{node['schema']}.{node['name']}"
        for node in payload["nodes"].values()
        if node["resource_type"] in ("model", "seed")
    )
    out["sources"] = sorted(
        f"{node['database']}.{node['schema']}.{node['name']}"
        for node in payload["sources"].values()
    )
    return out


def probe_arm(db_path: str) -> dict:
    """The arm where the step with no meaning behind it actually decides.

    On the published corpus every tied second is settled by the type rank, so
    the sort on the event id is reached nought times and the comparison above
    grades it not at all. This configuration plants seconds holding two events
    of the same rank. It is off by default because switching it on changes
    every byte the generator produces.

    This is the only arm where the two implementations could legitimately
    diverge. Python sorts the id by code point and SQL sorts it under whatever
    collation the warehouse uses, and on these ids those are the same order
    until somebody changes how an id is spelled.
    """
    cfg = gen.GeneratorConfig(same_second_update_rate=0.35)
    clean = gen.generate(cfg)
    wire = gen.to_wire(clean)
    landed = land(db_path, kafka_records(wire))
    build = run_dbt(db_path)
    if build["exit_code"] != 0:
        return {"landed_records": landed, "dbt_exit": build["exit_code"], "dbt": build}
    resolution = state.tie_resolution(clean)
    result = compare(state.reconstruct(wire).subscriptions, sql_state(db_path))
    return {
        "landed_records": landed,
        "clean_events": len(clean),
        "tied_seconds": resolution["tied_seconds"],
        "ties_left_to_the_event_id": resolution["resolved_by_event_id"],
        "subscriptions_disagreeing": result["subscriptions_disagreeing"],
        "fields_disagreeing": result["fields_disagreeing"],
        "folds_agree": result["folds_agree"],
        "dbt_exit": build["exit_code"],
    }


def figures(db_path: str) -> dict:
    clean = gen.generate()
    wire = gen.to_wire(clean)
    rows = kafka_records(wire)
    landed = land(db_path, rows)
    build = run_dbt(db_path)
    if build["exit_code"] != 0:
        return {"seed_rows": write_seed(), "landed_records": landed, "dbt": build}
    python_side = state.reconstruct(wire).subscriptions
    sql_side = sql_state(db_path)
    result = compare(python_side, sql_side)

    # Two controls, because a comparison reporting no disagreements is also
    # what a comparison of one answer against itself looks like.
    #
    # The first folds the same events in arrival order on the Python side. That
    # was measured as wrong for 102 of 200 subscriptions on 2026-11-19, so a
    # comparator that cannot see it is broken.
    #
    # The second is in rebuild_with_a_flat_precedence and it grades the SQL
    # side instead.
    by_arrival = state.reconstruct(wire, key=None).subscriptions
    arrival = compare(by_arrival, sql_side)
    flat = rebuild_with_a_flat_precedence(db_path)
    flattened = compare(python_side, flat["states"]) if flat["states"] else {}

    result.update(
        {
            "clean_events": len(clean),
            "landed_records": landed,
            "distinct_event_ids": len({event["id"] for event in wire}),
            "partitions": PARTITIONS,
            "seed_rows": write_seed(),
            "unranked_type_rank": state.UNRANKED,
            "dbt": build,
            "controls": {
                "arrival_order_subscriptions_disagreeing": arrival["subscriptions_disagreeing"],
                "arrival_order_fields_disagreeing": arrival["fields_disagreeing"],
                "flat_precedence_subscriptions_disagreeing": flattened.get(
                    "subscriptions_disagreeing"
                ),
                "flat_precedence_fields_disagreeing": flattened.get("fields_disagreeing"),
                "flat_precedence_dbt_exit": flat["build"]["exit_code"],
            },
        }
    )
    result.update(row_counts(db_path))
    stem, extension = os.path.splitext(db_path)
    result["probe"] = probe_arm(stem + "_probe" + extension)
    result["resolved"] = {
        target: resolved_relations(target, db_path) for target in ("duckdb", "snowflake")
    }
    # The flat build left the marts holding the control's answer. Put the real
    # one back so a reader querying the database after this script sees the
    # state this manifest describes.
    run_dbt(db_path)
    return result


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(ROOT, "measurements"))
    parser.add_argument("--db", default=os.environ.get("SUBEVENTS_DUCKDB", "/tmp/subevents.duckdb"))
    args = parser.parse_args(argv)

    write_seed()
    if shutil.which("dbt") is None:
        print("dbt is not on this machine, no manifest written")
        raise SystemExit(2)
    try:
        import duckdb  # noqa: F401
    except ImportError:
        print("duckdb is not on this machine, no manifest written")
        raise SystemExit(2)

    result = figures(args.db)
    os.makedirs(args.out, exist_ok=True)
    body = json.dumps(result, indent=2, sort_keys=True) + "\n"
    with open(os.path.join(args.out, "dbt.json"), "w", encoding="utf-8") as handle:
        handle.write(body)

    print(f"landed records            {result.get('landed_records')}")
    print(f"distinct event ids        {result.get('distinct_event_ids')}")
    print(f"subscriptions in python   {result.get('subscriptions_python')}")
    print(f"subscriptions in sql      {result.get('subscriptions_sql')}")
    print(f"subscriptions disagreeing {result.get('subscriptions_disagreeing')}")
    print(f"folds agree               {'yes' if result.get('folds_agree') else 'NO'}")
    print(f"fields disagreeing        {result.get('fields_disagreeing')}")
    print(f"staging rows              {result.get('staging_rows')}")
    print(f"arrival control disagrees {result['controls']['arrival_order_subscriptions_disagreeing']}")
    print(f"flat rank control differs {result['controls']['flat_precedence_subscriptions_disagreeing']}")
    print(f"probe ties left to the id {result['probe'].get('ties_left_to_the_event_id')}")
    print(f"probe subs disagreeing    {result['probe'].get('subscriptions_disagreeing')}")
    print(f"dbt nodes                 {result['dbt'].get('nodes_by_status')}")
    for target, resolved in sorted(result["resolved"].items()):
        print(f"resolved on {target:<13} {len(resolved.get('models', []))} models, exit {resolved['exit_code']}")


if __name__ == "__main__":
    main()
