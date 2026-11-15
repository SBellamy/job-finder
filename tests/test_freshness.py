from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import boto3
from moto import mock_aws
from job_finder.api import list_jobs, export_jobs, get_digest
from job_finder.availability import missing_observation
from job_finder.ats.ashby import fetch_ashby_jobs
from job_finder.ats.greenhouse import fetch_greenhouse_jobs
from job_finder.collector import build_job_record, collect_sources
from job_finder.models import SourceConfig
from job_finder.normalize import strip_html, classify_us_location
from job_finder.read_model import FEED_INDEXES
from job_finder.storage import DynamoJobRepository, LocalJsonRepository, LocalPayloadStore

SOURCE = SourceConfig("Acme", "greenhouse", "acme")
NOW = datetime.now(UTC)
OLD = (NOW - timedelta(days=20)).isoformat()

def job(number, **overrides):
    record = build_job_record(SOURCE, {"source_job_id": str(number), "title": "DevOps Engineer", "location_text": "Remote, United States", "description_text": "AWS Terraform Linux cloud infrastructure", "apply_url": f"https://example.com/{number}"}, None)
    for key, value in overrides.items():setattr(record, key, value)
    if "last_seen_at" in overrides and "availability" not in overrides:
        record.availability["checked_at"] = overrides["last_seen_at"]
    return record


class NormalizationTests(unittest.TestCase):
    def test_escaped_markup_is_decoded_and_hidden_content_is_removed(self):
        self.assertEqual(strip_html('&amp;lt;p&amp;gt;AWS &amp;amp; Linux&amp;lt;/p&amp;gt;'), 'AWS & Linux')
        self.assertEqual(strip_html('<p>Own <strong>production</strong>.</p><p>Terraform</p><script>marketing</script><style>sales</style>'), 'Own production. Terraform')
        self.assertEqual(strip_html('Memory < 5 GB and latency > 1 ms'), 'Memory < 5 GB and latency > 1 ms')

    def test_explicit_workplace_restrictions_override_provider_remote(self):
        for fields in [{"title":"DevOps Engineer (Hybrid, 3 days, New York)"}, {"title":"On-site DevOps Engineer"}, {"description_text":"This position is hybrid. AWS Terraform"}]:
            record=build_job_record(SOURCE,{"source_job_id":"x","title":"DevOps Engineer","location_text":"United States","remote_type":"remote",**fields},None)
            self.assertFalse(record.is_remote)
            self.assertFalse(record.is_eligible)
        record=build_job_record(SOURCE,{"source_job_id":"x","title":"Hybrid Cloud Engineer","location_text":"Remote, US","description_text":"We offer a hybrid cloud platform."},None)
        self.assertTrue(record.is_remote)

    def test_only_valid_complete_board_lists_allow_absence_checks(self):
        for fetcher, target in [(fetch_ashby_jobs, 'job_finder.ats.ashby.fetch_json'), (fetch_greenhouse_jobs, 'job_finder.ats.greenhouse.fetch_json')]:
            with patch(target, return_value=({'jobs': []}, '{}')):
                payload, jobs = fetcher(SOURCE)
                self.assertTrue(payload['_snapshot_complete'])
            with patch(target, return_value=({'error': 'upstream problem'}, '{}')):
                with self.assertRaises(ValueError):fetcher(SOURCE)
            with patch(target, return_value=({'jobs': [{'title': 'missing id'}]}, '{}')):
                payload, jobs = fetcher(SOURCE)
                self.assertFalse(payload['_snapshot_complete'])
        with patch('job_finder.ats.greenhouse.fetch_json', return_value=({'jobs': [{'id': 1}], 'meta': {'total': 3}}, '{}')):
            payload, jobs = fetch_greenhouse_jobs(SOURCE)
            self.assertFalse(payload['_snapshot_complete'])

    def test_postal_abbreviations_need_context(self):
        self.assertEqual(classify_us_location('Austin, TX','remote'),'confirmed')
        self.assertNotEqual(classify_us_location('Remote, CA','remote'),'confirmed')
        self.assertNotEqual(classify_us_location('CA','remote'),'confirmed')
        self.assertNotEqual(classify_us_location('Toronto, ON, Canada','remote'),'confirmed')


class AvailabilityTests(unittest.TestCase):
    def test_repeated_complete_checks_need_twelve_hours_and_are_idempotent(self):
        first=missing_observation({},last_seen_at=OLD,observed_at=NOW.isoformat(),check_id='one')
        self.assertEqual(first['state'],'missing')
        self.assertIsNone(missing_observation(first,last_seen_at=OLD,observed_at=(NOW+timedelta(hours=1)).isoformat(),check_id='one'))
        second=missing_observation(first,last_seen_at=OLD,observed_at=(NOW+timedelta(hours=1)).isoformat(),check_id='two')
        self.assertEqual(second['state'],'missing')
        third=missing_observation(second,last_seen_at=OLD,observed_at=(NOW+timedelta(hours=13)).isoformat(),check_id='three')
        self.assertEqual(third['state'],'closed')
        self.assertIsNone(missing_observation({},last_seen_at=(NOW+timedelta(minutes=1)).isoformat(),observed_at=NOW.isoformat(),check_id='old-check'))

    def test_local_reconciliation_keeps_review_history_and_reopens_returned_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            repo=LocalJsonRepository(f'{directory}/jobs.json',f'{directory}/runs.json')
            original=job(1,status='applied',notes='keep private',metadata={'applied_at':'earlier'},first_seen_at=OLD,last_seen_at=OLD)
            repo.upsert_job(original)
            repo.reconcile_source_jobs(SOURCE,set(),observed_at=NOW.isoformat(),check_id='one')
            repo.reconcile_source_jobs(SOURCE,set(),observed_at=(NOW+timedelta(hours=13)).isoformat(),check_id='two')
            stored=repo.get_job(original.job_id)
            self.assertEqual((stored.status,stored.notes,stored.metadata['applied_at']),('applied','keep private','earlier'))
            self.assertEqual(stored.availability['state'],'closed')
            self.assertEqual(len(repo.list_jobs(status='applied')),1)
            self.assertEqual(repo.list_jobs(available_only=True),[])
            repo.upsert_job(job(1))
            stored=repo.get_job(original.job_id)
            self.assertEqual(stored.availability['state'],'open')
            self.assertEqual(stored.first_seen_at,OLD)
            self.assertEqual(stored.notes,'keep private')

    def test_storage_failure_prevents_availability_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            repo=LocalJsonRepository(f'{directory}/jobs.json',f'{directory}/runs.json')
            with patch('job_finder.collector.fetch_jobs',return_value=({'_snapshot_complete':True},[{'source_job_id':'x'}])), patch.object(repo,'upsert_job',side_effect=RuntimeError('storage unavailable')), patch.object(repo,'reconcile_source_jobs',side_effect=AssertionError('incomplete write')):
                run=collect_sources([SOURCE],repo,LocalPayloadStore(f'{directory}/raw'))
            self.assertEqual(run.run_status,'failed')
            self.assertEqual(repo.list_source_records(enabled_only=False)[0].success_count,0)

    def test_partial_or_failed_crawls_never_reconcile_absent_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            repo=LocalJsonRepository(f'{directory}/jobs.json',f'{directory}/runs.json')
            repo.upsert_job(job(1,last_seen_at=OLD))
            with patch('job_finder.collector.fetch_jobs',return_value=({'_raw_body':'{}','_snapshot_complete':False},[])),patch.object(repo,'reconcile_source_jobs',side_effect=AssertionError('partial snapshot')):
                collect_sources([SOURCE],repo,LocalPayloadStore(f'{directory}/raw'))
            with patch('job_finder.collector.fetch_jobs',side_effect=RuntimeError('provider unavailable')),patch.object(repo,'reconcile_source_jobs',side_effect=AssertionError('failed snapshot')):
                run=collect_sources([SOURCE],repo,LocalPayloadStore(f'{directory}/raw'))
            self.assertEqual(run.run_status,'failed')
            self.assertEqual(repo.get_job(job(1).job_id).availability['state'],'open')


@mock_aws
class DynamoAvailabilityTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{'AWS_DEFAULT_REGION':'us-east-1'});env.start();self.addCleanup(env.stop)
        resource=boto3.resource('dynamodb',region_name='us-east-1')
        resource.create_table(TableName='jobs',KeySchema=[{'AttributeName':'job_id','KeyType':'HASH'}],BillingMode='PAY_PER_REQUEST',AttributeDefinitions=[{'AttributeName':name,'AttributeType':'S'} for name in ['job_id','status','source_name',*[key for name,key in FEED_INDEXES.values()]]],GlobalSecondaryIndexes=[*[
            {'IndexName':name,'KeySchema':[{'AttributeName':'status','KeyType':'HASH'},{'AttributeName':key,'KeyType':'RANGE'}],'Projection':{'ProjectionType':'INCLUDE','NonKeyAttributes':['feed_summary','score','is_eligible','us_location_confidence','source_type']}} for name,key in FEED_INDEXES.values()],
            {'IndexName':'jobs-source-v1','KeySchema':[{'AttributeName':'source_name','KeyType':'HASH'},{'AttributeName':'job_id','KeyType':'RANGE'}],'Projection':{'ProjectionType':'INCLUDE','NonKeyAttributes':['source_type','last_seen_at','availability']}}])
        resource.create_table(TableName='sources',KeySchema=[{'AttributeName':'source_id','KeyType':'HASH'}],AttributeDefinitions=[{'AttributeName':'source_id','AttributeType':'S'}],BillingMode='PAY_PER_REQUEST')
        self.repo=DynamoJobRepository('jobs','runs','sources',use_feed_indexes=True)
        self.repo.upsert_source(SOURCE)

    def test_stale_and_missing_records_are_filtered_before_cursor_and_export(self):
        for i in range(60):self.repo.upsert_job(job(i,score=200-i,last_seen_at=OLD))
        for i in range(60,67):self.repo.upsert_job(job(i,score=100-i))
        self.repo.upsert_job(job('closed',score=190,availability={'state':'closed'}))
        filters={'seen_after':(NOW-timedelta(days=7)).isoformat(),'available_only':'true','limit':'3'}
        first=list_jobs(self.repo,filters)
        second=list_jobs(self.repo,{**filters,'cursor':first['next_cursor']})
        self.assertEqual(len({row['job_id'] for row in first['items']+second['items']}),6)
        self.assertEqual(json.loads(export_jobs(self.repo,{**filters,'limit':'100','format':'json'})['body'])['count'],7)
        for sort in FEED_INDEXES:
            indexed=self.repo.list_jobs(seen_after=filters['seen_after'],available_only=True,sort_by=sort)
            self.repo.use_feed_indexes=False
            scanned=self.repo.list_jobs(seen_after=filters['seen_after'],available_only=True,sort_by=sort)
            self.repo.use_feed_indexes=True
            self.assertEqual([row.to_item() for row in indexed],[row.to_item() for row in scanned])
        with self.assertRaises(ValueError):list_jobs(self.repo,{**filters,'available_only':'false','cursor':first['next_cursor']})
        with self.assertRaises(ValueError):list_jobs(self.repo,{'seen_after':'yesterday'})
        self.assertNotIn(job('closed').job_id,[row['job_id'] for row in get_digest(self.repo,{'min_score':'0'})['items']])

    def test_concurrent_review_survives_availability_update(self):
        record=job(1,last_seen_at=OLD);self.repo.upsert_job(record)
        query=self.repo.jobs_table.query
        def reviewed(**kwargs):
            response=query(**kwargs)
            self.repo.update_job(record.job_id,status='saved',notes='concurrent note')
            return response
        with patch.object(self.repo.jobs_table,'query',side_effect=reviewed):
            result=self.repo.reconcile_source_jobs(SOURCE,set(),observed_at=NOW.isoformat(),check_id='one')
        self.assertEqual(result['missing'],1)
        stored=self.repo.get_job(record.job_id)
        self.assertEqual((stored.status,stored.notes,stored.availability['state']),('saved','concurrent note','missing'))
        self.assertEqual(self.repo.list_jobs(status='saved')[0].notes,'concurrent note')
        self.assertEqual(self.repo.list_jobs(available_only=True),[])

    def test_fresher_collection_wins_against_an_old_snapshot(self):
        record=job(1,last_seen_at=OLD);self.repo.upsert_job(record)
        query=self.repo.jobs_table.query
        def recollected(**kwargs):
            response=query(**kwargs)
            self.repo.upsert_job(job(1,last_seen_at=(NOW+timedelta(minutes=1)).isoformat()))
            return response
        with patch.object(self.repo.jobs_table,'query',side_effect=recollected):
            result=self.repo.reconcile_source_jobs(SOURCE,set(),observed_at=NOW.isoformat(),check_id='one')
        self.assertEqual(result['conflicts'],1)
        self.assertEqual(self.repo.get_job(record.job_id).availability['state'],'open')

    def test_large_board_reconciliation_resumes_past_its_write_budget(self):
        for i in range(105):self.repo.upsert_job(job(i,last_seen_at=OLD))
        first=self.repo.reconcile_source_jobs(SOURCE,set(),observed_at=NOW.isoformat(),check_id='one')
        second=self.repo.reconcile_source_jobs(SOURCE,set(),observed_at=NOW.isoformat(),check_id='one')
        self.assertEqual((first['missing'],first['deferred']),(100,1))
        self.assertEqual((second['missing'],second['deferred']),(5,0))
        self.assertEqual(self.repo.list_jobs(available_only=True),[])

    def test_collection_retries_after_a_concurrent_note_without_losing_it(self):
        record=job(1);self.repo.upsert_job(record)
        put=self.repo.jobs_table.put_item
        calls=0
        def reviewed(**kwargs):
            nonlocal calls
            calls+=1
            if calls==1:
                self.repo.jobs_table.update_item(Key={'job_id':record.job_id},UpdateExpression='SET notes = :note',ExpressionAttributeValues={':note':'new private note'})
            return put(**kwargs)
        with patch.object(self.repo.jobs_table,'put_item',side_effect=reviewed):
            self.repo.upsert_job(job(1,score=150))
        self.assertEqual(calls,2)
        self.assertEqual(self.repo.get_job(record.job_id).notes,'new private note')
        self.assertEqual(self.repo.get_job(record.job_id).score,150)

    def test_review_retries_when_availability_changes_after_the_read(self):
        record=job(1,last_seen_at=OLD);self.repo.upsert_job(record)
        put=self.repo.jobs_table.put_item
        calls=0
        def reconciled(**kwargs):
            nonlocal calls
            calls+=1
            if calls==1:
                self.repo.reconcile_source_jobs(SOURCE,set(),observed_at=NOW.isoformat(),check_id='one')
            return put(**kwargs)
        with patch.object(self.repo.jobs_table,'put_item',side_effect=reconciled):
            stored=self.repo.update_job(record.job_id,status='applied',notes='keep this')
        self.assertEqual(calls,2)
        self.assertEqual((stored.status,stored.notes,stored.availability['state']),('applied','keep this','missing'))


if __name__=='__main__':unittest.main()
