const state = {
  apiBaseUrl: "",
  jobs: [],
  filteredJobs: [],
  digest: null,
  sources: [],
  freshness: null,
  nextCursor: null,
};

const JOB_PAGE_SIZE = 1000;

const elements = {
  eligibleCount: document.querySelector("#eligibleCount"),
  newCount: document.querySelector("#newCount"),
  savedCount: document.querySelector("#savedCount"),
  searchInput: document.querySelector("#searchInput"),
  statusFilter: document.querySelector("#statusFilter"),
  sourceFilter: document.querySelector("#sourceFilter"),
  scoreFilter: document.querySelector("#scoreFilter"),
  sortFilter: document.querySelector("#sortFilter"),
  onlyNewToggle: document.querySelector("#onlyNewToggle"),
  refreshButton: document.querySelector("#refreshButton"),
  exportButton: document.querySelector("#exportButton"),
  loadMoreButton: document.querySelector("#loadMoreButton"),
  statusLine: document.querySelector("#statusLine"),
  apiLine: document.querySelector("#apiLine"),
  digestGenerated: document.querySelector("#digestGenerated"),
  digestCompanies: document.querySelector("#digestCompanies"),
  digestReasons: document.querySelector("#digestReasons"),
  sourceQualityList: document.querySelector("#sourceQualityList"),
  freshnessStatus: document.querySelector("#freshnessStatus"),
  freshnessLastRun: document.querySelector("#freshnessLastRun"),
  freshnessInserted: document.querySelector("#freshnessInserted"),
  freshnessUpdated: document.querySelector("#freshnessUpdated"),
  freshnessDetail: document.querySelector("#freshnessDetail"),
  jobsGrid: document.querySelector("#jobsGrid"),
  template: document.querySelector("#jobCardTemplate"),
};

async function loadConfig() {
  try {
    const response = await fetch("./config.json", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`config ${response.status}`);
    }
    return await response.json();
  } catch (_error) {
    return { apiBaseUrl: "http://localhost:3000" };
  }
}

function buildJobsUrl(cursor = null) {
  const url = new URL("/jobs", state.apiBaseUrl);
  const status = elements.statusFilter.value.trim();
  const minScore = elements.scoreFilter.value.trim();
  const sort = elements.sortFilter.value.trim();
  if (status) {
    url.searchParams.set("status", status);
  }
  if (minScore) {
    url.searchParams.set("min_score", minScore);
  }
  if (sort) {
    url.searchParams.set("sort", sort);
  }
  url.searchParams.set("eligible_only", "true");
  url.searchParams.set("only_new", elements.onlyNewToggle.checked ? "true" : "false");
  url.searchParams.set("limit", String(JOB_PAGE_SIZE));
  if (cursor) {
    url.searchParams.set("cursor", cursor);
  }
  return url;
}

function buildExportUrl() {
  const url = new URL("/jobs/export", state.apiBaseUrl);
  const status = elements.statusFilter.value.trim();
  const minScore = elements.scoreFilter.value.trim();
  const sort = elements.sortFilter.value.trim();
  if (status) {
    url.searchParams.set("status", status);
  }
  if (minScore) {
    url.searchParams.set("min_score", minScore);
  }
  if (sort) {
    url.searchParams.set("sort", sort);
  }
  url.searchParams.set("eligible_only", "true");
  url.searchParams.set("only_new", elements.onlyNewToggle.checked ? "true" : "false");
  url.searchParams.set("format", "csv");
  url.searchParams.set("limit", "1000");
  return url;
}

function updateSummary() {
  const eligible = state.jobs.filter((job) => job.is_eligible).length;
  const saved = state.jobs.filter((job) => job.status === "saved").length;
  const newCount = state.jobs.filter((job) => job.status === "new").length;
  elements.eligibleCount.textContent = String(eligible);
  elements.savedCount.textContent = String(saved);
  elements.newCount.textContent = String(newCount);
}

function updateSourceFilter() {
  const sources = [...new Set(state.jobs.map((job) => job.source_type))].sort();
  const currentValue = elements.sourceFilter.value;
  elements.sourceFilter.innerHTML = '<option value="">All</option>';
  for (const source of sources) {
    const option = document.createElement("option");
    option.value = source;
    option.textContent = source;
    elements.sourceFilter.append(option);
  }
  elements.sourceFilter.value = sources.includes(currentValue) ? currentValue : "";
}

function parseSortDate(value) {
  if (!value) {
    return 0;
  }
  if (/^\d+$/.test(String(value))) {
    return Number(value);
  }
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}

function sortJobs(jobs) {
  const selectedSort = elements.sortFilter.value;
  const sorted = [...jobs];
  if (selectedSort === "newest") {
    sorted.sort((left, right) => {
      const newestDelta = parseSortDate(right.first_seen_at) - parseSortDate(left.first_seen_at);
      if (newestDelta !== 0) {
        return newestDelta;
      }
      return (
        parseSortDate(right.last_seen_at) - parseSortDate(left.last_seen_at) ||
        parseSortDate(right.posted_date) - parseSortDate(left.posted_date) ||
        right.score - left.score
      );
    });
    return sorted;
  }
  sorted.sort((left, right) => {
    const rankedDelta =
      Number(right.is_eligible) - Number(left.is_eligible) ||
      right.score - left.score ||
      parseSortDate(right.first_seen_at) - parseSortDate(left.first_seen_at);
    if (rankedDelta !== 0) {
      return rankedDelta;
    }
    return parseSortDate(right.posted_date) - parseSortDate(left.posted_date);
  });
  return sorted;
}

function applyClientFilters() {
  const search = elements.searchInput.value.trim().toLowerCase();
  const source = elements.sourceFilter.value.trim();

  const filteredJobs = state.jobs.filter((job) => {
    if (source && job.source_type !== source) {
      return false;
    }
    if (!search) {
      return true;
    }
    const haystack = [
      job.company,
      job.title,
      job.description_text,
      job.description_excerpt,
      ...(job.score_reasons || []),
    ].join(" ").toLowerCase();
    return haystack.includes(search);
  });
  state.filteredJobs = sortJobs(filteredJobs);
  renderJobs();
}

function updateLoadMoreButton() {
  elements.loadMoreButton.hidden = !state.nextCursor;
  elements.loadMoreButton.disabled = false;
  elements.loadMoreButton.textContent = "Load more jobs";
}

function updateLoadedStatus() {
  const suffix = state.nextCursor ? " - more available" : "";
  elements.statusLine.textContent = `Showing ${state.filteredJobs.length} loaded jobs${suffix}`;
}

async function fetchJobs({ append = false } = {}) {
  const url = buildJobsUrl(append ? state.nextCursor : null);
  elements.statusLine.textContent = append ? "Loading more jobs..." : "Loading jobs...";
  if (append) {
    elements.loadMoreButton.disabled = true;
    elements.loadMoreButton.textContent = "Loading...";
  }
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`API ${response.status}`);
  }
  const payload = await response.json();
  state.jobs = append ? [...state.jobs, ...(payload.items || [])] : payload.items || [];
  state.nextCursor = payload.next_cursor || null;
  updateSummary();
  updateSourceFilter();
  applyClientFilters();
  updateLoadMoreButton();
  updateLoadedStatus();
}

function updateJobInState(jobId, changes) {
  let didUpdate = false;
  state.jobs = state.jobs.map((job) => {
    if (job.job_id !== jobId) {
      return job;
    }
    didUpdate = true;
    const metadata = { ...(job.metadata || {}), ...(changes.metadata || {}) };
    if (Object.hasOwn(changes, "dismiss_reason")) {
      if (changes.dismiss_reason) {
        metadata.dismiss_reason = changes.dismiss_reason;
      } else {
        delete metadata.dismiss_reason;
      }
    }
    const dismissReason = Object.hasOwn(changes, "dismiss_reason") ? changes.dismiss_reason : metadata.dismiss_reason || "";
    return { ...job, ...changes, metadata, dismiss_reason: dismissReason };
  });
  if (!didUpdate) {
    return;
  }
  updateSummary();
  applyClientFilters();
}

function getDismissReason(job) {
  return job.dismiss_reason || job.metadata?.dismiss_reason || "";
}

function renderDigest() {
  const digest = state.digest;
  elements.digestCompanies.innerHTML = "";
  elements.digestReasons.innerHTML = "";
  if (!digest) {
    elements.digestGenerated.textContent = "Unavailable";
    return;
  }
  elements.digestGenerated.textContent = `${digest.count} top matches`;
  (digest.summary?.top_companies || []).slice(0, 6).forEach(([company, count]) => {
    elements.digestCompanies.append(makePill(`${company} (${count})`));
  });
  (digest.summary?.top_reasons || []).slice(0, 6).forEach(([reason, count]) => {
    elements.digestReasons.append(makePill(`${reason} (${count})`));
  });
}

function renderSourceQuality() {
  elements.sourceQualityList.innerHTML = "";
  if (!state.sources.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No source quality data yet.";
    elements.sourceQualityList.append(empty);
    return;
  }
  state.sources.slice(0, 6).forEach((source) => {
    const metadata = source.metadata || {};
    const item = document.createElement("div");
    item.className = "source-item";

    const left = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = `${source.company} (${source.source_type})`;
    const detail = document.createElement("p");
    detail.textContent = `${source.status} • ${source.high_score_jobs_total || 0} strong matches • ${source.eligible_jobs_total || 0} eligible`;
    left.append(title, detail);

    const score = document.createElement("div");
    score.className = "source-score";
    score.textContent = source.quality_score;

    const controls = document.createElement("div");
    controls.className = "source-controls";

    const blockButton = document.createElement("button");
    blockButton.className = "ghost";
    blockButton.type = "button";
    blockButton.textContent = metadata.blocked ? "Unblock" : "Block";
    blockButton.addEventListener("click", () => {
      updateSource(source.source_id, { blocked: !metadata.blocked }).catch((error) => {
        elements.statusLine.textContent = error.message;
      });
    });

    const priorityButton = document.createElement("button");
    priorityButton.className = "ghost";
    priorityButton.type = "button";
    priorityButton.textContent = Number(metadata.priority || 0) > 0 ? "Normal" : "Boost";
    priorityButton.addEventListener("click", () => {
      updateSource(source.source_id, { priority: Number(metadata.priority || 0) > 0 ? 0 : 50 }).catch((error) => {
        elements.statusLine.textContent = error.message;
      });
    });

    controls.append(blockButton, priorityButton);
    item.append(left, score, controls);
    elements.sourceQualityList.append(item);
  });
}

function formatRelativeAge(seconds) {
  if (seconds === null || seconds === undefined) {
    return "unknown";
  }
  if (seconds < 90) {
    return "just now";
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) {
    return `${minutes}m ago`;
  }
  const hours = Math.round(minutes / 60);
  if (hours < 48) {
    return `${hours}h ago`;
  }
  return `${Math.round(hours / 24)}d ago`;
}

function renderFreshness() {
  const freshness = state.freshness;
  elements.freshnessStatus.classList.toggle("status-bad", !freshness?.ok);
  if (!freshness?.latest_run) {
    elements.freshnessStatus.textContent = "Unavailable";
    elements.freshnessLastRun.textContent = "No completed crawl";
    elements.freshnessInserted.textContent = "0";
    elements.freshnessUpdated.textContent = "0";
    elements.freshnessDetail.textContent = freshness?.error || "No crawl status available.";
    return;
  }

  const latestRun = freshness.latest_run;
  const lastRunDate = new Date(latestRun.finished_at || latestRun.started_at);
  elements.freshnessStatus.textContent = freshness.ok ? "Fresh" : "Stale";
  elements.freshnessLastRun.textContent = formatRelativeAge(freshness.age_seconds);
  elements.freshnessInserted.textContent = String(latestRun.jobs_inserted || 0);
  elements.freshnessUpdated.textContent = String(latestRun.jobs_updated || 0);
  elements.freshnessDetail.textContent = `${latestRun.run_status || "completed"} • ${latestRun.jobs_found || 0} found • ${latestRun.pages_checked || 0} sources • ${lastRunDate.toLocaleString()}`;
}

async function fetchInsights() {
  elements.digestGenerated.textContent = "Loading insights...";
  elements.freshnessStatus.textContent = "Checking...";
  const digestMinScore = Math.max(Number(elements.scoreFilter.value || "0"), 60);
  const digestUrl = new URL("/digest", state.apiBaseUrl);
  digestUrl.searchParams.set("limit", "8");
  digestUrl.searchParams.set("min_score", String(digestMinScore));

  const sourcesUrl = new URL("/sources", state.apiBaseUrl);
  sourcesUrl.searchParams.set("limit", "8");
  sourcesUrl.searchParams.set("statuses", "active,candidate,low_value");

  const freshnessUrl = new URL("/health/freshness", state.apiBaseUrl);
  freshnessUrl.searchParams.set("max_age_hours", "24");

  const [digestResponse, sourcesResponse, freshnessResponse] = await Promise.all([
    fetch(digestUrl, { cache: "no-store" }),
    fetch(sourcesUrl, { cache: "no-store" }),
    fetch(freshnessUrl, { cache: "no-store" }),
  ]);
  if (!digestResponse.ok || !sourcesResponse.ok || !freshnessResponse.ok) {
    throw new Error(`Insights ${digestResponse.status}/${sourcesResponse.status}/${freshnessResponse.status}`);
  }
  state.digest = await digestResponse.json();
  const sourcesPayload = await sourcesResponse.json();
  state.freshness = await freshnessResponse.json();
  state.sources = sourcesPayload.items || [];
  renderDigest();
  renderSourceQuality();
  renderFreshness();
}

async function refreshData() {
  await fetchJobs();
  fetchInsights().catch((error) => {
    elements.digestGenerated.textContent = "Unavailable";
    elements.freshnessStatus.textContent = "Unavailable";
    elements.freshnessStatus.classList.add("status-bad");
    elements.freshnessDetail.textContent = error.message;
    elements.sourceQualityList.innerHTML = "";
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = error.message;
    elements.sourceQualityList.append(empty);
  });
}

async function updateJob(jobId, payload) {
  const url = new URL(`/jobs/${jobId}`, state.apiBaseUrl);
  const response = await fetch(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Update failed with ${response.status}`);
  }
  const result = await response.json();
  const item = { ...(result.item || payload) };
  if (Object.hasOwn(payload, "dismiss_reason")) {
    item.dismiss_reason = payload.dismiss_reason;
  }
  updateJobInState(jobId, item);
}

async function updateSource(sourceId, payload) {
  const url = new URL(`/sources/${encodeURIComponent(sourceId)}`, state.apiBaseUrl);
  const response = await fetch(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Source update failed with ${response.status}`);
  }
  const result = await response.json();
  const item = result.item || {};
  state.sources = state.sources.map((source) => (source.source_id === sourceId ? { ...source, ...item } : source));
  renderSourceQuality();
}

async function exportJobs() {
  elements.exportButton.disabled = true;
  elements.exportButton.textContent = "Exporting...";
  try {
    const response = await fetch(buildExportUrl(), { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Export failed with ${response.status}`);
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const timestamp = new Date().toISOString().replace(/[:.]/g, "").slice(0, 15);
    link.href = objectUrl;
    link.download = `job-finder-${timestamp}.csv`;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
  } finally {
    elements.exportButton.disabled = false;
    elements.exportButton.textContent = "Export CSV";
  }
}

async function loadJobDetail(jobId) {
  const existing = state.jobs.find((job) => job.job_id === jobId);
  if (existing?.description_text) {
    return existing;
  }
  const url = new URL(`/jobs/${jobId}`, state.apiBaseUrl);
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Detail failed with ${response.status}`);
  }
  const payload = await response.json();
  const item = payload.item || {};
  state.jobs = state.jobs.map((job) => (job.job_id === jobId ? { ...job, ...item } : job));
  state.filteredJobs = state.filteredJobs.map((job) => (job.job_id === jobId ? { ...job, ...item } : job));
  return state.jobs.find((job) => job.job_id === jobId) || item;
}

function truncate(text, length = 260) {
  const normalized = (text || "").trim();
  if (normalized.length <= length) {
    return normalized;
  }
  return `${normalized.slice(0, length - 1)}…`;
}

function makePill(text) {
  const pill = document.createElement("span");
  pill.textContent = text;
  return pill;
}

function renderJobs() {
  elements.jobsGrid.innerHTML = "";
  if (!state.filteredJobs.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No jobs match the current filters.";
    elements.jobsGrid.append(empty);
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const job of state.filteredJobs) {
    const cardFragment = elements.template.content.cloneNode(true);
    const article = cardFragment.querySelector(".job-card");
    article.dataset.status = job.status;
    cardFragment.querySelector(".company").textContent = job.company;
    cardFragment.querySelector("h2").textContent = job.title;
    cardFragment.querySelector(".score-pill").textContent = job.score;
    const summaryDescription = job.description_excerpt || job.description_text || "";
    const description = cardFragment.querySelector(".description");
    description.textContent = truncate(summaryDescription, 280);

    const meta = cardFragment.querySelector(".meta");
    [
      job.location_text || "Unknown location",
      job.remote_type,
      job.posted_date || "posted date unavailable",
      job.status,
      job.source_type,
    ].forEach((text) => meta.append(makePill(text)));

    const reasons = cardFragment.querySelector(".reasons");
    (job.score_reasons || []).slice(0, 5).forEach((reason) => reasons.append(makePill(reason)));

    const notes = cardFragment.querySelector(".notes");
    notes.value = job.notes || "";
    notes.addEventListener("change", () => updateJob(job.job_id, { notes: notes.value }));

    const dismissReason = cardFragment.querySelector(".dismiss-reason");
    dismissReason.value = getDismissReason(job);
    dismissReason.addEventListener("change", () => {
      if (job.status === "dismissed") {
        updateJob(job.job_id, { dismiss_reason: dismissReason.value }).catch((error) => {
          elements.statusLine.textContent = error.message;
        });
      }
    });

    const applyLink = cardFragment.querySelector(".apply-link");
    applyLink.href = job.apply_url;

    cardFragment.querySelector(".save").addEventListener("click", () => updateJob(job.job_id, { status: "saved", notes: notes.value }));
    cardFragment.querySelector(".dismiss").addEventListener("click", () =>
      updateJob(job.job_id, { status: "dismissed", notes: notes.value, dismiss_reason: dismissReason.value })
    );
    cardFragment.querySelector(".applied").addEventListener("click", () => updateJob(job.job_id, { status: "applied", notes: notes.value }));

    const detailsButton = cardFragment.querySelector(".details");
    detailsButton.addEventListener("click", async () => {
      if (article.dataset.expanded === "true") {
        article.dataset.expanded = "false";
        description.textContent = truncate(summaryDescription, 280);
        detailsButton.textContent = "Details";
        return;
      }
      detailsButton.disabled = true;
      detailsButton.textContent = "Loading...";
      try {
        const detail = await loadJobDetail(job.job_id);
        article.dataset.expanded = "true";
        description.textContent = truncate(detail.description_text || summaryDescription, 1600);
        detailsButton.textContent = "Hide details";
      } catch (error) {
        detailsButton.textContent = "Details unavailable";
        elements.statusLine.textContent = error.message;
      } finally {
        detailsButton.disabled = false;
      }
    });

    fragment.append(cardFragment);
  }
  elements.jobsGrid.append(fragment);
}

async function init() {
  const config = await loadConfig();
  state.apiBaseUrl = config.apiBaseUrl;
  elements.apiLine.textContent = `API: ${state.apiBaseUrl}`;

  const handleFilterChange = (element) => {
    if (element === elements.searchInput || element === elements.sourceFilter) {
      applyClientFilters();
      updateLoadedStatus();
      return;
    }
    refreshData().catch((error) => {
      elements.statusLine.textContent = error.message;
    });
  };

  [
    elements.searchInput,
    elements.statusFilter,
    elements.sourceFilter,
    elements.scoreFilter,
    elements.sortFilter,
    elements.onlyNewToggle,
  ].forEach((element) => {
    const eventName = element === elements.searchInput || element === elements.scoreFilter ? "input" : "change";
    element.addEventListener(eventName, () => {
      handleFilterChange(element);
    });
  });

  elements.refreshButton.addEventListener("click", () => {
    refreshData().catch((error) => {
      elements.statusLine.textContent = error.message;
    });
  });

  elements.exportButton.addEventListener("click", () => {
    exportJobs().catch((error) => {
      elements.statusLine.textContent = error.message;
      elements.exportButton.disabled = false;
      elements.exportButton.textContent = "Export CSV";
    });
  });

  elements.loadMoreButton.addEventListener("click", () => {
    fetchJobs({ append: true }).catch((error) => {
      elements.statusLine.textContent = error.message;
      updateLoadMoreButton();
    });
  });

  await refreshData();
}

init().catch((error) => {
  elements.statusLine.textContent = error.message;
});
