const state = {
  apiBaseUrl: "",
  jobs: [],
  filteredJobs: [],
  digest: null,
  sources: [],
};

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
  statusLine: document.querySelector("#statusLine"),
  apiLine: document.querySelector("#apiLine"),
  digestGenerated: document.querySelector("#digestGenerated"),
  digestCompanies: document.querySelector("#digestCompanies"),
  digestReasons: document.querySelector("#digestReasons"),
  sourceQualityList: document.querySelector("#sourceQualityList"),
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

function buildJobsUrl() {
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
  url.searchParams.set("limit", "200");
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
      return right.score - left.score;
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
    const haystack = [job.company, job.title, job.description_text, ...(job.score_reasons || [])].join(" ").toLowerCase();
    return haystack.includes(search);
  });
  state.filteredJobs = sortJobs(filteredJobs);
  renderJobs();
}

async function fetchJobs() {
  const url = buildJobsUrl();
  elements.statusLine.textContent = "Loading jobs...";
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`API ${response.status}`);
  }
  const payload = await response.json();
  state.jobs = payload.items || [];
  updateSummary();
  updateSourceFilter();
  applyClientFilters();
  elements.statusLine.textContent = `Showing ${state.filteredJobs.length} jobs`;
}

function updateJobInState(jobId, changes) {
  let didUpdate = false;
  state.jobs = state.jobs.map((job) => {
    if (job.job_id !== jobId) {
      return job;
    }
    didUpdate = true;
    return { ...job, ...changes };
  });
  if (!didUpdate) {
    return;
  }
  updateSummary();
  applyClientFilters();
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

    item.append(left, score);
    elements.sourceQualityList.append(item);
  });
}

async function fetchInsights() {
  elements.digestGenerated.textContent = "Loading insights...";
  const digestMinScore = Math.max(Number(elements.scoreFilter.value || "0"), 60);
  const digestUrl = new URL("/digest", state.apiBaseUrl);
  digestUrl.searchParams.set("limit", "8");
  digestUrl.searchParams.set("min_score", String(digestMinScore));

  const sourcesUrl = new URL("/sources", state.apiBaseUrl);
  sourcesUrl.searchParams.set("limit", "8");
  sourcesUrl.searchParams.set("statuses", "active,candidate,low_value");

  const [digestResponse, sourcesResponse] = await Promise.all([
    fetch(digestUrl, { cache: "no-store" }),
    fetch(sourcesUrl, { cache: "no-store" }),
  ]);
  if (!digestResponse.ok || !sourcesResponse.ok) {
    throw new Error(`Insights ${digestResponse.status}/${sourcesResponse.status}`);
  }
  state.digest = await digestResponse.json();
  const sourcesPayload = await sourcesResponse.json();
  state.sources = sourcesPayload.items || [];
  renderDigest();
  renderSourceQuality();
}

async function refreshData() {
  await fetchJobs();
  fetchInsights().catch((error) => {
    elements.digestGenerated.textContent = "Unavailable";
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
  const item = result.item || payload;
  updateJobInState(jobId, item);
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
    cardFragment.querySelector(".description").textContent = truncate(job.description_text, 280);

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

    const applyLink = cardFragment.querySelector(".apply-link");
    applyLink.href = job.apply_url;

    cardFragment.querySelector(".save").addEventListener("click", () => updateJob(job.job_id, { status: "saved", notes: notes.value }));
    cardFragment.querySelector(".dismiss").addEventListener("click", () => updateJob(job.job_id, { status: "dismissed", notes: notes.value }));
    cardFragment.querySelector(".applied").addEventListener("click", () => updateJob(job.job_id, { status: "applied", notes: notes.value }));

    fragment.append(cardFragment);
  }
  elements.jobsGrid.append(fragment);
}

async function init() {
  const config = await loadConfig();
  state.apiBaseUrl = config.apiBaseUrl;
  elements.apiLine.textContent = `API: ${state.apiBaseUrl}`;

  [
    elements.searchInput,
    elements.statusFilter,
    elements.sourceFilter,
    elements.scoreFilter,
    elements.sortFilter,
    elements.onlyNewToggle,
  ].forEach((element) => {
    element.addEventListener("input", () => {
      if (element === elements.searchInput || element === elements.sourceFilter || element === elements.sortFilter) {
        applyClientFilters();
        return;
      }
      refreshData().catch((error) => {
        elements.statusLine.textContent = error.message;
      });
    });
    element.addEventListener("change", () => {
      if (element === elements.searchInput || element === elements.sourceFilter || element === elements.sortFilter) {
        applyClientFilters();
      }
    });
  });

  elements.refreshButton.addEventListener("click", () => {
    refreshData().catch((error) => {
      elements.statusLine.textContent = error.message;
    });
  });

  await refreshData();
}

init().catch((error) => {
  elements.statusLine.textContent = error.message;
});
