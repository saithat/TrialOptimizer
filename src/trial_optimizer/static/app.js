const state = {
  overview: null,
  trials: [],
  analogs: [],
  sources: [],
  filterTimer: null,
  recommendationJobId: null,
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
  $("#analogCount").textContent = formatNumber(metrics.program_comparisons ?? metrics.analog_relationships);
  $("#savedPrograms").textContent = formatNumber(metrics.convoke_program_snapshots ?? 0);
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
    : `<div class="empty-state"><strong>No trial statuses yet</strong><p>Import ClinicalTrials.gov records to fill this view.</p></div>`;

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
    : `<div class="empty-state"><strong>No reviewed outcomes</strong><p>A registry status is not an outcome until the results are reviewed.</p></div>`;
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
    grid.innerHTML = `<div class="panel"><div class="empty-state"><strong>No comparisons yet</strong><p>Import Program Tracker records containing the same drug in two or more indications.</p></div></div>`;
    return;
  }
  grid.innerHTML = items
    .map((item) => {
      const dimensions = Object.entries(item.dimension_scores || {})
        .slice(0, 4)
        .map(([name, score]) => `<span class="dimension-chip">${escapeHtml(humanize(name))} ${Math.round(Number(score) * 100)}%</span>`)
        .join("");
      const basis = (item.comparison_basis || [])
        .slice(0, 4)
        .map((label) => `<span class="dimension-chip">${escapeHtml(label)}</span>`)
        .join("");
      const score = item.overall_score == null ? "—" : Math.round(Number(item.overall_score) * 100);
      return `
        <article class="analog-card">
          <div class="analog-card-top">
            <span class="source-tag">${escapeHtml(item.source_system)}</span>
            ${score === "—" ? `<span class="analog-score"><small>Saved comparison</small></span>` : `<span class="analog-score">${score}<small>% match</small></span>`}
          </div>
          <div class="analog-pair">
            <span class="analog-name" title="${escapeHtml(item.anchor_label)}">${escapeHtml(item.anchor_label)}</span>
            <span class="analog-arrow">→</span>
            <span class="analog-name" title="${escapeHtml(item.analog_label)}">${escapeHtml(item.analog_label)}</span>
          </div>
          <p>${escapeHtml(item.rationale || "No rationale supplied by the source.")}</p>
          <div class="dimension-list">${basis || dimensions || `<span class="dimension-chip">Comparison details unavailable</span>`}</div>
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
    grid.innerHTML = `<div class="panel"><div class="empty-state"><strong>No source documents yet</strong><p>Import a registry or public web record to add a source.</p></div></div>`;
    return;
  }
  grid.innerHTML = items
    .map(
      (item) => `
        <article class="source-card">
          <div class="source-card-top">
            <span class="source-avatar">${escapeHtml(sourceInitials(item.source_system))}</span>
            <span class="source-tag">Imported</span>
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
    return emptyEvidence(`No reviewed ${kind} assessments match these trials.`);
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
      active: "No active similar trials were found in the imported records.",
      completed: "No completed registry trials were found for this disease.",
      unassessed: "No additional trials without reviewed outcomes were found.",
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
    return emptyEvidence("No inactive, probable-inactive, or discontinued Convoke programs were found in the imported records.");
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

function renderRelatedDiseases(items) {
  if (!items?.length) {
    return emptyEvidence("No other indications connected by the same drug or a shared target were found in the cached Convoke landscape.");
  }
  return items
    .map((item) => {
      const relationship = item.relationship_kind === "same_drug_cross_indication"
        ? "Same drug"
        : "Shared target";
      const bases = (item.relationship_basis || [])
        .map((basis) => `<span>${escapeHtml(basis)}</span>`)
        .join("");
      const programs = (item.programs || [])
        .map((program) => `${program.drug} · ${program.stage} · ${program.status}`)
        .join("; ");
      const trials = (item.trials || [])
        .map(
          (trial) => `
            <li>
              <div>
                <strong>${escapeHtml(trial.title || "Untitled trial")}</strong>
                <span>${escapeHtml(trial.nct_id)}${trial.phase ? ` · ${escapeHtml(trial.phase)}` : ""}</span>
                <small>${escapeHtml(trial.summary || "No compact trial summary is available.")}</small>
              </div>
              ${citationLink(trial.registry_url, "Open registry")}
            </li>`,
        )
        .join("");
      const hiddenCount = Math.max(0, Number(item.trial_count_returned || 0) - (item.trials || []).length);
      return `
        <article class="related-disease-card">
          <div class="context-card-top">
            <span class="source-tag">Convoke Program Tracker</span>
            <span class="relationship-pill">${escapeHtml(relationship)}</span>
          </div>
          <h5>${escapeHtml(item.indication)}</h5>
          <p>${escapeHtml(item.summary || "No linked-trial summary is available.")}</p>
          <div class="causal-tags">${bases}</div>
          <p class="related-programs"><strong>${formatNumber(item.program_count)} program${item.program_count === 1 ? "" : "s"}</strong>${programs ? ` · ${escapeHtml(programs)}` : ""}</p>
          <ul class="related-trial-list">${trials || "<li><span>No linked NCT records were returned.</span></li>"}</ul>
          ${hiddenCount ? `<p class="related-trial-note">${formatNumber(hiddenCount)} additional returned trial${hiddenCount === 1 ? "" : "s"} omitted from this compact view.</p>` : ""}
        </article>`;
    })
    .join("");
}

function renderLLMCitations(citationIds, llm) {
  const index = llm.citation_index || {};
  return (citationIds || [])
    .map((citationId) => {
      const citation = index[citationId];
      if (!citation) return "";
      const label = citation.label || citationId;
      const url = safeUrl(citation.url);
      if (!url) {
        return `<span title="${escapeHtml(citation.source || "Evidence source")}">${escapeHtml(label)}</span>`;
      }
      return `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer" title="${escapeHtml(citation.source || "Evidence source")}">${escapeHtml(label)} <span>↗</span></a>`;
    })
    .filter(Boolean)
    .join("");
}

function renderLLMClaims(items, llm, emptyMessage) {
  if (!items?.length) return `<div class="llm-empty">${escapeHtml(emptyMessage)}</div>`;
  return items
    .map(
      (item) => `
        <article class="llm-claim">
          <span class="inference-tag ${escapeHtml(item.evidence_kind)}">${escapeHtml(humanize(item.evidence_kind))}</span>
          <p>${escapeHtml(item.statement)}</p>
          <div class="llm-citations">${renderLLMCitations(item.citation_ids, llm)}</div>
        </article>`,
    )
    .join("");
}

function renderLLMSynthesis(llm) {
  if (!llm || llm.status === "disabled") {
    return `
      <section class="llm-fallback">
        <span aria-hidden="true">✦</span>
        <div><strong>AI review is off</strong><p>The rules-based comparison and source links are still available.</p></div>
      </section>`;
  }
  if (llm.status === "pending") {
    return `
      <section class="llm-fallback">
        <span aria-hidden="true">✦</span>
        <div><strong>AI summary is still running</strong><p>${escapeHtml(llm.message || "The design and evidence are ready to review now.")}</p></div>
      </section>`;
  }
  if (llm.status !== "enhanced" || !llm.output) {
    return `
      <section class="llm-fallback warning">
        <span aria-hidden="true">!</span>
        <div><strong>AI review is unavailable</strong><p>${escapeHtml(llm.message || "The rules-based comparison is still available.")}</p></div>
      </section>`;
  }

  const output = llm.output;
  const alternatives = (output.alternative_designs || [])
    .map(
      (item) => `
        <article class="alternative-card">
          <h5>${escapeHtml(item.title)}</h5>
          <p><strong>Change</strong>${escapeHtml(item.change)}</p>
          <p><strong>Why</strong>${escapeHtml(item.rationale)}</p>
          <p><strong>Tradeoff</strong>${escapeHtml(item.tradeoff)}</p>
          <div class="llm-citations">${renderLLMCitations(item.citation_ids, llm)}</div>
        </article>`,
    )
    .join("");
  const gaps = (output.evidence_gaps || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");

  return `
    <section class="llm-synthesis">
      <header class="llm-header">
        <div>
          <p class="section-label">AI review</p>
          <h4>Summary and design questions</h4>
        </div>
        <div class="llm-meta">
          <span class="llm-confidence ${escapeHtml(output.confidence)}">${escapeHtml(humanize(output.confidence))} confidence</span>
          <small>${escapeHtml(llm.model)}</small>
        </div>
      </header>
      <p class="llm-summary">${escapeHtml(output.executive_summary)}</p>
      <div class="llm-columns">
        <section>
          <h5>Design review</h5>
          <div class="llm-claim-list">${renderLLMClaims(output.design_assessment, llm, "No additional design claims were supported.")}</div>
        </section>
        <section>
          <h5>How failed trials may apply</h5>
          <div class="llm-claim-list">${renderLLMClaims(output.failure_readthrough, llm, "No reviewed failed trials were available for comparison.")}</div>
        </section>
      </div>
      <div class="alternative-group">
        <h5>Other design options</h5>
        <div class="alternative-grid">${alternatives || `<div class="llm-empty">No other design supported by the sources was proposed.</div>`}</div>
      </div>
      <div class="llm-review-grid">
        <section><h5>Missing information</h5><ul>${gaps || "<li>No additional missing information identified.</li>"}</ul></section>
        <section><h5>Questions for reviewers</h5><ul><li>${(output.expert_review_questions || []).length ? "AI-added questions appear in the main review panel above." : "No additional AI question was supported."}</li></ul></section>
      </div>
      <footer class="llm-footer">
        <span>${llm.included_convoke_context ? "Convoke records used" : "Convoke records not sent to AI"}</span>
        <span>${llm.audit_status === "stored" ? "Review saved" : "Review not saved"}</span>
      </footer>
    </section>`;
}

function normalizedQuestionTokens(value) {
  return new Set(String(value || "").toLowerCase().replace(/[^a-z0-9\s]/g, " ").split(/\s+/).filter((token) => token.length > 3));
}

function questionsOverlap(first, second) {
  const left = normalizedQuestionTokens(first);
  const right = normalizedQuestionTokens(second);
  if (!left.size || !right.size) return false;
  const shared = [...left].filter((token) => right.has(token)).length;
  return shared / Math.min(left.size, right.size) >= 0.55;
}

function appendAIReviewQuestions(llm) {
  const list = $("#recommendationQuestionList");
  const status = $("#aiQuestionStatus");
  if (!list || !status) return;
  if (llm.status !== "enhanced" || !llm.output) {
    status.textContent = "AI did not add questions; the immediate checks remain available.";
    status.classList.add("complete");
    return;
  }
  const initialQuestions = [...list.querySelectorAll("li[data-question]")]
    .map((item) => item.dataset.question || "");
  const additions = (llm.output.expert_review_questions || [])
    .map((item) => typeof item === "string"
      ? { question: item, basis: "Generated from the supplied review context.", basis_kind: "evidence_gap", citation_ids: [] }
      : item)
    .filter((item) => item.question && !initialQuestions.some((question) => questionsOverlap(question, item.question)));

  if (!additions.length) {
    status.textContent = "AI review found no additional non-duplicate questions.";
    status.classList.add("complete");
    return;
  }
  list.insertAdjacentHTML("beforeend", additions.map((item) => `
    <li class="ai-review-question" data-question="${escapeHtml(item.question)}">
      <span>AI</span>
      <div>
        <p>${escapeHtml(item.question)}</p>
        <small>${escapeHtml(item.basis || "No basis supplied.")}</small>
        <div class="question-basis">
          <em>${item.basis_kind === "evidence" ? "Saved evidence" : "Evidence gap"}</em>
          ${renderLLMCitations(item.citation_ids || [], llm)}
        </div>
      </div>
    </li>`).join(""));
  status.textContent = `${additions.length} AI-added question${additions.length === 1 ? "" : "s"} added.`;
  status.classList.add("complete");
}

function renderRecommendation(data) {
  const recommendation = data.recommendation;
  const sample = recommendation.sample_size_benchmark;
  const endpoints = recommendation.primary_endpoint_candidates || [];
  const questions = recommendation.risk_flags || [];
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
  const questionMarkup = questions.length
    ? questions.map((question) => `<li data-question="${escapeHtml(question.message)}"><span>?</span><div><p>${escapeHtml(question.message)}</p><small>Immediate check</small></div></li>`).join("")
    : `<li class="neutral"><span>i</span><p>No review questions were generated. Confirm the evidence and statistical plan before proceeding.</p></li>`;

  $("#recommendationOutput").innerHTML = `
    <header class="recommendation-header">
      <div>
        <p class="section-label">Suggested design</p>
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
        <p class="section-label">Enrollment in similar trials</p>
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
        <p class="section-label">Possible primary endpoints</p>
        <ul>${endpointMarkup}</ul>
      </section>
    </div>
    <div class="recommendation-notes">
      <section>
        <h4>What supports this design</h4>
        <ul>${(recommendation.rationale || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
      </section>
      <section>
        <h4>Questions to review</h4>
        <ul class="risk-list" id="recommendationQuestionList">${questionMarkup}</ul>
        <p class="ai-question-status" id="aiQuestionStatus">${data.llm?.status === "pending" ? "AI is checking for additional evidence-based questions…" : ""}</p>
      </section>
    </div>
    <div id="recommendationAiSlot">${renderLLMSynthesis(data.llm)}</div>
    <section class="evidence-section">
      <div class="evidence-heading"><div><p class="section-label">Related trials</p><h4>Reviewed outcomes</h4></div><span>Human-reviewed labels only</span></div>
      <div class="evidence-columns">
        <div><h4><i class="success-dot"></i> Successful or partial</h4><div class="evidence-stack">${renderAssessedEvidence(evidence.successful, "success")}</div></div>
        <div><h4><i class="failure-dot"></i> Failed</h4><div class="evidence-stack">${renderAssessedEvidence(evidence.failed, "failure")}</div></div>
      </div>
    </section>
    <section class="evidence-section context-section">
      <div class="evidence-heading"><div><p class="section-label">Convoke cross-indication context</p><h4>Related diseases and their linked trials</h4></div><span>Same drug or shared target · not outcome transfer</span></div>
      <div class="related-disease-grid">${renderRelatedDiseases(evidence.related_diseases)}</div>
    </section>
    <section class="evidence-section context-section">
      <div class="evidence-heading"><div><p class="section-label">Other related records</p><h4>Current-indication trial and program context</h4></div><span>Status is not outcome</span></div>
      <div class="context-group"><h5>Active trials</h5><div class="context-grid">${renderContextEvidence(evidence.active, "active")}</div></div>
      <div class="context-group"><h5>Completed trials · outcome-neutral unless reviewed</h5><div class="context-grid">${renderContextEvidence(evidence.completed, "completed")}</div></div>
      <div class="context-group"><h5>Inactive or discontinued programs</h5><div class="context-grid">${renderInactivePrograms(evidence.inactive_programs)}</div></div>
      <div class="context-group"><h5>Additional trials without reviewed outcomes</h5><div class="context-grid">${renderContextEvidence(evidence.unassessed_context, "unassessed")}</div></div>
    </section>
    <div class="limitations"><strong>Limits</strong><ul>${(data.limitations || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>`;
}

async function pollRecommendationAI(jobId) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
    if (state.recommendationJobId !== jobId) return;
    let llm;
    try {
      llm = await request(`/api/recommendations/ai/${encodeURIComponent(jobId)}`);
    } catch (error) {
      const slot = $("#recommendationAiSlot");
      if (slot && state.recommendationJobId === jobId) {
        slot.innerHTML = renderLLMSynthesis({
          status: "fallback",
          message: `The separate AI summary could not be loaded. ${error.message}`,
        });
        appendAIReviewQuestions({status: "fallback"});
      }
      return;
    }
    if (llm.status === "pending") continue;
    const slot = $("#recommendationAiSlot");
    if (slot && state.recommendationJobId === jobId) {
      slot.innerHTML = renderLLMSynthesis(llm);
      appendAIReviewQuestions(llm);
    }
    return;
  }
  const slot = $("#recommendationAiSlot");
  if (slot && state.recommendationJobId === jobId) {
    slot.innerHTML = renderLLMSynthesis({
      status: "fallback",
      message: "The design is ready, but the AI summary is taking longer than expected.",
    });
    appendAIReviewQuestions({status: "fallback"});
  }
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
  state.recommendationJobId = null;
  try {
    const data = await request("/api/recommendations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderRecommendation(data);
    $("#recommendationOutput").classList.remove("hidden");
    if (data.llm?.status === "pending" && data.llm.job_id) {
      state.recommendationJobId = data.llm.job_id;
      pollRecommendationAI(data.llm.job_id).catch(console.error);
    }
  } catch (error) {
    $("#recommendationOutput").innerHTML = `<div class="recommendation-error"><strong>Could not compare trial designs</strong><p>${escapeHtml(error.message)}</p></div>`;
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
  } catch (error) {
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
