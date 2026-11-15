from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import boto3
from moto import mock_aws
from job_finder.api import list_jobs, get_digest
from job_finder.collector import build_job_record
from job_finder.migrations import backfill_feed_indexes
from job_finder.models import SourceConfig
from job_finder.read_model import FEED_INDEXES, index_fields, job_sort_key, sortable_number
from job_finder.storage import DynamoJobRepository


def make_job(number, **overrides):
    job = build_job_record(SourceConfig("Acme", "greenhouse", "acme"), {
        "source_job_id": str(number), "title": f"DevOps Engineer {number}",
        "location_text": "Remote, United States", "description_text": "AWS Terraform Ansible Linux CI/CD",
        "apply_url": f"https://example.com/{number}",
    }, None)
    for key, value in overrides.items(): setattr(job, key, value)
    return job


class SortEncodingTests(unittest.TestCase):
    def test_negative_scores_dates_and_ties_preserve_repository_order(self):
        values = [-1000, -10.5, -1, 0, 1, 10, 10000000000]
        self.assertEqual(sorted(values, key=sortable_number), values)
        jobs = [make_job(i, score=(i % 7) - 4, is_eligible=(i % 3 != 0),
                         first_seen_at=f"2026-11-{10 + i % 4:02d}T00:00:00+00:00",
                         posted_date=[None, "invalid", "2026-11-12T00:00:00Z", "1794441600000"][i % 4]) for i in range(30)]
        for sort, (_, attribute) in FEED_INDEXES.items():
            with self.subTest(sort=sort):
                expected = sorted(jobs, key=lambda job: job_sort_key(job, sort_by=sort), reverse=True)
                indexed = sorted(jobs, key=lambda job: index_fields(job)[attribute], reverse=True)
                self.assertEqual([job.job_id for job in indexed], [job.job_id for job in expected])


@mock_aws
class DynamoFeedTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"AWS_DEFAULT_REGION": "us-east-1"})
        self.environment.start(); self.addCleanup(self.environment.stop)
        self.resource = boto3.resource("dynamodb", region_name="us-east-1")
        self.client = boto3.client("dynamodb", region_name="us-east-1")
        self.resource.create_table(TableName="jobs", KeySchema=[{"AttributeName":"job_id","KeyType":"HASH"}],
            AttributeDefinitions=[{"AttributeName":"job_id","AttributeType":"S"}, {"AttributeName":"status","AttributeType":"S"}, *[{"AttributeName":key,"AttributeType":"S"} for _, key in FEED_INDEXES.values()]],
            BillingMode="PAY_PER_REQUEST", GlobalSecondaryIndexes=[{
                "IndexName": name, "KeySchema":[{"AttributeName":"status","KeyType":"HASH"},{"AttributeName":key,"KeyType":"RANGE"}],
                "Projection":{"ProjectionType":"INCLUDE","NonKeyAttributes":["feed_summary","score","is_eligible","us_location_confidence","source_type"]},
            } for name,key in FEED_INDEXES.values()])
        self.repo = DynamoJobRepository("jobs", "runs", "sources", use_feed_indexes=True)

    def seed(self, count=30):
        for i in range(count):
            self.repo.upsert_job(make_job(i, score=100-i, status=["new","saved","dismissed","applied"][i % 4],
                first_seen_at=f"2026-11-{10+i % 4:02d}T00:00:00+00:00", posted_date="2026-11-12T00:00:00Z" if i % 2 else None))

    def assert_parity(self, **filters):
        indexed = self.repo.list_jobs(**filters)
        with patch.object(self.repo, "use_feed_indexes", False):
            scanned = self.repo.list_jobs(**filters)
        self.assertEqual([job.to_item() for job in indexed], [job.to_item() for job in scanned])

    def test_all_sorts_filters_and_cross_status_pages_match_scan(self):
        self.seed(40)
        for sort in FEED_INDEXES:
            for filters in [{}, {"only_new":True}, {"status":"saved"}, {"min_score":80}, {"found_after":"2026-11-12T00:00:00+00:00"}, {"company":"acme"}, {"source_type":"greenhouse","confirmed_us_only":True}]:
                with self.subTest(sort=sort,filters=filters):
                    self.assert_parity(sort_by=sort,offset=2,limit=5,**filters)

    def test_ranked_time_window_uses_recent_index_without_losing_older_high_scores(self):
        now=datetime.now(UTC)
        for i in range(130):
            self.repo.upsert_job(make_job(i,score=i,first_seen_at=(now-timedelta(hours=i)).isoformat()))
        with patch.object(self.repo.jobs_table,"query",wraps=self.repo.jobs_table.query) as query:
            results=self.repo.list_jobs(only_new=True,found_after=(now-timedelta(days=7)).isoformat(),limit=3)
        self.assertEqual([job.score for job in results],[129,128,127])
        self.assertTrue(all(call.kwargs["IndexName"]=="jobs-newest-v1" for call in query.call_args_list))

    def test_filtered_empty_pages_continue_until_matches(self):
        for i in range(160): self.repo.upsert_job(make_job(i,score=200-i,company="Target" if i>=150 else "Other"))
        with patch.object(self.repo.jobs_table,"scan",side_effect=AssertionError("feed must not scan")):
            jobs=self.repo.list_jobs(only_new=True,company="Target",limit=3)
        self.assertEqual(len(jobs),3)
        self.assertEqual(jobs[0].score,50)

    def test_empty_dynamodb_filter_pages_do_not_end_the_feed(self):
        for i in range(160):
            self.repo.upsert_job(make_job(i,score=200-i,us_location_confidence="assumed" if i<150 else "confirmed"))
        jobs=self.repo.list_jobs(only_new=True,confirmed_us_only=True,limit=3)
        self.assertEqual(len(jobs),3)
        self.assertEqual(jobs[0].score,50)

    def test_review_and_recollection_move_partition_without_losing_notes(self):
        job=make_job(1);self.repo.upsert_job(job)
        self.repo.update_job(job.job_id,status="saved",notes="keep this",dismiss_reason="low_priority")
        self.repo.upsert_job(make_job(1,score=150))
        self.assertEqual(self.repo.list_jobs(only_new=True),[])
        saved=self.repo.list_jobs(status="saved")[0]
        self.assertEqual(saved.notes,"keep this")
        self.assertEqual(saved.metadata["dismiss_reason"],"low_priority")
        detail=self.repo.get_job(job.job_id)
        self.assertEqual(detail.description_text,job.description_text)
        self.assertNotIn("feed_summary",detail.to_item())

    def test_search_remains_full_description_search_and_feedback_uses_queries(self):
        job=make_job(1,description_text="x "*400+"hidden needle")
        self.repo.upsert_job(job)
        self.assertEqual(len(self.repo.list_jobs(search="hidden needle")),1)
        self.repo.update_job(job.job_id,status="dismissed",dismiss_reason="already_seen")
        with patch.object(self.repo.jobs_table,"scan",side_effect=AssertionError("feedback must not scan")):
            feedback=self.repo.list_feedback_jobs()
        self.assertEqual(feedback[0].metadata["dismiss_reason"],"already_seen")
        self.assertEqual(feedback[0].location_text,job.location_text)

    def test_cursor_pages_are_distinct_and_filter_signature_is_preserved(self):
        self.seed(30)
        first=list_jobs(self.repo,{"limit":"4","only_new":"true"})
        second=list_jobs(self.repo,{"limit":"4","only_new":"true","cursor":first["next_cursor"]})
        self.assertEqual(len({item["job_id"] for item in first["items"]+second["items"]}),8)
        with self.assertRaises(ValueError):list_jobs(self.repo,{"limit":"4","status":"saved","cursor":first["next_cursor"]})

    def test_backfill_dry_run_resume_and_review_state_preservation(self):
        for i in range(7):self.repo.jobs_table.put_item(Item=make_job(i,notes="private notes",status="saved").to_item())
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint=Path(temporary)/"checkpoint.json"
            dry=backfill_feed_indexes(self.client,"jobs",checkpoint=checkpoint,page_size=2,workers=1)
            self.assertEqual(dry["updated"],7);self.assertFalse(checkpoint.exists())
            self.assertEqual(self.repo.list_jobs(status="saved"),[])
            partial=backfill_feed_indexes(self.client,"jobs",apply=True,checkpoint=checkpoint,page_size=2,workers=1,max_pages=1)
            self.assertFalse(partial["complete"])
            done=backfill_feed_indexes(self.client,"jobs",apply=True,checkpoint=checkpoint,page_size=2,workers=1)
            self.assertTrue(done["complete"]);self.assertEqual(done["updated"],7)
            self.assertEqual(len(self.repo.list_jobs(status="saved")),7)
            self.assertTrue(all(job.notes=="private notes" for job in self.repo.list_jobs(status="saved")))
            self.assert_parity(status="saved")

    def test_backfill_never_clobbers_a_concurrent_review_or_note(self):
        job=make_job(1);self.repo.jobs_table.put_item(Item=job.to_item())
        original=self.client.update_item
        def update_after_review(**kwargs):
            self.repo.update_job(job.job_id,status="applied",notes="new notes")
            return original(**kwargs)
        with patch.object(self.client,"update_item",side_effect=update_after_review):
            result=backfill_feed_indexes(self.client,"jobs",apply=True,workers=1)
        self.assertEqual(result["conflicts"],1)
        self.assertEqual(self.repo.get_job(job.job_id).notes,"new notes")
        self.assertEqual(len(self.repo.list_jobs(status="applied")),1)

    def test_backfill_protects_note_only_edits_without_status_change(self):
        job=make_job(1);self.repo.jobs_table.put_item(Item=job.to_item())
        original=self.client.update_item
        def update_after_note(**kwargs):
            self.repo.update_job(job.job_id,notes="edited during migration")
            return original(**kwargs)
        with patch.object(self.client,"update_item",side_effect=update_after_note):
            result=backfill_feed_indexes(self.client,"jobs",apply=True,workers=1)
        self.assertEqual(result["conflicts"],1)
        self.assertEqual(self.repo.list_jobs(only_new=True)[0].notes,"edited during migration")

    def test_checkpoint_cannot_resume_against_another_table(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint=Path(temporary)/"checkpoint.json"
            checkpoint.write_text(json.dumps({"table_arn":"another-account","complete":True}))
            with self.assertRaises(ValueError):backfill_feed_indexes(self.client,"jobs",apply=True,checkpoint=checkpoint)


if __name__ == "__main__":unittest.main()
