from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from job_finder.api import create_source, export_jobs, get_digest, list_jobs
from job_finder.ats.ashby import fetch_ashby_jobs
from job_finder.ats.greenhouse import fetch_greenhouse_jobs
from job_finder.ats.lever import fetch_lever_jobs
from job_finder.collector import build_job_record
from job_finder.models import SourceConfig
from job_finder.normalize import classify_us_location, detect_remote_type
from job_finder.scoring import build_feedback_profile
from job_finder.server import populate_demo
from job_finder.storage import DynamoJobRepository, LocalJsonRepository


def make_job(identifier="1", **overrides):
    return build_job_record(SourceConfig(company="Acme", source_type="greenhouse", identifier="acme"), {
        "source_job_id": identifier, "title": "DevOps Engineer", "location_text": "Remote, United States",
        "description_text": "AWS Terraform Ansible CI/CD Linux cloud infrastructure", "apply_url": f"https://example.com/{identifier}",
        **overrides,
    }, None)


class EligibilityTests(unittest.TestCase):
    def test_location_tokens_do_not_match_substrings(self):
        for location in ["Remote - Austria", "Remote - Brussels", "Remote - Auckland", "Remote - Amsterdam"]:
            with self.subTest(location=location):
                self.assertNotEqual(classify_us_location(location, "remote"), "confirmed")
        for location in ["Remote - Canada", "Remote - Brazil", "Remote - UK"]:
            self.assertEqual(classify_us_location(location, "remote"), "non_us")

    def test_new_mexico_is_a_us_state(self):
        self.assertEqual(classify_us_location("Remote - New Mexico", "remote"), "confirmed")

    def test_multiple_countries_keep_explicit_us_availability(self):
        self.assertEqual(classify_us_location("Remote - United States or Canada", "remote"), "confirmed")
        self.assertEqual(classify_us_location("Remote - US / UK", "remote"), "confirmed")

    def test_explicit_us_exclusion(self):
        for location in ["Remote outside the US", "Remote excluding United States", "Remote not available in the USA"]:
            with self.subTest(location=location):
                self.assertEqual(classify_us_location(location, "remote"), "non_us")

    def test_non_remote_and_hybrid_override_remote_hint(self):
        for location in ["US - not remote", "US - no remote", "US - remote work is not available", "US - on-site only"]:
            self.assertEqual(detect_remote_type(location, True), "onsite")
        self.assertEqual(detect_remote_type("Hybrid, US", True), "hybrid")
        self.assertEqual(detect_remote_type("Work from home, United States"), "remote")


class RankingTests(unittest.TestCase):
    def test_words_inside_other_words_do_not_score(self):
        job = make_job(title="Business Relationship Engineer", description_text="Abide by laws. Manage a drawstring helm of affairs.")
        self.assertNotIn("mentions aws", job.score_reasons)
        self.assertNotIn("platform/SRE title", job.score_reasons)
        self.assertNotIn("Kubernetes-heavy profile", job.score_reasons)
        self.assertLess(job.score, 30)

    def test_title_synonyms_do_not_multiply_score(self):
        simple = make_job(title="Infrastructure Engineer")
        nested = make_job(title="Platform Infrastructure Engineer")
        self.assertLessEqual(nested.score - simple.score, 2)

    def test_keyword_stuffing_does_not_rescue_an_off_target_role(self):
        keywords = "AWS Terraform Ansible CI/CD GitLab GitHub Actions automation observability Prometheus Grafana Linux RPM packaging infrastructure as code"
        for title in ["Tax Manager", "Senior Salesforce Engineer", "Data Scientist", "Finance Analyst"]:
            with self.subTest(title=title):
                self.assertLess(make_job(title=title, description_text=keywords).score, 30)
        self.assertLess(make_job(title="Software Engineer", description_text="AWS Terraform Ansible CI/CD Linux").score, 30)
        self.assertGreater(make_job(title="Software Engineer", description_text=keywords + " Own deployment pipelines and production reliability.").score, 60)

    def test_incidental_kubernetes_is_not_called_heavy(self):
        incidental = make_job(description_text="AWS Terraform Ansible CI/CD Linux Kubernetes")
        heavy = make_job(description_text="AWS Terraform Ansible CI/CD Linux Kubernetes Helm service mesh")
        self.assertNotIn("Kubernetes-heavy profile", incidental.score_reasons)
        self.assertIn("Kubernetes-heavy profile", heavy.score_reasons)
        self.assertGreater(incidental.score, heavy.score)

    def test_business_systems_specialization_needs_title_and_platform_evidence(self):
        description="Own Salesforce SFDX and NetSuite release pipelines. AWS Terraform CI/CD Linux cloud infrastructure."
        specialist=make_job(title="GTM DevOps Engineer",description_text=description)
        self.assertLess(specialist.score,30)
        self.assertIn("business-systems specialization mismatch",specialist.score_reasons)
        infrastructure=make_job(title="Senior DevOps Engineer",description_text=description)
        self.assertGreater(infrastructure.score,60)
        generic_gtm=make_job(title="GTM Platform Engineer",description_text="AWS Terraform Linux cloud infrastructure")
        self.assertGreater(generic_gtm.score,60)

    def test_cautions_survive_long_positive_reason_list(self):
        job = make_job(description_text="AWS Terraform Ansible CI/CD GitLab automation Linux RPM packaging observability. Contract role requiring security clearance and Kubernetes Helm.")
        self.assertIn("contract role", job.score_reasons)
        self.assertIn("clearance preference mismatch", job.score_reasons)
        self.assertIn("Kubernetes-heavy profile", job.score_reasons)

    def test_duplicate_and_timing_feedback_does_not_poison_role_fit(self):
        for reason in ["already_seen", "low_priority", "not_us"]:
            job = make_job()
            job.status = "dismissed"
            job.metadata["dismiss_reason"] = reason
            self.assertTrue(build_feedback_profile([job]).is_empty)

    def test_company_feedback_does_not_punish_title(self):
        job = make_job()
        job.status = "dismissed"
        job.metadata["dismiss_reason"] = "weak_company_fit"
        profile = build_feedback_profile([job])
        self.assertEqual(profile.title_token_weights, {})
        self.assertLess(profile.company_weights["acme"], 0)


class SearchAndDigestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = LocalJsonRepository(jobs_path=f"{self.temporary.name}/jobs.json", runs_path=f"{self.temporary.name}/runs.json")

    def test_search_before_pagination_and_export_use_same_filters(self):
        for index in range(12):
            job = make_job(str(index), description_text="AWS Terraform " + ("needle only here" if index >= 10 else "ordinary"))
            self.repo.upsert_job(job)
        first = list_jobs(self.repo, {"search": "needle", "limit": "1"})
        second = list_jobs(self.repo, {"search": "needle", "limit": "1", "cursor": first["next_cursor"]})
        self.assertEqual(first["count"], 1)
        self.assertEqual(second["count"], 1)
        self.assertNotEqual(first["items"][0]["job_id"], second["items"][0]["job_id"])
        self.assertIsNone(second["next_cursor"])
        exported = json.loads(export_jobs(self.repo, {"search": "needle", "format": "json"})["body"])
        self.assertEqual(exported["count"], 2)
        with self.assertRaises(ValueError):
            list_jobs(self.repo, {"search": "different", "cursor": first["next_cursor"]})

    def test_since_visit_and_confirmed_us_filters(self):
        old = make_job("old")
        old.first_seen_at = (datetime.now(UTC) - timedelta(days=4)).isoformat()
        self.repo.upsert_job(old)
        self.repo.upsert_job(make_job("recent"))
        self.repo.upsert_job(make_job("unclear", location_text="Remote"))
        query = {"found_after": (datetime.now(UTC) - timedelta(days=1)).isoformat(), "confirmed_us_only": "true"}
        self.assertEqual(list_jobs(self.repo, query)["count"], 1)
        self.assertEqual(json.loads(export_jobs(self.repo, {**query, "format": "json"})["body"])["count"], 1)
        for value in ["yesterday", "2026-04-01T00:00:00"]:
            with self.assertRaises(ValueError):
                list_jobs(self.repo, {"found_after": value})

    def test_daily_digest_skips_old_duplicate_reviewed_and_unclear_jobs(self):
        old = make_job("old")
        old.first_seen_at = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        self.repo.upsert_job(old)
        self.repo.upsert_job(make_job("stale", title="Stale DevOps Engineer", posted_date=(datetime.now(UTC) - timedelta(days=60)).isoformat()))
        self.repo.upsert_job(make_job("unclear", title="Unclear DevOps Engineer", location_text="Remote"))
        reviewed = make_job("reviewed", title="Reviewed DevOps Engineer")
        reviewed.status = "applied"
        self.repo.upsert_job(reviewed)
        self.repo.upsert_job(make_job("repost", title=reviewed.title))
        self.repo.upsert_job(make_job("fresh", title="Fresh DevOps Engineer"))
        self.repo.upsert_job(make_job("duplicate", title="Fresh DevOps Engineer"))
        digest = get_digest(self.repo, {})
        self.assertEqual(digest["count"], 1)
        self.assertEqual(digest["items"][0]["title"], "Fresh DevOps Engineer")
        self.assertEqual(digest["hours"], 24)
        self.assertGreater(get_digest(self.repo, {"hours": "72"})["count"], 1)

    def test_company_diversity_does_not_drop_other_companies(self):
        for index in range(6):
            job = make_job(str(index), title=f"DevOps Engineer {index}")
            job.company = "One" if index < 5 else "Two"
            self.repo.upsert_job(job)
        digest = get_digest(self.repo, {"limit": "3"})
        self.assertEqual(digest["count"], 3)
        self.assertEqual({item["company"] for item in digest["items"]}, {"One", "Two"})
        self.assertEqual(get_digest(self.repo, {"limit": "-10"})["count"], 1)

    def test_follow_source_from_url(self):
        result = create_source(self.repo, {"url": "https://jobs.lever.co/acme/a-job-id/apply"})
        self.assertEqual(result["item"]["identifier"], "acme")
        self.assertEqual(result["item"]["status"], "manual")
        for url in ["https://jobs.lever.co.evil.example/acme", "http://jobs.lever.co/acme", "https://example.com/careers"]:
            with self.assertRaises(ValueError):
                create_source(self.repo, {"url": url})

    def test_company_fit_survives_source_catalog_round_trip(self):
        source = SourceConfig("Acme", "greenhouse", "acme", company_size="small", company_tags=["saas"])
        self.repo.upsert_source(source)
        restored = self.repo.list_sources()[0]
        self.assertEqual(restored.company_size, "small")
        self.assertEqual(restored.company_tags, ["saas"])
        self.repo.upsert_source(SourceConfig("Acme", "greenhouse", "acme"))
        self.assertEqual(self.repo.list_sources()[0].company_size, "small")

    def test_demo_exercises_real_api_and_review_workflow(self):
        populate_demo(self.repo)
        digest = get_digest(self.repo, {})
        self.assertGreater(digest["count"], 0)
        first_id = digest["items"][0]["job_id"]
        self.repo.update_job(first_id, status="saved")
        self.assertNotIn(first_id, [item["job_id"] for item in get_digest(self.repo, {})["items"]])
        self.assertEqual(list_jobs(self.repo, {"status": "saved"})["count"], 1)


class ProviderTests(unittest.TestCase):
    def test_greenhouse_uses_publication_not_edit_date(self):
        payload = {"jobs": [{"id": 1, "title": "DevOps Engineer", "first_published": "2026-01-01", "updated_at": "2026-11-14"}]}
        with patch("job_finder.ats.greenhouse.fetch_json", return_value=(payload, "{}")):
            _, jobs = fetch_greenhouse_jobs(SourceConfig("Acme", "greenhouse", "acme"))
        self.assertEqual(jobs[0]["posted_date"], "2026-01-01")

    def test_lever_reads_top_level_workplace_type(self):
        payload = [{"id": "1", "text": "DevOps Engineer", "workplaceType": "remote", "categories": {"location": "United States"}}]
        with patch("job_finder.ats.lever.fetch_json", return_value=(payload, "[]")):
            _, jobs = fetch_lever_jobs(SourceConfig("Acme", "lever", "acme"))
        self.assertTrue(build_job_record(SourceConfig("Acme", "lever", "acme"), jobs[0], None).is_eligible)

    def test_ashby_hybrid_and_secondary_us_location(self):
        payload = {"jobs": [{"id": "1", "title": "DevOps Engineer", "location": "Canada", "workplaceType": "Remote", "secondaryLocations": [{"location": "United States"}]}, {"id": "2", "title": "DevOps Engineer", "location": "Remote US", "isRemote": True, "workplaceType": "Hybrid"}, {"id": "3", "isListed": False}]}
        source = SourceConfig("Acme", "ashby", "acme")
        with patch("job_finder.ats.ashby.fetch_json", return_value=(payload, "{}")):
            _, jobs = fetch_ashby_jobs(source)
        self.assertEqual(len(jobs), 2)
        self.assertTrue(build_job_record(source, jobs[0], None).is_eligible)
        self.assertFalse(build_job_record(source, jobs[1], None).is_eligible)

    def test_aws_feedback_keeps_dismiss_reason_and_duplicate_identity(self):
        class Condition:
            def __init__(self, *args): pass
            def eq(self, *args): return self
            def __or__(self, other): return self
        class Table:
            def scan(self, **kwargs):
                self.projection = kwargs["ProjectionExpression"]
                return {"Items": [{"title": "DevOps Engineer", "company": "Acme", "location_text": "Remote, United States", "status": "dismissed", "metadata": {"dismiss_reason": "already_seen"}}]}
        table = Table()
        repository = object.__new__(DynamoJobRepository)
        repository.jobs_table = table
        fake = types.ModuleType("boto3.dynamodb.conditions")
        fake.Attr = Condition
        repository.list_jobs = lambda **query: [make_job("reposted-copy")]
        with patch.dict(sys.modules, {"boto3.dynamodb.conditions": fake}):
            jobs = repository.list_feedback_jobs()
            digest = get_digest(repository, {})
        self.assertEqual(digest["count"], 0)
        self.assertEqual(jobs[0].location_text, "Remote, United States")
        self.assertIn("location_text", table.projection)
        self.assertIn("#metadata", table.projection)
        self.assertEqual(jobs[0].metadata["dismiss_reason"], "already_seen")
        self.assertTrue(build_feedback_profile(jobs).is_empty)


if __name__ == "__main__":
    unittest.main()
