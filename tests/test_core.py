from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from job_finder.api import export_jobs, get_digest, get_freshness, get_job, list_jobs, list_sources, update_job, update_source_control
from job_finder.collector import build_job_record
from job_finder.discovery import discover_sources, extract_source_from_url
from job_finder.lambda_handlers import _emit_crawl_metrics, api_handler
from job_finder.models import CrawlRunRecord, DiscoverySettings, JobRecord, SourceConfig, stable_source_id
from job_finder.normalize import classify_us_location
from job_finder.scoring import build_feedback_profile
from job_finder.storage import LocalJsonRepository, LocalPayloadStore


def make_source(**overrides: object) -> SourceConfig:
    payload: dict[str, object] = {
        "company": "Acme",
        "source_type": "greenhouse",
        "identifier": "acme",
        "company_size": "mid",
        "company_tags": ["saas"],
        "careers_url": "https://boards.greenhouse.io/acme",
    }
    payload.update(overrides)
    return SourceConfig.from_dict(payload)


class BuildJobRecordTests(unittest.TestCase):
    def test_scores_remote_us_infrastructure_role(self) -> None:
        job = build_job_record(
            make_source(),
            {
                "source_job_id": "123",
                "title": "Senior DevOps Engineer",
                "location_text": "Remote, United States",
                "description_text": "AWS Terraform Ansible CI/CD automation observability Linux RPM packaging",
                "apply_url": "https://example.com/jobs/123?utm_source=test",
            },
            raw_payload_key=None,
        )

        self.assertTrue(job.is_eligible)
        self.assertTrue(job.is_remote)
        self.assertTrue(job.is_us_based)
        self.assertEqual(job.canonical_url, "https://example.com/jobs/123")
        self.assertGreater(job.score, 90)
        self.assertIn("remote US confirmed", job.score_reasons)
        self.assertIn("mentions aws", job.score_reasons)

    def test_penalizes_non_us_kubernetes_heavy_role(self) -> None:
        job = build_job_record(
            make_source(),
            {
                "source_job_id": "456",
                "title": "Platform Engineer",
                "location_text": "Remote, Canada",
                "description_text": "Kubernetes K8s Helm operators and service mesh",
                "apply_url": "https://example.com/jobs/456",
            },
            raw_payload_key=None,
        )

        self.assertFalse(job.is_eligible)
        self.assertFalse(job.is_us_based)
        self.assertLess(job.score, 0)
        self.assertIn("Kubernetes-heavy profile", job.score_reasons)

    def test_keeps_generic_remote_as_us_compatible_with_lower_confidence(self) -> None:
        job = build_job_record(
            make_source(),
            {
                "source_job_id": "777",
                "title": "Site Reliability Engineer",
                "location_text": "Remote",
                "description_text": "AWS Terraform observability",
                "apply_url": "https://example.com/jobs/777",
            },
            raw_payload_key=None,
        )

        self.assertTrue(job.is_eligible)
        self.assertEqual(job.us_location_confidence, "assumed")
        self.assertIn("remote, US not explicit", job.score_reasons)

    def test_penalizes_off_target_salesforce_role_even_with_keywords(self) -> None:
        job = build_job_record(
            make_source(),
            {
                "source_job_id": "778",
                "title": "Senior Salesforce Engineer",
                "location_text": "Remote, United States",
                "description_text": "AWS CI/CD automation for revenue systems",
                "apply_url": "https://example.com/jobs/778",
            },
            raw_payload_key=None,
        )

        self.assertLess(job.score, 10)
        self.assertIn("off-target title family", job.score_reasons)

    def test_penalizes_business_operations_titles(self) -> None:
        job = build_job_record(
            make_source(),
            {
                "source_job_id": "781",
                "title": "Senior Associate, Business Operations",
                "location_text": "Remote, United States",
                "description_text": "AWS for internal tools",
                "apply_url": "https://example.com/jobs/781",
            },
            raw_payload_key=None,
        )

        self.assertLess(job.score, 10)
        self.assertIn("off-target title family", job.score_reasons)

    def test_penalizes_customer_operations_titles_with_incidental_keywords(self) -> None:
        job = build_job_record(
            make_source(),
            {
                "source_job_id": "785",
                "title": "Head of Customer Operations",
                "location_text": "Remote, United States",
                "description_text": "Own partner support operations, contractual support obligations, AWS workflows, and automation tooling.",
                "apply_url": "https://example.com/jobs/785",
            },
            raw_payload_key=None,
        )

        self.assertLess(job.score, 20)
        self.assertIn("off-target title family", job.score_reasons)
        self.assertNotIn("contract role", job.score_reasons)

    def test_penalizes_sdet_roles_even_with_cloud_keywords(self) -> None:
        job = build_job_record(
            make_source(),
            {
                "source_job_id": "782",
                "title": "Senior Software Development Engineer in Test (SDET)",
                "location_text": "Remote, United States",
                "description_text": "AWS CI/CD Playwright Cypress Selenium test automation quality assurance",
                "apply_url": "https://example.com/jobs/782",
            },
            raw_payload_key=None,
        )

        self.assertLess(job.score, 20)
        self.assertIn("QA/test-heavy role", job.score_reasons)

    def test_penalizes_mobile_platform_roles_that_are_not_infra(self) -> None:
        job = build_job_record(
            make_source(),
            {
                "source_job_id": "783",
                "title": "Senior Software Engineer, Mobile Platform",
                "location_text": "Remote, United States",
                "description_text": "AWS CI/CD automation React Native iOS Android App Store Google Play mobile distribution",
                "apply_url": "https://example.com/jobs/783",
            },
            raw_payload_key=None,
        )

        self.assertLess(job.score, 30)
        self.assertIn("mobile platform role", job.score_reasons)

    def test_penalizes_ai_data_leadership_roles_with_cloud_mentions(self) -> None:
        job = build_job_record(
            make_source(),
            {
                "source_job_id": "784",
                "title": "Senior Director, Applied Data Science and AI",
                "location_text": "Remote, United States",
                "description_text": "AWS LLM LangChain RAG vector databases PyTorch TensorFlow automation",
                "apply_url": "https://example.com/jobs/784",
            },
            raw_payload_key=None,
        )

        self.assertLess(job.score, 20)
        self.assertIn("AI/data role", job.score_reasons)

    def test_feedback_profile_downranks_similar_dismissed_titles(self) -> None:
        dismissed = build_job_record(
            make_source(),
            {
                "source_job_id": "779",
                "title": "Senior Salesforce Engineer",
                "location_text": "Remote, United States",
                "description_text": "AWS automation",
                "apply_url": "https://example.com/jobs/779",
            },
            raw_payload_key=None,
        )
        dismissed.status = "dismissed"
        feedback_profile = build_feedback_profile([dismissed])

        similar = build_job_record(
            make_source(),
            {
                "source_job_id": "780",
                "title": "Salesforce Architect",
                "location_text": "Remote, United States",
                "description_text": "AWS CI/CD automation",
                "apply_url": "https://example.com/jobs/780",
            },
            raw_payload_key=None,
            feedback_profile=feedback_profile,
        )

        self.assertIn("similar to dismissed roles", similar.score_reasons)
        without_feedback = build_job_record(
            make_source(),
            {
                "source_job_id": "780",
                "title": "Salesforce Architect",
                "location_text": "Remote, United States",
                "description_text": "AWS CI/CD automation",
                "apply_url": "https://example.com/jobs/780",
            },
            raw_payload_key=None,
        )
        self.assertLess(similar.score, without_feedback.score)

    def test_dismiss_reason_strengthens_negative_title_feedback(self) -> None:
        dismissed = build_job_record(
            make_source(),
            {
                "source_job_id": "786",
                "title": "Customer Operations Manager",
                "location_text": "Remote, United States",
                "description_text": "AWS automation workflows for support operations",
                "apply_url": "https://example.com/jobs/786",
            },
            raw_payload_key=None,
        )
        dismissed.status = "dismissed"
        dismissed.metadata["dismiss_reason"] = "not_infrastructure"
        feedback_profile = build_feedback_profile([dismissed])

        similar = build_job_record(
            make_source(),
            {
                "source_job_id": "787",
                "title": "Customer Operations Lead",
                "location_text": "Remote, United States",
                "description_text": "AWS automation workflows",
                "apply_url": "https://example.com/jobs/787",
            },
            raw_payload_key=None,
            feedback_profile=feedback_profile,
        )

        self.assertIn("similar to dismissed roles", similar.score_reasons)


class LocalRepositoryTests(unittest.TestCase):
    def test_status_updates_do_not_change_scrape_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
            )
            job = build_job_record(
                make_source(),
                {
                    "source_job_id": "123",
                    "title": "DevOps Engineer",
                    "location_text": "Remote, United States",
                    "description_text": "AWS and Terraform",
                    "apply_url": "https://example.com/jobs/123",
                },
                raw_payload_key=None,
            )
            repo.upsert_job(job)

            updated = repo.update_job(job.job_id, status="saved", notes="Strong fit")
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated.status, "saved")
            self.assertEqual(updated.notes, "Strong fit")
            self.assertEqual(updated.last_seen_at, job.last_seen_at)

    def test_dismiss_reason_is_persisted_and_preserved_across_upserts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
            )
            job = build_job_record(
                make_source(),
                {
                    "source_job_id": "125",
                    "title": "Platform Engineer",
                    "location_text": "Remote, United States",
                    "description_text": "AWS Terraform",
                    "apply_url": "https://example.com/jobs/125",
                },
                raw_payload_key=None,
            )
            repo.upsert_job(job)

            updated = repo.update_job(job.job_id, status="dismissed", dismiss_reason="not_infrastructure")
            assert updated is not None
            self.assertEqual(updated.metadata["dismiss_reason"], "not_infrastructure")
            self.assertIn("status_updated_at", updated.metadata)

            refreshed = build_job_record(
                make_source(),
                {
                    "source_job_id": "125",
                    "title": "Platform Engineer",
                    "location_text": "Remote, United States",
                    "description_text": "AWS Terraform observability",
                    "apply_url": "https://example.com/jobs/125",
                },
                raw_payload_key=None,
            )
            repo.upsert_job(refreshed)
            persisted = repo.get_job(job.job_id)
            assert persisted is not None
            self.assertEqual(persisted.status, "dismissed")
            self.assertEqual(persisted.metadata["dismiss_reason"], "not_infrastructure")

    def test_repository_sorts_by_score_before_recency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
            )
            lower_score = build_job_record(
                make_source(),
                {
                    "source_job_id": "150",
                    "title": "Platform Engineer",
                    "location_text": "Remote, United States",
                    "description_text": "AWS Terraform",
                    "apply_url": "https://example.com/jobs/150",
                },
                raw_payload_key=None,
            )
            higher_score = build_job_record(
                make_source(),
                {
                    "source_job_id": "151",
                    "title": "Senior DevOps Engineer",
                    "location_text": "Remote, United States",
                    "description_text": "AWS Terraform Ansible observability CI/CD Linux packaging",
                    "apply_url": "https://example.com/jobs/151",
                },
                raw_payload_key=None,
            )
            lower_score.first_seen_at = "2026-03-21T10:00:00+00:00"
            higher_score.first_seen_at = "2026-03-21T09:00:00+00:00"
            repo.upsert_job(lower_score)
            repo.upsert_job(higher_score)

            listed = repo.list_jobs(limit=10, eligible_only=False, only_new=False)
            self.assertEqual(listed[0].job_id, higher_score.job_id)

    def test_api_can_sort_by_newest_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
            )
            older_high_score = build_job_record(
                make_source(),
                {
                    "source_job_id": "160",
                    "title": "Senior DevOps Engineer",
                    "location_text": "Remote, United States",
                    "description_text": "AWS Terraform Ansible observability CI/CD Linux packaging",
                    "apply_url": "https://example.com/jobs/160",
                },
                raw_payload_key=None,
            )
            newer_lower_score = build_job_record(
                make_source(company="NewerCo"),
                {
                    "source_job_id": "161",
                    "title": "Platform Engineer",
                    "location_text": "Remote, United States",
                    "description_text": "AWS Terraform automation",
                    "apply_url": "https://example.com/jobs/161",
                },
                raw_payload_key=None,
            )
            older_high_score.first_seen_at = "2026-03-21T09:00:00+00:00"
            newer_lower_score.first_seen_at = "2026-03-21T10:00:00+00:00"
            repo.upsert_job(older_high_score)
            repo.upsert_job(newer_lower_score)

            payload = list_jobs(repo, {"eligible_only": "true", "limit": "10", "sort": "newest"})
            self.assertEqual(payload["items"][0]["job_id"], newer_lower_score.job_id)

    def test_newest_sort_uses_last_seen_as_tie_breaker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
            )
            older_seen = build_job_record(
                make_source(),
                {
                    "source_job_id": "same-crawl-old",
                    "title": "Platform Engineer",
                    "location_text": "Remote, United States",
                    "description_text": "AWS Terraform automation",
                    "apply_url": "https://example.com/jobs/same-crawl-old",
                },
                raw_payload_key=None,
            )
            newer_seen = build_job_record(
                make_source(company="FreshCo"),
                {
                    "source_job_id": "same-crawl-new",
                    "title": "Platform Engineer",
                    "location_text": "Remote, United States",
                    "description_text": "AWS Terraform automation",
                    "apply_url": "https://example.com/jobs/same-crawl-new",
                },
                raw_payload_key=None,
            )
            older_seen.first_seen_at = "2026-03-21T10:00:00+00:00"
            newer_seen.first_seen_at = "2026-03-21T10:00:00+00:00"
            older_seen.last_seen_at = "2026-03-21T10:01:00+00:00"
            newer_seen.last_seen_at = "2026-03-21T10:02:00+00:00"
            repo.upsert_job(older_seen)
            repo.upsert_job(newer_seen)

            listed = repo.list_jobs(limit=10, eligible_only=True, only_new=False, sort_by="newest")
            self.assertEqual(listed[0].job_id, newer_seen.job_id)

    def test_jobs_api_returns_cursor_for_next_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
            )
            for index in range(3):
                job = build_job_record(
                    make_source(company=f"PageCo{index}"),
                    {
                        "source_job_id": f"page-{index}",
                        "title": "Senior DevOps Engineer",
                        "location_text": "Remote, United States",
                        "description_text": "AWS Terraform observability CI/CD",
                        "apply_url": f"https://example.com/jobs/page-{index}",
                    },
                    raw_payload_key=None,
                )
                job.first_seen_at = f"2026-03-21T10:0{index}:00+00:00"
                repo.upsert_job(job)

            first_page = list_jobs(repo, {"eligible_only": "true", "limit": "2", "sort": "newest"})
            self.assertEqual(first_page["count"], 2)
            self.assertIsNotNone(first_page["next_cursor"])

            second_page = list_jobs(
                repo,
                {
                    "eligible_only": "true",
                    "limit": "2",
                    "sort": "newest",
                    "cursor": first_page["next_cursor"],
                },
            )
            self.assertEqual(second_page["count"], 1)
            self.assertIsNone(second_page["next_cursor"])
            self.assertNotEqual(first_page["items"][0]["job_id"], second_page["items"][0]["job_id"])

            with self.assertRaises(ValueError):
                list_jobs(
                    repo,
                    {
                        "eligible_only": "true",
                        "limit": "2",
                        "sort": "ranked",
                        "cursor": first_page["next_cursor"],
                    },
                )

    def test_jobs_api_can_return_more_than_one_hundred_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
            )
            for index in range(125):
                repo.upsert_job(
                    JobRecord(
                        job_id=f"bulk-{index}",
                        source_job_id=f"bulk-{index}",
                        canonical_url=f"https://example.com/jobs/bulk-{index}",
                        apply_url=f"https://example.com/jobs/bulk-{index}",
                        source_type="lever",
                        source_name="BulkCo:bulk",
                        company="BulkCo",
                        title="Platform Engineer",
                        location_text="Remote, United States",
                        remote_type="remote",
                        posted_date=None,
                        first_seen_at=f"2026-03-21T10:{index // 60:02d}:{index % 60:02d}+00:00",
                        last_seen_at=f"2026-03-21T10:{index // 60:02d}:{index % 60:02d}+00:00",
                        description_text="AWS Terraform automation",
                        score=80,
                        score_reasons=["remote US confirmed"],
                        is_eligible=True,
                        is_remote=True,
                        is_us_based=True,
                    )
                )

            payload = list_jobs(repo, {"eligible_only": "true", "limit": "125", "sort": "newest"})
            self.assertEqual(payload["count"], 125)
            self.assertIsNone(payload["next_cursor"])

    def test_feedback_jobs_only_include_labeled_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
            )
            dismissed_job = build_job_record(
                make_source(company="DismissedCo"),
                {
                    "source_job_id": "170",
                    "title": "Platform Engineer",
                    "location_text": "Remote, United States",
                    "description_text": "AWS Terraform automation",
                    "apply_url": "https://example.com/jobs/170",
                },
                raw_payload_key=None,
            )
            dismissed_job.status = "dismissed"
            dismissed_job.first_seen_at = "2026-03-21T11:00:00+00:00"
            new_job = build_job_record(
                make_source(company="FreshCo"),
                {
                    "source_job_id": "171",
                    "title": "Senior DevOps Engineer",
                    "location_text": "Remote, United States",
                    "description_text": "AWS Terraform observability CI/CD",
                    "apply_url": "https://example.com/jobs/171",
                },
                raw_payload_key=None,
            )
            new_job.status = "new"
            new_job.first_seen_at = "2026-03-21T12:00:00+00:00"
            repo.upsert_job(dismissed_job)
            repo.upsert_job(new_job)

            feedback_jobs = repo.list_feedback_jobs(limit=10)
            self.assertEqual(len(feedback_jobs), 1)
            self.assertEqual(feedback_jobs[0].company, "DismissedCo")
            self.assertEqual(feedback_jobs[0].status, "dismissed")

    def test_freshness_reports_recent_completed_crawl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
            )
            finished_at = (datetime.now(UTC) - timedelta(hours=2)).replace(microsecond=0).isoformat()
            repo.record_crawl_run(
                CrawlRunRecord(
                    run_id="run-recent",
                    started_at=(datetime.now(UTC) - timedelta(hours=3)).replace(microsecond=0).isoformat(),
                    finished_at=finished_at,
                    jobs_found=12,
                    jobs_inserted=3,
                    jobs_updated=9,
                )
            )

            payload = get_freshness(repo, {"max_age_hours": "24"})
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["latest_run"]["run_id"], "run-recent")
            self.assertLess(payload["age_seconds"], 24 * 3600)

    def test_freshness_reports_stale_completed_crawl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
            )
            repo.record_crawl_run(
                CrawlRunRecord(
                    run_id="run-stale",
                    started_at="2026-03-21T00:00:00+00:00",
                    finished_at="2026-03-21T00:05:00+00:00",
                )
            )

            payload = get_freshness(repo, {"max_age_hours": "24"})
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["latest_run"]["run_id"], "run-stale")

    def test_api_filters_and_updates_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_path = Path(temp_dir) / "jobs.json"
            repo = LocalJsonRepository(jobs_path=str(jobs_path), runs_path=str(Path(temp_dir) / "runs.json"))

            good_job = build_job_record(
                make_source(),
                {
                    "source_job_id": "123",
                    "title": "DevOps Engineer",
                    "location_text": "Remote, United States",
                    "description_text": "AWS Terraform observability",
                    "apply_url": "https://example.com/jobs/123",
                },
                raw_payload_key=None,
            )
            low_job = build_job_record(
                make_source(company="OtherCo"),
                {
                    "source_job_id": "999",
                    "title": "Linux Administrator",
                    "location_text": "Boston, Massachusetts",
                    "description_text": "Onsite support role",
                    "apply_url": "https://example.com/jobs/999",
                },
                raw_payload_key=None,
            )
            repo.upsert_job(good_job)
            repo.upsert_job(low_job)

            payload = list_jobs(repo, {"min_score": "25", "eligible_only": "true", "limit": "10"})
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["items"][0]["job_id"], good_job.job_id)

            updated = update_job(repo, good_job.job_id, {"status": "applied", "notes": "Sent application"})
            self.assertEqual(updated["status"], "applied")
            self.assertEqual(updated["notes"], "Sent application")
            self.assertIn("applied_at", updated["metadata"])

            stored = json.loads(jobs_path.read_text(encoding="utf-8"))
            self.assertEqual(stored[0]["status"], "applied")

            dismissed = update_job(repo, good_job.job_id, {"status": "dismissed", "dismiss_reason": "off_target"})
            self.assertEqual(dismissed["metadata"]["dismiss_reason"], "off_target")

            with self.assertRaises(ValueError):
                update_job(repo, good_job.job_id, {"dismiss_reason": "bad_reason"})

    def test_jobs_list_uses_summary_shape_and_detail_keeps_full_description(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
            )
            job = build_job_record(
                make_source(),
                {
                    "source_job_id": "220",
                    "title": "Senior DevOps Engineer",
                    "location_text": "Remote, United States",
                    "description_text": "AWS Terraform observability automation " * 30,
                    "apply_url": "https://example.com/jobs/220",
                },
                raw_payload_key="raw/run/test.json",
            )
            job.metadata["large"] = "metadata"
            repo.upsert_job(job)

            listed = list_jobs(repo, {"limit": "10"})
            summary = listed["items"][0]
            self.assertNotIn("description_text", summary)
            self.assertNotIn("metadata", summary)
            self.assertNotIn("raw_payload_s3_key", summary)
            self.assertIn("description_excerpt", summary)
            self.assertTrue(summary["has_description"])
            self.assertLess(len(summary["description_excerpt"]), len(job.description_text))

            detail = get_job(repo, job.job_id)
            self.assertEqual(detail["description_text"], job.description_text)
            self.assertEqual(detail["metadata"]["large"], "metadata")

    def test_jobs_export_supports_csv_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
            )
            job = build_job_record(
                make_source(company="ExportCo"),
                {
                    "source_job_id": "230",
                    "title": "Senior DevOps Engineer",
                    "location_text": "Remote, United States",
                    "description_text": "AWS Terraform observability automation",
                    "apply_url": "https://example.com/jobs/230",
                },
                raw_payload_key=None,
            )
            repo.upsert_job(job)
            updated = repo.update_job(job.job_id, status="applied", notes="Follow up next week")
            assert updated is not None

            csv_export = export_jobs(repo, {"format": "csv", "status": "applied"})
            self.assertEqual(csv_export["content_type"], "text/csv; charset=utf-8")
            self.assertIn("ExportCo", csv_export["body"])
            self.assertIn("Follow up next week", csv_export["body"])
            self.assertIn("applied_at", csv_export["body"].splitlines()[0])

            json_export = export_jobs(repo, {"format": "json", "status": "applied"})
            parsed = json.loads(json_export["body"])
            self.assertEqual(parsed["count"], 1)
            self.assertEqual(parsed["items"][0]["company"], "ExportCo")

    def test_digest_returns_top_new_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
            )
            top_job = build_job_record(
                make_source(company="TopCo"),
                {
                    "source_job_id": "201",
                    "title": "Senior DevOps Engineer",
                    "location_text": "Remote, United States",
                    "description_text": "AWS Terraform observability CI/CD automation",
                    "apply_url": "https://example.com/jobs/201",
                },
                raw_payload_key=None,
            )
            lower_job = build_job_record(
                make_source(company="OtherCo"),
                {
                    "source_job_id": "202",
                    "title": "Platform Engineer",
                    "location_text": "Remote, United States",
                    "description_text": "AWS Terraform",
                    "apply_url": "https://example.com/jobs/202",
                },
                raw_payload_key=None,
            )
            repo.upsert_job(lower_job)
            repo.upsert_job(top_job)

            payload = get_digest(repo, {"limit": "1", "min_score": "40"})
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["items"][0]["company"], "TopCo")
            self.assertEqual(payload["summary"]["top_companies"][0][0], "TopCo")

    def test_sources_endpoint_reports_quality(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
                sources_path=str(Path(temp_dir) / "sources.json"),
            )
            source = make_source(company="netlify", source_type="lever", identifier="netlify")
            repo.upsert_source(source)
            repo.record_source_result(
                source,
                was_success=True,
                jobs_found=12,
                eligible_jobs=6,
                high_score_jobs=3,
                total_score=480,
                top_score=92,
            )

            payload = list_sources(repo, {"limit": "10", "statuses": "active,candidate"})
            self.assertEqual(payload["count"], 1)
            self.assertGreater(payload["items"][0]["quality_score"], 0)
            self.assertEqual(payload["items"][0]["last_high_score_jobs"], 3)

    def test_source_control_updates_are_exposed_by_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
                sources_path=str(Path(temp_dir) / "sources.json"),
            )
            source = make_source(company="noisyco", source_type="greenhouse", identifier="noisyco")
            repo.upsert_source(source)

            payload = update_source_control(
                repo,
                stable_source_id(source.source_type, source.identifier),
                {"blocked": True, "priority": 25, "note": "too noisy"},
            )

            self.assertTrue(payload["metadata"]["blocked"])
            self.assertEqual(payload["metadata"]["priority"], 25)
            self.assertEqual(payload["metadata"]["control_note"], "too noisy")

    def test_source_quality_prefers_strong_match_boards_over_large_noisy_boards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
                sources_path=str(Path(temp_dir) / "sources.json"),
            )
            strong_source = make_source(company="strongco", source_type="lever", identifier="strongco")
            noisy_source = make_source(company="noisyco", source_type="greenhouse", identifier="noisyco")
            repo.upsert_source(strong_source)
            repo.upsert_source(noisy_source)
            repo.record_source_result(
                strong_source,
                was_success=True,
                jobs_found=12,
                eligible_jobs=8,
                high_score_jobs=4,
                total_score=0,
                top_score=112,
            )
            for _ in range(3):
                repo.record_source_result(
                    noisy_source,
                    was_success=True,
                    jobs_found=120,
                    eligible_jobs=48,
                    high_score_jobs=0,
                    total_score=0,
                    top_score=54,
                )

            payload = list_sources(repo, {"limit": "10", "statuses": "active,low_value"})
            self.assertEqual(payload["items"][0]["company"], "strongco")
            self.assertLess(payload["items"][1]["quality_score"], payload["items"][0]["quality_score"])

    def test_noisy_sources_are_marked_low_value_by_quality_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
                sources_path=str(Path(temp_dir) / "sources.json"),
            )
            source = make_source(company="noisyco", source_type="greenhouse", identifier="noisyco")
            repo.upsert_source(source)
            for _ in range(3):
                repo.record_source_result(
                    source,
                    was_success=True,
                    jobs_found=120,
                    eligible_jobs=48,
                    high_score_jobs=0,
                    total_score=0,
                    top_score=54,
                )

            payload = list_sources(repo, {"limit": "10", "statuses": "low_value"})
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["items"][0]["company"], "noisyco")


class DiscoveryTests(unittest.TestCase):
    def test_extract_source_from_provider_urls(self) -> None:
        lever = extract_source_from_url("lever", "https://jobs.lever.co/netlify/platform-engineer")
        greenhouse = extract_source_from_url("greenhouse", "https://boards.greenhouse.io/clickhouse/jobs/5335613004")
        ashby = extract_source_from_url("ashby", "https://jobs.ashbyhq.com/notion/0f0d5160-fake")
        workable = extract_source_from_url("workable", "https://apply.workable.com/sourcegraph/j/ABC123")

        assert lever is not None
        assert greenhouse is not None
        assert ashby is not None
        assert workable is not None

        self.assertEqual(lever.identifier, "netlify")
        self.assertEqual(greenhouse.identifier, "clickhouse")
        self.assertEqual(ashby.identifier, "notion")
        self.assertEqual(workable.identifier, "sourcegraph")

    def test_collect_uses_discovery_when_no_manual_sources_are_provided(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
            )
            payload_store = LocalPayloadStore(base_path=str(Path(temp_dir) / "raw"))
            discovered_source = make_source(company="netlify", source_type="lever", identifier="netlify")

            with patch("job_finder.collector.discover_sources", return_value=[discovered_source]), patch(
                "job_finder.collector.fetch_jobs",
                return_value=(
                    {"_raw_body": "{\"jobs\": 1}"},
                    [
                        {
                            "source_job_id": "123",
                            "title": "Platform Engineer",
                            "location_text": "Remote, United States",
                            "description_text": "AWS Terraform automation",
                            "apply_url": "https://jobs.lever.co/netlify/123",
                        }
                    ],
                ),
            ):
                from job_finder.collector import collect_sources

                run = collect_sources(
                    [],
                    repo,
                    payload_store,
                    discovery_settings=DiscoverySettings(enabled=True, keywords=["platform"]),
                )

            self.assertEqual(run.source, "discovery")
            self.assertEqual(run.pages_checked, 1)
            self.assertEqual(run.jobs_found, 1)
            self.assertEqual(run.jobs_inserted, 1)

    def test_collect_reuses_persisted_sources_when_search_returns_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
                sources_path=str(Path(temp_dir) / "sources.json"),
            )
            payload_store = LocalPayloadStore(base_path=str(Path(temp_dir) / "raw"))
            persisted_source = make_source(company="netlify", source_type="lever", identifier="netlify")
            repo.upsert_source(persisted_source)

            with patch("job_finder.collector.discover_sources", return_value=[]), patch(
                "job_finder.collector.fetch_jobs",
                return_value=(
                    {"_raw_body": "{\"jobs\": 1}"},
                    [
                        {
                            "source_job_id": "456",
                            "title": "Platform Engineer",
                            "location_text": "Remote, United States",
                            "description_text": "AWS Terraform automation",
                            "apply_url": "https://jobs.lever.co/netlify/456",
                        }
                    ],
                ),
            ):
                from job_finder.collector import collect_sources

                run = collect_sources(
                    [],
                    repo,
                    payload_store,
                    discovery_settings=DiscoverySettings(enabled=True, keywords=["platform"], max_sources_per_run=5),
                )

            self.assertEqual(run.pages_checked, 1)
            self.assertEqual(run.jobs_inserted, 1)
            self.assertEqual(repo.list_sources(limit=10)[0].identifier, "netlify")

    def test_collect_skips_when_lock_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
            )
            payload_store = LocalPayloadStore(base_path=str(Path(temp_dir) / "raw"))
            repo.acquire_collector_lock(
                lock_id="existing-run",
                expires_at_epoch=int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
            )

            from job_finder.collector import collect_sources

            run = collect_sources(
                [],
                repo,
                payload_store,
                discovery_settings=DiscoverySettings(max_sources_per_run=1),
            )

            self.assertEqual(run.run_status, "skipped")
            self.assertEqual(run.pages_checked, 0)
            self.assertIsNone(repo.latest_crawl_run())

    def test_discovery_unions_multiple_strategies(self) -> None:
        with patch(
            "job_finder.discovery.discover_urls_via_bing_rss",
            return_value=["https://jobs.lever.co/netlify/platform-engineer"],
        ), patch(
            "job_finder.discovery.discover_urls_via_duckduckgo",
            return_value=["https://jobs.lever.co/clickhouse/platform-engineer"],
        ):
            settings = DiscoverySettings(
                strategies=["bing_rss", "duckduckgo"],
                keywords=["platform engineer"],
                source_types=["lever"],
            )
            sources = discover_sources(settings)

        self.assertEqual([(source.source_type, source.identifier) for source in sources], [("lever", "clickhouse"), ("lever", "netlify")])

    def test_failed_source_is_removed_from_active_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
                sources_path=str(Path(temp_dir) / "sources.json"),
            )
            source = make_source(company="deadco", source_type="ashby", identifier="deadco")
            repo.upsert_source(source)
            repo.record_source_result(
                source,
                was_success=False,
                error_message="deadco (ashby:deadco): HTTP Error 404: Not Found",
                disable_after_errors=2,
            )

            self.assertEqual(repo.list_sources(limit=10), [])

    def test_low_value_sources_are_only_rechecked_after_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            sources_path = Path(temp_dir) / "sources.json"
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
                sources_path=str(sources_path),
            )
            source = make_source(company="noisy", source_type="greenhouse", identifier="noisy")
            repo.upsert_source(source)
            for _ in range(3):
                repo.record_source_result(
                    source,
                    was_success=True,
                    jobs_found=120,
                    eligible_jobs=0,
                    high_score_jobs=0,
                    total_score=0,
                    top_score=0,
                )

            with patch("job_finder.collector.discover_sources", return_value=[]):
                from job_finder.collector import resolve_sources

                fresh_run_sources = resolve_sources(
                    [],
                    DiscoverySettings(max_sources_per_run=10, max_low_value_rechecks_per_run=1, low_value_retry_hours=48),
                    repo,
                )
            self.assertEqual(fresh_run_sources, [])

            records = json.loads(sources_path.read_text(encoding="utf-8"))
            records[0]["last_checked_at"] = "2026-03-18T00:00:00+00:00"
            sources_path.write_text(json.dumps(records), encoding="utf-8")

            with patch("job_finder.collector.discover_sources", return_value=[]):
                from job_finder.collector import resolve_sources

                retry_sources = resolve_sources(
                    [],
                    DiscoverySettings(max_sources_per_run=10, max_low_value_rechecks_per_run=1, low_value_retry_hours=48),
                    repo,
                )
            self.assertEqual(len(retry_sources), 1)
            self.assertEqual(retry_sources[0].identifier, "noisy")

    def test_source_controls_block_and_prioritize_collection_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = LocalJsonRepository(
                jobs_path=str(Path(temp_dir) / "jobs.json"),
                runs_path=str(Path(temp_dir) / "runs.json"),
                sources_path=str(Path(temp_dir) / "sources.json"),
            )
            normal = make_source(company="normal", source_type="lever", identifier="normal")
            priority = make_source(company="priority", source_type="lever", identifier="priority")
            blocked = make_source(company="blocked", source_type="lever", identifier="blocked")
            for source in (normal, priority, blocked):
                repo.upsert_source(source)
            repo.update_source_control(stable_source_id(priority.source_type, priority.identifier), priority=50)
            repo.update_source_control(stable_source_id(blocked.source_type, blocked.identifier), blocked=True)

            with patch("job_finder.collector.discover_sources", return_value=[]):
                from job_finder.collector import resolve_sources

                sources = resolve_sources(
                    [],
                    DiscoverySettings(max_sources_per_run=10),
                    repo,
                )

            self.assertEqual([source.identifier for source in sources], ["priority", "normal"])


class NormalizeTests(unittest.TestCase):
    def test_classify_us_location(self) -> None:
        self.assertEqual(classify_us_location("Remote - United States", "remote"), "confirmed")
        self.assertEqual(classify_us_location("Remote", "remote"), "assumed")
        self.assertEqual(classify_us_location("Remote - Canada", "remote"), "non_us")


class LambdaHandlerTests(unittest.TestCase):
    def test_health_route_skips_storage_initialization(self) -> None:
        with patch("job_finder.lambda_handlers._repository", side_effect=RuntimeError("should not be called")):
            response = api_handler(
                {
                    "rawPath": "/health",
                    "requestContext": {"http": {"method": "GET"}},
                },
                None,
            )

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(json.loads(response["body"]), {"ok": True})

    def test_unexpected_repository_error_returns_500(self) -> None:
        with patch("job_finder.lambda_handlers._repository", side_effect=RuntimeError("ddb init failed")):
            response = api_handler(
                {
                    "rawPath": "/jobs",
                    "requestContext": {"http": {"method": "GET"}},
                    "queryStringParameters": {},
                },
                None,
            )

        self.assertEqual(response["statusCode"], 500)
        self.assertEqual(json.loads(response["body"]), {"error": "Internal server error"})

    def test_collector_metrics_include_completed_crawl_signal(self) -> None:
        calls: list[dict[str, object]] = []

        class FakeCloudWatch:
            def put_metric_data(self, **kwargs: object) -> None:
                calls.append(kwargs)

        fake_boto3 = types.SimpleNamespace(client=lambda service_name: FakeCloudWatch())
        run = CrawlRunRecord(
            run_id="run-metrics",
            started_at="2026-03-21T00:00:00+00:00",
            finished_at="2026-03-21T00:03:00+00:00",
            jobs_inserted=2,
            errors_count=0,
            run_status="completed",
        )

        with patch.dict(sys.modules, {"boto3": fake_boto3}), patch.dict(
            os.environ,
            {"PROJECT_NAME": "job-finder", "ENVIRONMENT": "dev"},
        ):
            _emit_crawl_metrics(run)

        self.assertEqual(calls[0]["Namespace"], "JobFinder")
        metric_data = calls[0]["MetricData"]
        metric_names = {metric["MetricName"] for metric in metric_data}  # type: ignore[index]
        self.assertIn("CompletedCrawls", metric_names)
        self.assertIn("CrawlDurationSeconds", metric_names)
        self.assertEqual(
            metric_data[0]["Dimensions"],  # type: ignore[index]
            [{"Name": "Project", "Value": "job-finder"}, {"Name": "Environment", "Value": "dev"}],
        )


if __name__ == "__main__":
    unittest.main()
