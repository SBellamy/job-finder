from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from job_finder.models import JobRecord, SourceConfig
from job_finder.normalize import normalize_whitespace


POSITIVE_KEYWORDS = {
    "aws": 20,
    "terraform": 18,
    "ansible": 12,
    "ci/cd": 12,
    "cicd": 12,
    "gitlab": 8,
    "github actions": 8,
    "automation": 4,
    "observability": 10,
    "prometheus": 6,
    "grafana": 6,
    "linux": 6,
    "rpm": 10,
    "packaging": 10,
    "infrastructure as code": 8,
    "platform engineering": 8,
    "site reliability": 8,
    "incident response": 8,
    "release engineering": 8,
    "developer experience": 6,
}

NEGATIVE_KEYWORDS = {
    "kubernetes": -12,
    "k8s": -12,
    "helm": -6,
    "operator": -4,
    "istio": -5,
    "service mesh": -6,
}

TITLE_WEIGHTS = {
    "devops": 18,
    "site reliability": 18,
    "sre": 18,
    "platform engineer": 14,
    "platform engineering": 12,
    "platform": 10,
    "cloud infrastructure": 14,
    "infrastructure": 12,
    "developer experience": 10,
    "platform infrastructure": 14,
    "infrastructure engineer": 12,
    "release": 10,
    "release engineer": 12,
    "build": 8,
    "build engineer": 10,
    "production engineering": 12,
    "automation": 8,
}

OFF_TARGET_TITLE_WEIGHTS = {
    "salesforce": -55,
    "customer success": -50,
    "customer support": -45,
    "customer service": -45,
    "account executive": -45,
    "business partner": -40,
    "business operations": -40,
    "revenue operations": -40,
    "people operations": -35,
    "legal operations": -35,
    "recruit": -40,
    "marketing": -40,
    "copywriter": -40,
    "designer": -35,
    "product manager": -35,
    "product owner": -35,
    "data scientist": -35,
    "machine learning": -35,
    "analytics": -30,
    "analyst": -28,
    "sales": -28,
    "brand": -28,
    "support": -24,
    "technical support": -30,
    "commercial": -18,
    "solutions architect": -30,
    "solutions engineer": -30,
    "data engineer": -45,
    "data science": -45,
    "applied data science": -55,
    "artificial intelligence": -40,
    "sdet": -60,
    "software development engineer in test": -60,
    "quality assurance": -55,
    "test automation": -45,
    "mobile platform": -45,
    "mobile engineer": -40,
    "ios": -30,
    "android": -30,
    "data platform": -35,
    "full stack": -18,
    "fullstack": -18,
    "frontend": -30,
    "front-end": -30,
    "backend": -16,
    "back-end": -16,
    "search engineering": -30,
    "design systems": -20,
    "corporate engineering": -16,
}

GENERIC_ENGINEERING_TITLE_HINTS = {
    "software engineer",
    "software developer",
    "engineer ii",
    "engineer iii",
    "staff engineer",
    "senior engineer",
}

TITLE_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#./-]*")
TITLE_STOPWORDS = {
    "a",
    "an",
    "and",
    "architect",
    "associate",
    "cloud",
    "engineer",
    "engineering",
    "for",
    "ii",
    "iii",
    "iv",
    "jr",
    "lead",
    "manager",
    "of",
    "principal",
    "remote",
    "senior",
    "site",
    "staff",
    "the",
}

POSITIVE_FEEDBACK_MULTIPLIERS = {
    "saved": 1,
    "applied": 2,
}

NEGATIVE_FEEDBACK_MULTIPLIERS = {
    "dismissed": -1,
}

ROLE_PROFILE_KEYWORDS = {
    "qa": {
        "quality assurance",
        "qa automation",
        "test automation",
        "software development engineer in test",
        "sdet",
        "selenium",
        "cypress",
        "playwright",
        "postman",
        "browserstack",
        "sauce labs",
        "cross-browser",
        "cross-platform testing",
        "e2e",
        "end-to-end",
    },
    "mobile": {
        "react native",
        "mobile app",
        "mobile apps",
        "mobile platform",
        "ios",
        "android",
        "app store",
        "google play",
        "mobile distribution",
        "store compliance",
        "certificates",
        "provisioning",
        "macstadium",
        "ota updates",
    },
    "ai_data": {
        "applied data science",
        "machine learning",
        "artificial intelligence",
        "llm",
        "large language model",
        "langchain",
        "rag",
        "vector database",
        "vector databases",
        "pytorch",
        "tensorflow",
        "recommendation engine",
        "predictive model",
    },
}

ROLE_PROFILE_PENALTIES = {
    "qa": (-45, "QA/test-heavy role"),
    "mobile": (-35, "mobile platform role"),
    "ai_data": (-40, "AI/data role"),
}


@dataclass(slots=True)
class FeedbackProfile:
    title_token_weights: dict[str, int] = field(default_factory=dict)
    company_weights: dict[str, int] = field(default_factory=dict)

    def adjustment_for(self, job: JobRecord) -> tuple[int, list[str]]:
        title_tokens = set(_title_tokens(job.title))
        title_adjustment = sum(self.title_token_weights.get(token, 0) for token in title_tokens)
        company_adjustment = self.company_weights.get(normalize_whitespace(job.company).lower(), 0)
        adjustment = max(-24, min(24, title_adjustment + company_adjustment))
        if adjustment >= 6:
            return adjustment, ["similar to saved roles"]
        if adjustment <= -6:
            return adjustment, ["similar to dismissed roles"]
        return adjustment, []

    @property
    def is_empty(self) -> bool:
        return not self.title_token_weights and not self.company_weights


def _match_keywords(text: str, weights: dict[str, int]) -> Counter[str]:
    matches: Counter[str] = Counter()
    for keyword in weights:
        if keyword in text:
            matches[keyword] += 1
    return matches


def _title_tokens(title: str) -> list[str]:
    return [
        token
        for token in TITLE_TOKEN_RE.findall(normalize_whitespace(title).lower())
        if len(token) > 2 and token not in TITLE_STOPWORDS
    ]


def build_feedback_profile(jobs: Iterable[JobRecord]) -> FeedbackProfile:
    title_token_weights: Counter[str] = Counter()
    company_weights: Counter[str] = Counter()
    for job in jobs:
        company_key = normalize_whitespace(job.company).lower()
        if job.status in POSITIVE_FEEDBACK_MULTIPLIERS:
            multiplier = POSITIVE_FEEDBACK_MULTIPLIERS[job.status]
            for token in set(_title_tokens(job.title)):
                title_token_weights[token] += 5 * multiplier
            company_weights[company_key] += 2 * multiplier
        elif job.status in NEGATIVE_FEEDBACK_MULTIPLIERS:
            multiplier = NEGATIVE_FEEDBACK_MULTIPLIERS[job.status]
            for token in set(_title_tokens(job.title)):
                title_token_weights[token] += 6 * multiplier
            company_weights[company_key] += multiplier
    return FeedbackProfile(
        title_token_weights={token: max(-18, min(18, weight)) for token, weight in title_token_weights.items()},
        company_weights={company: max(-4, min(6, weight)) for company, weight in company_weights.items()},
    )


def _score_title_alignment(title_text: str, positive_keyword_matches: Counter[str]) -> tuple[int, list[str], bool]:
    score = 0
    reasons: list[str] = []
    positive_title_matches = _match_keywords(title_text, TITLE_WEIGHTS)
    negative_title_matches = _match_keywords(title_text, OFF_TARGET_TITLE_WEIGHTS)
    has_target_title = bool(positive_title_matches)
    positive_keyword_count = sum(positive_keyword_matches.values())

    for keyword, count in positive_title_matches.items():
        score += TITLE_WEIGHTS[keyword] * count
        if keyword in {"site reliability", "sre", "devops"}:
            reasons.append("platform/SRE title")
        else:
            reasons.append(f"title includes {keyword}")

    for keyword, count in negative_title_matches.items():
        score += OFF_TARGET_TITLE_WEIGHTS[keyword] * count
    if negative_title_matches:
        reasons.append("off-target title family")

    if not has_target_title and any(hint in title_text for hint in GENERIC_ENGINEERING_TITLE_HINTS):
        score -= 18
        reasons.append("generic software title")

    if not has_target_title and positive_keyword_count < 2:
        score -= 20
        reasons.append("weak DevOps/SRE alignment")

    return score, reasons, has_target_title


def _count_keyword_matches(text: str, keywords: set[str]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


def _score_role_profile(text: str, title_text: str, *, has_target_title: bool) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    generic_software_title = any(hint in title_text for hint in GENERIC_ENGINEERING_TITLE_HINTS)

    qa_matches = _count_keyword_matches(text, ROLE_PROFILE_KEYWORDS["qa"])
    mobile_matches = _count_keyword_matches(text, ROLE_PROFILE_KEYWORDS["mobile"])
    ai_data_matches = _count_keyword_matches(text, ROLE_PROFILE_KEYWORDS["ai_data"])

    if qa_matches >= 2:
        penalty, reason = ROLE_PROFILE_PENALTIES["qa"]
        score += penalty
        reasons.append(reason)

    if mobile_matches >= 3 and (generic_software_title or "platform" in title_text):
        penalty, reason = ROLE_PROFILE_PENALTIES["mobile"]
        score += penalty
        reasons.append(reason)

    if ai_data_matches >= 2 and not has_target_title:
        penalty, reason = ROLE_PROFILE_PENALTIES["ai_data"]
        score += penalty
        reasons.append(reason)

    if generic_software_title and not has_target_title:
        if qa_matches >= 1:
            score -= 12
        if mobile_matches >= 2:
            score -= 10
        if ai_data_matches >= 1:
            score -= 10

    return score, reasons


def score_job(job: JobRecord, source: SourceConfig, feedback_profile: FeedbackProfile | None = None) -> tuple[int, list[str], bool]:
    text = normalize_whitespace(" ".join([job.title, job.description_text, job.location_text])).lower()
    title_text = normalize_whitespace(job.title).lower()
    score = 0
    reasons: list[str] = []

    if job.is_remote and job.us_location_confidence == "confirmed":
        score += 25
        reasons.append("remote US confirmed")
    elif job.is_remote and job.is_us_based:
        score += 12
        reasons.append("remote, US not explicit")
    elif not job.is_remote:
        score -= 90
        reasons.append("not remote")
    else:
        score -= 75
        reasons.append("US eligibility unclear")

    positive_matches = _match_keywords(text, POSITIVE_KEYWORDS)
    for keyword, count in positive_matches.items():
        score += POSITIVE_KEYWORDS[keyword] * count
        if keyword == "ci/cd" or keyword == "cicd":
            reasons.append("mentions CI/CD")
        elif keyword in {"rpm", "packaging"}:
            reasons.append("mentions RPM/packaging")
        else:
            reasons.append(f"mentions {keyword}")

    title_score, title_reasons, has_target_title = _score_title_alignment(title_text, positive_matches)
    score += title_score
    reasons.extend(title_reasons)

    role_profile_score, role_profile_reasons = _score_role_profile(text, title_text, has_target_title=has_target_title)
    score += role_profile_score
    reasons.extend(role_profile_reasons)

    negative_matches = _match_keywords(text, NEGATIVE_KEYWORDS)
    for keyword, count in negative_matches.items():
        score += NEGATIVE_KEYWORDS[keyword] * count
    if negative_matches:
        reasons.append("Kubernetes-heavy profile")

    if source.company_size in {"small", "mid"}:
        score += 6
        reasons.append("smaller SaaS target")
    if "saas" in source.company_tags:
        score += 6
        if "smaller SaaS target" not in reasons:
            reasons.append("smaller SaaS target")

    if "contract" in text:
        score -= 15
        reasons.append("contract role")
    if "security clearance" in text:
        score -= 10
        reasons.append("clearance preference mismatch")
    if not has_target_title and any(keyword in text for keyword in {"salesforce", "customer success", "marketing", "copywriter", "people business partner"}):
        score -= 12

    if feedback_profile is not None and not feedback_profile.is_empty:
        feedback_adjustment, feedback_reasons = feedback_profile.adjustment_for(job)
        score += feedback_adjustment
        reasons.extend(feedback_reasons)

    eligible = job.is_remote and job.is_us_based
    unique_reasons: list[str] = []
    for reason in reasons:
        if reason not in unique_reasons:
            unique_reasons.append(reason)
    return score, unique_reasons[:8], eligible
