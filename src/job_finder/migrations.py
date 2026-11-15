"""Resumable, conditional backfill of dashboard index keys; dry-run by default."""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from job_finder.read_model import FEED_INDEXES, FEED_INTERNAL_ATTRIBUTES, REVIEW_STATUSES, SUMMARY_ATTRIBUTES, index_fields
from job_finder.storage import _summary_record

ORDER_INPUTS = ("status", "score", "first_seen_at", "last_seen_at", "posted_date", "is_eligible")


def backfill_feed_indexes(client: Any, table_name: str, *, apply: bool = False,
                          checkpoint: Path | None = None, restart: bool = False,
                          page_size: int = 250, workers: int = 8,
                          max_pages: int | None = None) -> dict[str, Any]:
    from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
    from botocore.exceptions import ClientError

    table = client.describe_table(TableName=table_name)["Table"]
    identity = table["TableArn"]
    if apply:
        active = {index["IndexName"] for index in table.get("GlobalSecondaryIndexes", []) if index["IndexStatus"] == "ACTIVE"}
        if not {name for name, _ in FEED_INDEXES.values()} <= active:
            raise ValueError("Create all three feed indexes and wait until ACTIVE before applying the backfill")
    stats: dict[str, Any] = {"table_arn": identity, "scanned": 0, "updated": 0,
                             "unchanged": 0, "conflicts": 0, "last_key": None, "complete": False}
    if apply and checkpoint and checkpoint.exists() and not restart:
        saved = json.loads(checkpoint.read_text())
        if saved["table_arn"] != identity:
            raise ValueError("Checkpoint belongs to a different table/account")
        stats.update(saved)
        if stats["complete"]:
            return stats
    deserialize = TypeDeserializer().deserialize
    serialize = TypeSerializer().serialize
    fields = tuple(dict.fromkeys(("job_id", *ORDER_INPUTS, *SUMMARY_ATTRIBUTES, *FEED_INTERNAL_ATTRIBUTES)))
    names = {f"#f{index}": name for index, name in enumerate(fields)}
    scan: dict[str, Any] = {"TableName": table_name, "ConsistentRead": True,
                            "ProjectionExpression": ", ".join(names), "ExpressionAttributeNames": names,
                            "Limit": page_size}
    if stats["last_key"]:
        scan["ExclusiveStartKey"] = stats["last_key"]

    def update(raw: dict[str, Any]) -> str:
        item = {key: deserialize(value) for key, value in raw.items()}
        record = _summary_record(item)
        if record.status not in REVIEW_STATUSES:
            raise ValueError(f"Unknown review status for {record.job_id}; resolve before switching reads")
        desired = index_fields(record)
        if all(item.get(key) == value for key, value in desired.items()) and "status" in item:
            return "unchanged"
        if not apply:
            return "updated"  # Would update; no checkpoint or data is written in dry-run.
        aliases = {f"#k{index}": key for index, key in enumerate(desired)}
        values = {f":k{index}": serialize(value) for index, value in enumerate(desired.values())}
        assignments = [f"#k{index} = :k{index}" for index in range(len(desired))]
        conditions = []
        for index, field in enumerate(dict.fromkeys((*ORDER_INPUTS, *SUMMARY_ATTRIBUTES))):
            alias = f"#c{index}"
            aliases[alias] = field
            if field in item:
                value_key = f":c{index}"
                values[value_key] = serialize(item[field])
                conditions.append(f"{alias} = {value_key}")
            else:
                conditions.append(f"attribute_not_exists({alias})")
        # Legacy rows can omit status; normalize only when still absent.
        if "status" not in item:
            values[":new_status"] = serialize("new")
            assignments.append("#c0 = :new_status")
        try:
            client.update_item(TableName=table_name, Key={"job_id": raw["job_id"]},
                               UpdateExpression="SET " + ", ".join(assignments),
                               ConditionExpression=" AND ".join(conditions),
                               ExpressionAttributeNames=aliases, ExpressionAttributeValues=values)
            return "updated"
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return "conflicts"  # A live writer won; never overwrite its newer ordering fields.
            raise

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 32))) as pool:
        pages = 0
        while True:
            response = client.scan(**scan)
            items = response.get("Items", [])
            for result in pool.map(update, items):
                stats[result] += 1
                stats["scanned"] += 1
            stats["last_key"] = response.get("LastEvaluatedKey")
            stats["complete"] = not bool(stats["last_key"])
            if apply and checkpoint:
                checkpoint.parent.mkdir(parents=True, exist_ok=True)
                temporary = checkpoint.with_suffix(checkpoint.suffix + ".tmp")
                temporary.write_text(json.dumps(stats, indent=2) + "\n")
                temporary.replace(checkpoint)
            pages += 1
            if stats["complete"] or (max_pages is not None and pages >= max_pages):
                break
            scan["ExclusiveStartKey"] = stats["last_key"]
            if pages % 20 == 0:
                print(json.dumps({"event": "feed_backfill_progress", **stats}), flush=True)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs-table", required=True)
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--checkpoint", type=Path, default=Path("data/feed-index-backfill.json"))
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    import boto3
    from botocore.config import Config
    client = boto3.client("dynamodb", region_name=args.region,
                          config=Config(max_pool_connections=40, retries={"mode": "adaptive", "max_attempts": 10}))
    result = backfill_feed_indexes(client, args.jobs_table, apply=args.apply, checkpoint=args.checkpoint,
                                  restart=args.restart, workers=args.workers)
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
