const state = {
  overview: null,
  trials: [],
  analogs: [],
  sources: [],
  filterTimer: null,
};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function humanize(value) {
  if (!value) return "Unknown";
  return String(value)
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatNumber(value) {
  return new Intl.NumberFormat("en-US").format(Number(value || 0));
}

function formatDate(value) {
  if (!value) return "Not reported";
  const date = new Date(`${String(value).slice(0, 10)}T12:00:00`);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(date);
}

function setSyncState(status, text) {
  const element = $("#syncState");
  element.className = `sync-state ${status}`;
  element.querySelector("span:last-child").textContent = text;
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { Accept: "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Request failed (${response.status})`);
  }
  return response.json();
}

function safeUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value, window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function renderOverview(data) {
  const metrics = data.metrics;
  $("#totalTrials").textContent = formatNumber(metrics.total_trials);
  $("#activeTrials").textContent = formatNumber(metrics.active_trials);
  $("#resultCoverage").textContent = Number(metrics.result_coverage_percent).toFixed(
    Number(metrics.result_coverage_percent) % 1 ? 1 : 0,
  );
  $("#trialsWithResults").textContent = formatNumber(metrics.trials_with_results);
  $("#analogCount").textContent = formatNumber(metrics.analog_relationships);
  $("#unresolvedAnalogs").textContent = formatNumber(metrics.unresolved_analogs);
  $("#sourceDocuments").textContent = formatNumber(metrics.source_documents);
  $("#pendingReviews").textContent = formatNumber(metrics.pending_reviews);

  const statuses = data.status_breakdown || [];
  const maximum = Math.max(...statuses.map((item) => item.count), 1);
  const chart = $("#statusChart");
  chart.innerHTML = statuses.length
    ? statuses
        .slice(0, 7)
        .map(
          (item) => `
            <div class="status-row">
              <span class="status-name" title="${escapeHtml(humanize(item.status))}">${escapeHtml(humanize(item.status))}</span>
              <div class="status-track"><div class="status-fill" style="width:${Math.max(3, (item.count / maximum) * 100)}%"></div></div>
              <span class="status-count">${formatNumber(item.count)}</span>
            </div>`,
        )
        .join("")
    : `<div class="empty-state"><strong>No trial statuses yet</strong><p>Ingest ClinicalTrials.gov records to populate this view.</p></div>`;

  const filter = $("#statusFilter");
  const current = filter.value;
  filter.innerHTML = `<option value="">All statuses</option>${statuses
    .map((item) => `<option value="${escapeHtml(item.status)}">${escapeHtml(humanize(item.status))}</option>`)
    .join("")}`;
  filter.value = current;

  const outcomes = data.outcome_breakdown || [];
  $("#outcomeBreakdown").innerHTML = outcomes.length
    ? outcomes
        .map(
          (item) => `
            <div class="outcome-item ${escapeHtml(item.outcome)}">
              <span><i></i>${escapeHtml(humanize(item.outcome))}</span>
              <strong>${formatNumber(item.count)}</strong>
            </div>`,
        )
        .join("")
    : `<div class="empty-state"><strong>No accepted assessments</strong><p>Registry state remains separate until outcome evidence is reviewed.</p></div>`;
}

function statusPill(value) {
  const className = escapeHtml(String(value || "unknown").toLowerCase());
  return `<span class="pill ${className}">${escapeHtml(humanize(value))}</span>`;
}

function renderTrials(data) {
  state.trials = data.items || [];
  $("#trialResultCount").textContent = `${formatNumber(data.total)} ${data.total === 1 ? "trial" : "trials"}`;
  const rows = $("#trialRows");
  const empty = $("#trialEmpty");
  if (!state.trials.length) {
    rows.innerHTML = "";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  rows.innerHTML = state.trials
    .map((trial) => {
      const sponsor = trial.sponsors?.[0] || "Sponsor not listed";
      const condition = trial.conditions?.[0] || "Condition not listed";
      const phase = trial.phases?.length ? trial.phases.map(humanize).join(" / ") : "N/A";
      const outcome = trial.assessed_outcome
        ? statusPill(trial.assessed_outcome)
        : `<span class="pill">Not assessed</span>`;
      return `
        <tr data-nct-id="${escapeHtml(trial.nct_id)}" tabindex="0" role="button" aria-label="Open ${escapeHtml(trial.nct_id)}">
          <td class="trial-cell">
            <strong title="${escapeHtml(trial.brief_title || "Untitled trial")}">${escapeHtml(trial.brief_title || "Untitled trial")}</strong>
            <div class="trial-meta"><span class="nct">${escapeHtml(trial.nct_id)}</span><span>•</span><span>${escapeHtml(sponsor)}</span><span>•</span><span>${escapeHtml(condition)}</span></div>
          </td>
          <td>${escapeHtml(phase)}</td>
          <td>${statusPill(trial.overall_status)}</td>
          <td>${trial.enrollment_count == null ? "—" : formatNumber(trial.enrollment_count)}</td>
          <td>${outcome}</td>
          <td><span class="open-arrow">›</span></td>
        </tr>`;
    })
    .join("");

  rows.querySelectorAll("tr[data-nct-id]").forEach((row) => {
    row.addEventListener("click", () => openTrial(row.dataset.nctId));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openTrial(row.dataset.nctId);
      }
    });
  });
}

function renderAnalogs(items) {
  const grid = $("#analogGrid");
  if (!items.length) {
    grid.innerHTML = `<div class="panel"><div class="empty-state"><strong>No analog links yet</strong><p>Import a Convoke export to begin comparing development programs.</p></div></div>`;
    return;
  }
  grid.innerHTML = items
    .map((item) => {
      const dimensions = Object.entries(item.dimension_scores || {})
        .slice(0, 4)
        .map(([name, score]) => `<span class="dimension-chip">${escapeHtml(humanize(name))} ${Math.round(Number(score) * 100)}%</span>`)
        .join("");
      const score = item.overall_score == null ? "—" : Math.round(Number(item.overall_score) * 100);
      return `
        <article class="analog-card">
          <div class="analog-card-top">
            <span class="source-tag">${escapeHtml(item.source_system)}</span>
            <span class="analog-score">${score}<small>${score === "—" ? "" : "% match"}</small></span>
          </div>
          <div class="analog-pair">
            <span class="analog-name" title="${escapeHtml(item.anchor_label)}">${escapeHtml(item.anchor_label)}</span>
            <span class="analog-arrow">→</span>
            <span class="analog-name" title="${escapeHtml(item.analog_label)}">${escapeHtml(item.analog_label)}</span>
          </div>
          <p>${escapeHtml(item.rationale || "No rationale supplied by the source.")}</p>
          <div class="dimension-list">${dimensions || `<span class="dimension-chip">No dimension scores</span>`}</div>
          <div style="margin-top:14px"><span class="resolution-tag ${escapeHtml(item.resolution_status)}">${escapeHtml(humanize(item.resolution_status))}</span></div>
        </article>`;
    })
    .join("");
}

function sourceInitials(value) {
  return String(value || "source")
    .split(/[._\-\s]+/)
    .map((part) => part[0])
    .join("")
    .slice(0, 3)
    .toUpperCase();
}

function renderSources(items) {
  const grid = $("#sourceGrid");
  if (!items.length) {
    grid.innerHTML = `<div class="panel"><div class="empty-state"><strong>No source documents yet</strong><p>Ingest a registry or public-web record to populate provenance.</p></div></div>`;
    return;
  }
  grid.innerHTML = items
    .map(
      (item) => `
        <article class="source-card">
          <div class="source-card-top">
            <span class="source-avatar">${escapeHtml(sourceInitials(item.source_system))}</span>
            <span class="source-tag">Connected</span>
          </div>
          <h3 title="${escapeHtml(item.source_system)}">${escapeHtml(humanize(item.source_system))}</h3>
          <p>Last observed ${escapeHtml(formatDate(item.last_observed_at))}</p>
          <div class="source-metrics">
            <span><strong>${formatNumber(item.documents)}</strong> documents</span>
            <span><strong>${formatNumber(item.observations)}</strong> observations</span>
          </div>
        </article>`,
    )
    .join("");
}

function citationLink(url, label) {
  const safe = safeUrl(url);
  if (!safe) return "";
  return `<a href="${escapeHtml(safe)}" target="_blank" rel="noreferrer">${escapeHtml(label)} <span>↗</span></a>`;
}

function emptyEvidence(message) {
  return `<div class="evidence-empty"><strong>No linked records</strong><p>${escapeHtml(message)}</p></div>`;
}

function renderAssessedEvidence(items, kind) {
  if (!items?.length) {
    return emptyEvidence(`No reviewed ${kind} assessments match the current evidence set.`);
  }
  return items
    .map((item) => {
      const categories = (item.causal_categories || [])
        .map((category) => `<span>${escapeHtml(humanize(category))}</span>`)
        .join("");
      return `
        <article class="evidence-card ${escapeHtml(kind)}">
          <div class="evidence-card-top">
            ${statusPill(item.outcome)}
            <span>${item.confidence == null ? "Confidence not scored" : `${Math.round(Number(item.confidence) * 100)}% confidence`}</span>
          </div>
          <h5>${escapeHtml(item.title || "Untitled trial")}</h5>
          <p class="evidence-nct">${escapeHtml(item.nct_id)}</p>
          <p>${escapeHtml(item.rationale || "No assessment rationale recorded.")}</p>
          ${categories ? `<div class="causal-tags">${categories}</div>` : ""}
          <div class="citation-links">
            ${citationLink(item.registry_url, "Registry")}
            ${citationLink(item.publication_url, "Publication")}
          </div>
        </article>`;
    })
    .join("");
}

function renderContextEvidence(items, kind) {
  if (!items?.length) {
    const messages = {
      active: "No currently active analog trials were found in the imported snapshots.",
      completed: "No completed registry trials were found for this disease context.",
      unassessed: "No additional unassessed registry context was found.",
    };
    return emptyEvidence(messages[kind] || "No matching context was found.");
  }
  return items
    .map(
      (item) => `
        <article class="context-card">
          <div class="context-card-top">
            <span class="source-tag">${escapeHtml(item.source || "ClinicalTrials.gov")}</span>
            ${item.registry_status ? statusPill(item.registry_status) : ""}
          </div>
          <h5>${escapeHtml(item.title || "Untitled trial")}</h5>
          <p>${escapeHtml(item.nct_id || "No registry ID")}${item.phase ? ` · ${escapeHtml(humanize(item.phase))}` : ""}</p>
          ${item.drug || item.indication ? `<p>${escapeHtml(item.drug || "")}${item.drug && item.indication ? " · " : ""}${escapeHtml(item.indication || "")}</p>` : ""}
          <div class="citation-links">${citationLink(item.registry_url, "Registry record")}</div>
        </article>`,
    )
    .join("");
}

function renderInactivePrograms(items) {
  if (!items?.length) {
    return emptyEvidence("No inactive, probable-inactive, or discontinued Convoke programs were found in the imported snapshots.");
  }
  return items
    .map((item) => {
      const linked = (item.linked_trials || [])
        .slice(0, 4)
        .map((trial) => citationLink(trial.registry_url, trial.nct_id || "Linked trial"))
        .filter(Boolean)
        .join("");
      return `
        <article class="context-card inactive-program-card">
          <div class="context-card-top">
            <span class="source-tag">Convoke Program Tracker</span>
            <span class="pill terminated">${escapeHtml(humanize(item.status))}</span>
          </div>
          <h5>${escapeHtml(item.drug)} in ${escapeHtml(item.indication)}</h5>
          <p>${escapeHtml(item.stage || "Stage not reported")}${item.organizations?.length ? ` · ${escapeHtml(item.organizations.join(", "))}` : ""}</p>
          ${item.targets?.length ? `<div class="causal-tags">${item.targets.map((target) => `<span>${escapeHtml(target)}</span>`).join("")}</div>` : ""}
          <div class="citation-links">${linked || "<span>No linked NCT records</span>"}</div>
        </article>`;
    })
    .join("");
}

function renderRecommendation(data) {
  const recommendation = data.recommendation;
  const sample = recommendation.sample_size_benchmark;
  const endpoints = recommendation.primary_endpoint_candidates || [];
  const risks = recommendation.risk_flags || [];
  const evidence = data.evidence || {};
  const endpointMarkup = endpoints.length
    ? endpoints
        .map(
          (endpoint) => `
            <li>
              <span>${escapeHtml(endpoint.title)}</span>
              <small>${escapeHtml(endpoint.time_frame || "Time frame not reported")}</small>
              ${citationLink(`https://clinicaltrials.gov/study/${endpoint.source_nct_id}`, endpoint.source_nct_id)}
            </li>`,
        )
        .join("")
    : `<li><span>No recurring primary endpoint found</span><small>Define the endpoint with clinical and statistical review.</small></li>`;
  const riskMarkup = risks.length
    ? risks.map((risk) => `<li><span>!</span><p>${escapeHtml(risk.message)}</p></li>`).join("")
    : `<li class="neutral"><span>i</span><p>No reviewed failure factors were available; risk identification remains incomplete.</p></li>`;

  $("#recommendationOutput").innerHTML = `
    <header class="recommendation-header">
      <div>
        <p class="section-label">Recommended starting point</p>
        <h3>${escapeHtml(recommendation.phase)} design for ${escapeHtml(data.request.drug)}</h3>
        <p>${escapeHtml(data.request.disease)} · generated ${escapeHtml(formatDate(data.generated_at))}</p>
      </div>
      <span class="strength-badge ${escapeHtml(data.evidence_strength)}">${escapeHtml(humanize(data.evidence_strength))} evidence</span>
    </header>
    <div class="design-spec-grid">
      <div><span>Allocation</span><strong>${escapeHtml(recommendation.allocation)}</strong></div>
      <div><span>Model</span><strong>${escapeHtml(recommendation.intervention_model)}</strong></div>
      <div><span>Masking</span><strong>${escapeHtml(recommendation.masking)}</strong></div>
      <div><span>Comparator</span><strong>${escapeHtml(recommendation.comparator)}</strong></div>
      <div><span>Purpose</span><strong>${escapeHtml(recommendation.primary_purpose)}</strong></div>
      <div><span>Route</span><strong>${escapeHtml(recommendation.route)}</strong></div>
    </div>
    <div class="recommendation-midgrid">
      <section class="sample-card">
        <p class="section-label">Enrollment benchmark</p>
        <strong>${sample.median == null ? "Not available" : formatNumber(sample.median)}</strong>
        <span>median participants</span>
        <div>
          <p><span>Lower quartile</span><strong>${sample.lower_quartile == null ? "—" : formatNumber(sample.lower_quartile)}</strong></p>
          <p><span>Upper quartile</span><strong>${sample.upper_quartile == null ? "—" : formatNumber(sample.upper_quartile)}</strong></p>
          <p><span>Trials</span><strong>${formatNumber(sample.trial_count)}</strong></p>
        </div>
        <small>${escapeHtml(sample.caveat)}</small>
      </section>
      <section class="endpoint-card">
        <p class="section-label">Primary endpoint candidates</p>
        <ul>${endpointMarkup}</ul>
      </section>
    </div>
    <div class="recommendation-notes">
      <section>
        <h4>Why this pattern</h4>
        <ul>${(recommendation.rationale || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
      </section>
      <section>
        <h4>Risks to investigate</h4>
        <ul class="risk-list">${riskMarkup}</ul>
      </section>
    </div>
    <section class="evidence-section">
      <div class="evidence-heading"><div><p class="section-label">Cited analog evidence</p><h4>Reviewed outcomes</h4></div><span>Human-reviewed labels only</span></div>
      <div class="evidence-columns">
        <div><h4><i class="success-dot"></i> Successful or partial</h4><div class="evidence-stack">${renderAssessedEvidence(evidence.successful, "success")}</div></div>
        <div><h4><i class="failure-dot"></i> Failed</h4><div class="evidence-stack">${renderAssessedEvidence(evidence.failed, "failure")}</div></div>
      </div>
    </section>
    <section class="evidence-section context-section">
      <div class="evidence-heading"><div><p class="section-label">Landscape context</p><h4>Current and historical programs</h4></div><span>Status is not outcome</span></div>
      <div class="context-group"><h5>Active trials</h5><div class="context-grid">${renderContextEvidence(evidence.active, "active")}</div></div>
      <div class="context-group"><h5>Completed trials · outcome-neutral unless reviewed</h5><div class="context-grid">${renderContextEvidence(evidence.completed, "completed")}</div></div>
      <div class="context-group"><h5>Inactive or discontinued programs</h5><div class="context-grid">${renderInactivePrograms(evidence.inactive_programs)}</div></div>
      <div class="context-group"><h5>Additional unassessed registry context</h5><div class="context-grid">${renderContextEvidence(evidence.unassessed_context, "unassessed")}</div></div>
    </section>
    <div class="limitations"><strong>Use with expert review</strong><ul>${(data.limitations || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>`;
}

async function generateRecommendation(event) {
  event.preventDefault();
  const submit = $("#designSubmit");
  const payload = {
    drug: $("#designDrug").value.trim(),
    disease: $("#designDisease").value.trim(),
    phase: $("#designPhase").value || null,
    target: $("#designTarget").value.trim() || null,
    route: $("#designRoute").value.trim() || null,
  };
  $("#recommendationEmpty").classList.add("hidden");
  $("#recommendationOutput").classList.add("hidden");
  $("#recommendationLoading").classList.remove("hidden");
  submit.disabled = true;
  try {
    const data = await request("/api/recommendations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderRecommendation(data);
    $("#recommendationOutput").classList.remove("hidden");
  } catch (error) {
    $("#recommendationOutput").innerHTML = `<div class="recommendation-error"><strong>Could not build the evidence brief</strong><p>${escapeHtml(error.message)}</p></div>`;
    $("#recommendationOutput").classList.remove("hidden");
  } finally {
    $("#recommendationLoading").classList.add("hidden");
    submit.disabled = false;
  }
}

function detailList(title, items, renderer) {
  if (!items?.length) return "";
  return `
    <section class="detail-section">
      <h3>${escapeHtml(title)}</h3>
      <ul class="detail-list">${items.map(renderer).join("")}</ul>
    </section>`;
}

async function openTrial(nctId) {
  const drawer = $("#trialDrawer");
  const backdrop = $("#drawerBackdrop");
  $("#drawerNct").textContent = nctId;
  $("#drawerContent").innerHTML = `<div class="empty-state"><strong>Loading trial evidence…</strong></div>`;
  backdrop.classList.remove("hidden");
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";

  try {
    const data = await request(`/api/trials/${encodeURIComponent(nctId)}`);
    const trial = data.trial;
    const phase = trial.phases?.length ? trial.phases.map(humanize).join(" / ") : "N/A";
    const tags = [statusPill(trial.overall_status), `<span class="pill">${escapeHtml(phase)}</span>`];
    if (trial.has_results) tags.push(`<span class="pill success">Results posted</span>`);
    const assessment = data.assessment;
    $("#drawerContent").innerHTML = `
      <h2 class="drawer-title">${escapeHtml(trial.brief_title || trial.official_title || "Untitled trial")}</h2>
      <div class="drawer-tags">${tags.join("")}</div>
      <div class="detail-grid">
        <div class="detail-stat"><span>Enrollment</span><strong>${trial.enrollment_count == null ? "Not reported" : formatNumber(trial.enrollment_count)}</strong></div>
        <div class="detail-stat"><span>Started</span><strong>${escapeHtml(formatDate(trial.start_date))}</strong></div>
        <div class="detail-stat"><span>Primary completion</span><strong>${escapeHtml(formatDate(trial.primary_completion_date))}</strong></div>
      </div>
      ${assessment ? `
        <section class="detail-section">
          <h3>Latest outcome assessment</h3>
          <ul class="detail-list"><li><strong>${escapeHtml(humanize(assessment.outcome))} · ${Math.round(Number(assessment.confidence) * 100)}% confidence</strong>${escapeHtml(assessment.rationale || "No rationale recorded.")}<br />Evidence cutoff: ${escapeHtml(formatDate(assessment.evidence_cutoff_date))}</li></ul>
        </section>` : `
        <section class="detail-section">
          <h3>Outcome assessment</h3>
          <ul class="detail-list"><li><strong>Not yet assessed</strong>Registry status is shown above, but no reviewed clinical outcome has been assigned.</li></ul>
        </section>`}
      ${detailList("Sponsors", data.sponsors, (item) => `<li><strong>${escapeHtml(item.name)}</strong>${escapeHtml(humanize(item.role))}</li>`)}
      ${detailList("Interventions", data.interventions, (item) => `<li><strong>${escapeHtml(item.name)}</strong>${escapeHtml(humanize(item.type))}${item.description ? ` · ${escapeHtml(item.description)}` : ""}</li>`)}
      ${detailList("Outcome measures", data.outcomes, (item) => `<li><strong>${escapeHtml(humanize(item.type))}: ${escapeHtml(item.title)}</strong>${escapeHtml(item.time_frame || "Time frame not reported")}</li>`)}
      ${detailList("References", data.references, (item) => `<li><strong>${escapeHtml(item.pmid ? `PMID ${item.pmid}` : item.doi || humanize(item.type))}</strong>${escapeHtml(item.citation || item.url || "Reference metadata unavailable")}</li>`)}
      ${trial.canonical_url ? `<a class="registry-link" href="${escapeHtml(trial.canonical_url)}" target="_blank" rel="noreferrer">Open registry record <span>↗</span></a>` : ""}`;
  } catch (error) {
    $("#drawerContent").innerHTML = `<div class="empty-state"><strong>Could not load trial</strong><p>${escapeHtml(error.message)}</p></div>`;
  }
}

function closeTrial() {
  $("#trialDrawer").classList.remove("open");
  $("#trialDrawer").setAttribute("aria-hidden", "true");
  $("#drawerBackdrop").classList.add("hidden");
  document.body.style.overflow = "";
}

async function loadTrials() {
  const parameters = new URLSearchParams({ limit: "100" });
  const search = $("#trialSearch").value.trim();
  const status = $("#statusFilter").value;
  if (search) parameters.set("search", search);
  if (status) parameters.set("status", status);
  $("#trialRows").innerHTML = `<tr class="loading-row"><td colspan="6">Loading trial records…</td></tr>`;
  $("#trialEmpty").classList.add("hidden");
  renderTrials(await request(`/api/trials?${parameters}`));
}

async function loadDashboard() {
  setSyncState("", "Connecting");
  $("#setupCard").classList.add("hidden");
  try {
    const [overview, trials, analogs, sources] = await Promise.all([
      request("/api/overview"),
      request("/api/trials?limit=100"),
      request("/api/analogs?limit=12"),
      request("/api/sources"),
    ]);
    state.overview = overview;
    state.analogs = analogs;
    state.sources = sources;
    renderOverview(overview);
    renderTrials(trials);
    renderAnalogs(analogs);
    renderSources(sources);
    setSyncState("ready", "Evidence current");
  } catch (error) {
    setSyncState("error", "Database unavailable");
    $("#setupCard").classList.remove("hidden");
    $("#trialRows").innerHTML = `<tr class="loading-row"><td colspan="6">${escapeHtml(error.message)}</td></tr>`;
  }
}

$("#trialSearch").addEventListener("input", () => {
  window.clearTimeout(state.filterTimer);
  state.filterTimer = window.setTimeout(() => loadTrials().catch(console.error), 250);
});
$("#statusFilter").addEventListener("change", () => loadTrials().catch(console.error));
$("#designForm").addEventListener("submit", generateRecommendation);
$("#closeDrawer").addEventListener("click", closeTrial);
$("#drawerBackdrop").addEventListener("click", closeTrial);
$("#retryButton").addEventListener("click", loadDashboard);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeTrial();
});

document.querySelectorAll(".topnav a").forEach((link) => {
  link.addEventListener("click", () => {
    document.querySelectorAll(".topnav a").forEach((item) => item.classList.remove("active"));
    link.classList.add("active");
  });
});

loadDashboard();
