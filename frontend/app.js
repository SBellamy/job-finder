const state = {
  apiBaseUrl: "",
  jobs: [],
  filteredJobs: [],
  digest: null,
  sources: [],
  freshness: null,
  nextCursor: null,
  jobsRequest: 0,
  insightsRequest: 0,
  visitBaseline: null,
};

const JOB_PAGE_SIZE = 100;
const FILTER_STORAGE_KEY = "job-finder-filters-v1";
const VISIT_STORAGE_KEY = "job-finder-last-visit";
const FILTER_IDS = ["statusFilter", "scoreFilter", "sortFilter", "onlyNewToggle", "confirmedOnlyToggle", "foundFilter", "digestWindow"];
const CAUTION_RE = /not explicit|not remote|unclear|off-target|generic|weak |heavy|mismatch|contract role|similar to dismissed/i;

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
  confirmedOnlyToggle: document.querySelector("#confirmedOnlyToggle"),
  foundFilter: document.querySelector("#foundFilter"),
  digestWindow: document.querySelector("#digestWindow"),
  sourceForm: document.querySelector("#sourceForm"),
  sourceUrl: document.querySelector("#sourceUrl"),
  sourceMessage: document.querySelector("#sourceMessage"),
  refreshButton: document.querySelector("#refreshButton"),
  exportButton: document.querySelector("#exportButton"),
  loadMoreButton: document.querySelector("#loadMoreButton"),
  statusLine: document.querySelector("#statusLine"),
  digestGenerated: document.querySelector("#digestGenerated"),
  digestJobs: document.querySelector("#digestJobs"),
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

function foundAfter() {
  const selected = elements.foundFilter.value;
  if (selected === "last_visit") return state.visitBaseline;
  return selected ? new Date(Date.now() - Number(selected) * 3600000).toISOString() : null;
}

function buildJobsUrl(cursor = null) {
  const url = new URL("/jobs", state.apiBaseUrl);
  for (const [key, value] of Object.entries({
    status: elements.statusFilter.value,
    min_score: elements.scoreFilter.value,
    sort: elements.sortFilter.value,
    source_type: elements.sourceFilter.value,
    search: elements.searchInput.value.trim(),
    found_after: state.queryFoundAfter,
    eligible_only: "true",
    confirmed_us_only: String(elements.confirmedOnlyToggle.checked),
    only_new: String(elements.onlyNewToggle.checked),
    limit: String(JOB_PAGE_SIZE),
    cursor,
  })) {
    if (value) url.searchParams.set(key, value);
  }
  return url;
}

function buildExportUrl() {
  const url = buildJobsUrl();
  url.pathname = "/jobs/export";
  url.searchParams.set("format", "csv");
  url.searchParams.set("limit", "1000");
  return url;
}

function restorePreferences() {
  try {
    const previousVisit = localStorage.getItem(VISIT_STORAGE_KEY);
    state.visitBaseline = Number.isNaN(Date.parse(previousVisit)) ? null : previousVisit;
    const saved = JSON.parse(localStorage.getItem(FILTER_STORAGE_KEY) || "{}");
    for (const id of FILTER_IDS) {
      const element = elements[id];
      if (!Object.hasOwn(saved, id)) continue;
      if (element.type === "checkbox") element.checked = Boolean(saved[id]);
      else element.value = saved[id];
    }
  } catch (_error) { /* Browsing without storage still works. */ }
  const lastVisitOption = elements.foundFilter.querySelector('[value="last_visit"]');
  lastVisitOption.disabled = !state.visitBaseline;
  if (!state.visitBaseline && elements.foundFilter.value === "last_visit") elements.foundFilter.value = "";
}

function savePreferences() {
  try {
    const values = Object.fromEntries(FILTER_IDS.map((id) => [id, elements[id].type === "checkbox" ? elements[id].checked : elements[id].value]));
    localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(values));
  } catch (_error) { /* Preferences are optional. */ }
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
  const sources = ["ashby", "greenhouse", "lever", "smartrecruiters", "workable"];
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
    const numeric = Number(value);
    return numeric > 10000000000 ? numeric : numeric * 1000;
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
  if (selectedSort === "newest_posted") {
    sorted.sort((left, right) => {
      const leftPosted = parseSortDate(left.posted_date) || parseSortDate(left.first_seen_at);
      const rightPosted = parseSortDate(right.posted_date) || parseSortDate(right.first_seen_at);
      return (
        rightPosted - leftPosted ||
        parseSortDate(right.first_seen_at) - parseSortDate(left.first_seen_at) ||
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
    if (elements.statusFilter.value && job.status !== elements.statusFilter.value) return false;
    if (elements.onlyNewToggle.checked && job.status !== "new") return false;
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
  const request = ++state.jobsRequest;
  if (!append) state.queryFoundAfter = foundAfter();
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
  if (request !== state.jobsRequest) return;
  state.jobs = append ? [...new Map([...state.jobs, ...(payload.items || [])].map((job) => [job.job_id, job])).values()] : payload.items || [];
  state.nextCursor = payload.next_cursor || null;
  updateSummary();
  updateSourceFilter();
  applyClientFilters();
  updateLoadMoreButton();
  updateLoadedStatus();
  try { localStorage.setItem(VISIT_STORAGE_KEY, new Date().toISOString()); } catch (_error) { /* Optional. */ }
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
  if (state.digest) {
    state.digest.items = state.digest.items.filter((job) => job.job_id !== jobId || !changes.status || changes.status === "new");
    state.digest.count = state.digest.items.length;
    renderDigest();
  }
  if (didUpdate) {
    updateSummary();
    applyClientFilters();
    updateLoadedStatus();
  }
}

function getDismissReason(job) {
  return job.dismiss_reason || job.metadata?.dismiss_reason || "";
}

function renderDigest() {
  elements.digestJobs.replaceChildren();
  const digest = state.digest;
  if (!digest) {
    elements.digestGenerated.textContent = "Unavailable";
    return;
  }
  elements.digestGenerated.textContent = `${digest.count} picks · last ${digest.hours}h`;
  if (!digest.items?.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No fresh picks in this window. Try 3 or 7 days, or review the full feed below.";
    elements.digestJobs.append(empty);
    return;
  }
  for (const job of digest.items) {
    const row = document.createElement("article");
    row.className = "shortlist-item";
    const link = document.createElement("a");
    link.href = job.apply_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = `${job.company} · ${job.title}`;
    const detail = document.createElement("p");
    detail.className = "muted";
    detail.textContent = `Fit ${job.score} · ${job.location_text || "Location unclear"} · ${formatJobDate(job.posted_date, "Posted")}`;
    const reasons = document.createElement("p");
    reasons.className = "muted";
    reasons.textContent = (job.score_reasons || []).filter((reason) => !CAUTION_RE.test(reason)).slice(0, 3).join(" · ");
    const save = document.createElement("button");
    save.type = "button";
    save.className = "ghost";
    save.textContent = "Save";
    save.addEventListener("click", async () => {
      save.disabled = true;
      try { await updateJob(job.job_id, { status: "saved" }); }
      catch (error) { elements.statusLine.textContent = error.message; save.disabled = false; }
    });
    const cautions = document.createElement("div");
    cautions.className = "cautions";
    for (const reason of (job.score_reasons || []).filter((reason) => CAUTION_RE.test(reason))) cautions.append(makePill(reason));
    row.append(link, detail, reasons, cautions, save);
    elements.digestJobs.append(row);
  }
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
  state.sources.forEach((source) => {
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
  const request = ++state.insightsRequest;
  const digestUrl = new URL("/digest", state.apiBaseUrl);
  digestUrl.searchParams.set("limit", "8");
  digestUrl.searchParams.set("min_score", String(Math.max(Number(elements.scoreFilter.value || "0"), 60)));
  digestUrl.searchParams.set("hours", elements.digestWindow.value);
  digestUrl.searchParams.set("confirmed_us_only", String(elements.confirmedOnlyToggle.checked));
  const sourcesUrl = new URL("/sources", state.apiBaseUrl);
  sourcesUrl.searchParams.set("limit", "50");
  sourcesUrl.searchParams.set("statuses", "manual,active,candidate,low_value,dead");
  const sections = [
    { url: digestUrl, apply: (data) => { state.digest = data; renderDigest(); }, fail: (message) => { state.digest = null; renderDigest(); elements.digestGenerated.textContent = message; } },
    { url: sourcesUrl, apply: (data) => { state.sources = data.items || []; renderSourceQuality(); }, fail: (message) => { elements.sourceQualityList.textContent = message; } },
    { url: new URL("/health/freshness", state.apiBaseUrl), apply: (data) => { state.freshness = data; renderFreshness(); }, fail: (message) => { elements.freshnessStatus.textContent = "Unavailable"; elements.freshnessDetail.textContent = message; } },
  ];
  await Promise.all(sections.map(async (section) => {
    try {
      const response = await fetch(section.url, { cache: "no-store" });
      if (!response.ok) throw new Error(`Unable to load (${response.status})`);
      const data = await response.json();
      if (request === state.insightsRequest) section.apply(data);
    } catch (error) {
      if (request === state.insightsRequest) section.fail(error.message);
    }
  }));
}

async function refreshData() {
  await Promise.all([fetchJobs(), fetchInsights()]);
}

async function followSource(event) {
  event.preventDefault();
  const button = elements.sourceForm.querySelector("button");
  button.disabled = true;
  elements.sourceMessage.textContent = "Following board...";
  try {
    const response = await fetch(new URL("/sources", state.apiBaseUrl), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: elements.sourceUrl.value.trim() }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Unable to follow board (${response.status})`);
    elements.sourceMessage.textContent = "Board followed. Jobs will appear after its next successful collection.";
    elements.sourceUrl.value = "";
    await fetchInsights();
  } catch (error) { elements.sourceMessage.textContent = error.message; }
  finally { button.disabled = false; }
}

async function updateJob(jobId, payload) {
  const url = new URL(`/jobs/${jobId}`, state.apiBaseUrl);
  const response = await fetch(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const message = `Update failed with ${response.status}`;
    elements.statusLine.textContent = message;
    throw new Error(message);
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

function formatJobDate(value, label) {
  const timestamp = parseSortDate(value);
  return timestamp ? `${label} ${new Date(timestamp).toLocaleDateString()}` : `${label} date unknown`;
}

function reviewJob(jobId, payload) {
  return updateJob(jobId, payload).catch((error) => { elements.statusLine.textContent = error.message; });
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
    cardFragment.querySelector(".score-pill").title = "Fit score, not a percentage";
    const summaryDescription = job.description_excerpt || job.description_text || "";
    const description = cardFragment.querySelector(".description");
    description.textContent = truncate(summaryDescription, 280);

    const meta = cardFragment.querySelector(".meta");
    [
      job.location_text || "Unknown location",
      job.remote_type,
      formatJobDate(job.posted_date, "Posted"),
      formatJobDate(job.first_seen_at, "Found"),
      job.status,
      job.source_type,
    ].forEach((text) => meta.append(makePill(text)));

    const reasons = cardFragment.querySelector(".reasons");
    (job.score_reasons || []).filter((reason) => !CAUTION_RE.test(reason)).slice(0, 5).forEach((reason) => reasons.append(makePill(reason)));
    const cautions = cardFragment.querySelector(".cautions");
    const concerns = (job.score_reasons || []).filter((reason) => CAUTION_RE.test(reason));
    if (job.us_location_confidence !== "confirmed" && !concerns.some((reason) => /US/i.test(reason))) concerns.unshift("US eligibility needs confirmation");
    if (job.last_seen_at && Date.now() - parseSortDate(job.last_seen_at) > 7 * 86400000) concerns.unshift("Not seen in a crawl for over 7 days; check availability");
    for (const concern of concerns) cautions.append(makePill(concern));
    cautions.hidden = !concerns.length;

    const notes = cardFragment.querySelector(".notes");
    notes.value = job.notes || "";
    notes.addEventListener("change", () => reviewJob(job.job_id, { notes: notes.value }));

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

    cardFragment.querySelector(".save").addEventListener("click", () => reviewJob(job.job_id, { status: "saved", notes: notes.value }));
    cardFragment.querySelector(".dismiss").addEventListener("click", () =>
      reviewJob(job.job_id, { status: "dismissed", notes: notes.value, dismiss_reason: dismissReason.value })
    );
    cardFragment.querySelector(".applied").addEventListener("click", () => reviewJob(job.job_id, { status: "applied", notes: notes.value }));

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
        description.textContent = detail.description_text || summaryDescription;
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
  state.apiBaseUrl = config.apiBaseUrl || window.location.origin;
  if (config.demo) {
    const banner = document.createElement("p");
    banner.className = "demo-banner";
    banner.textContent = "Demo mode · Illustrative jobs and companies. Changes are temporary; application links are examples.";
    document.querySelector(".shell").prepend(banner);
  }
  restorePreferences();
  updateSourceFilter();
  let filterTimer;
  const handleFilterChange = (event) => {
    if (event.target === elements.onlyNewToggle && elements.onlyNewToggle.checked) elements.statusFilter.value = "new";
    if (elements.statusFilter.value && elements.statusFilter.value !== "new") elements.onlyNewToggle.checked = false;
    savePreferences();
    // Invalidate in-flight responses immediately, including during debounce.
    state.jobsRequest += 1;
    state.nextCursor = null;
    updateLoadMoreButton();
    clearTimeout(filterTimer);
    filterTimer = setTimeout(() => refreshData().catch((error) => {
      elements.statusLine.textContent = error.message;
    }), 250);
  };
  for (const element of [elements.searchInput, elements.sourceFilter, ...FILTER_IDS.filter((id) => id !== "digestWindow").map((id) => elements[id])]) {
    const eventName = element === elements.searchInput || element === elements.scoreFilter ? "input" : "change";
    element.addEventListener(eventName, handleFilterChange);
  }
  elements.digestWindow.addEventListener("change", () => { savePreferences(); fetchInsights(); });
  elements.sourceForm.addEventListener("submit", followSource);

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
