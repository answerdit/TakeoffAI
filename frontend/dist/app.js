const API_BASE = 'http://localhost:8000';  // empty = relative URLs; nginx proxies /api/ → backend:8000

/* ── Utilities ──────────────────────────────────────────────────────── */

function fmt$(n) {
  if (n == null || isNaN(n)) return '—';
  return '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function setLoading(btn, loading, defaultLabel) {
  btn.disabled = loading;
  btn.innerHTML = loading
    ? `<span class="spinner"></span> Working…`
    : defaultLabel;
}

function showError(el, msg) {
  el.textContent = msg;
  el.style.display = 'block';
}

function clearError(el) {
  el.style.display = 'none';
  el.textContent = '';
}

function toast(msg) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.innerHTML = `<span style="color:var(--green)">✓</span> ${msg}`;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2800);
}

/* ── API key ───────────────────────────────────────────────────────── */

const hdrApiKey = document.getElementById('hdr-api-key');
hdrApiKey.value = sessionStorage.getItem('takeoffai_key') || '';
hdrApiKey.addEventListener('input', () =>
  sessionStorage.setItem('takeoffai_key', hdrApiKey.value)
);

function getHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-API-Key': hdrApiKey.value,
  };
}

/* ── Tab switching ─────────────────────────────────────────────────── */

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
  });
});

/* ── Sliders ───────────────────────────────────────────────────────── */

const overheadSlider = document.getElementById('est-overhead');
const marginSlider   = document.getElementById('est-margin');
const overheadVal    = document.getElementById('overhead-val');
const marginVal      = document.getElementById('margin-val');

overheadSlider.addEventListener('input', () => overheadVal.textContent = overheadSlider.value + '%');
marginSlider.addEventListener('input',   () => marginVal.textContent   = marginSlider.value   + '%');

/* ── TAB 1: Pre-Bid Estimate ───────────────────────────────────────── */

let lastEstimate = null;

const estBtn     = document.getElementById('est-btn');
const estExport  = document.getElementById('est-export');
const estError   = document.getElementById('est-error');
const estResults = document.getElementById('est-results');

estBtn.addEventListener('click', async () => {
  const description  = document.getElementById('est-desc').value.trim();
  const zip_code     = document.getElementById('est-zip').value.trim();
  const trade_type   = document.getElementById('est-trade').value;
  const overhead_pct = parseFloat(overheadSlider.value);
  const margin_pct   = parseFloat(marginSlider.value);

  if (!hdrApiKey.value) { showError(estError, 'Enter your API key in the header.'); return; }
  if (!description) { showError(estError, 'Project description is required.'); return; }
  if (!zip_code)     { showError(estError, 'Zip code is required.'); return; }

  clearError(estError);
  estResults.style.display = 'none';
  estExport.style.display  = 'none';
  setLoading(estBtn, true, 'Calculate Estimate');

  try {
    const res = await fetch(`${API_BASE}/api/estimate`, {
      method: 'POST',
      headers: getHeaders(),
      body: JSON.stringify({ description, zip_code, trade_type, overhead_pct, margin_pct }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    const data = await res.json();
    lastEstimate = data;
    renderEstimate(data);
    // Auto-fill bid strategy textarea
    document.getElementById('bid-est-json').value = JSON.stringify(data, null, 2);

  } catch (e) {
    showError(estError, 'Error: ' + e.message);
  } finally {
    setLoading(estBtn, false, 'Calculate Estimate');
  }
});

function renderEstimate(data) {
  // Project label
  document.getElementById('est-project-label').textContent =
    data.project_summary || 'Estimate';

  // Confidence badge
  const conf = (data.confidence || 'medium').toLowerCase();
  const badge = document.getElementById('est-confidence');
  badge.textContent = conf.charAt(0).toUpperCase() + conf.slice(1) + ' Confidence';
  badge.className = 'confidence-badge confidence-' + conf;

  // Line items
  const tbody = document.getElementById('est-tbody');
  tbody.innerHTML = '';
  (data.line_items || []).forEach(item => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="desc">${esc(item.description)}</td>
      <td class="right">${Number(item.quantity).toLocaleString()}</td>
      <td>${esc(item.unit || '')}</td>
      <td class="right">${fmt$(item.unit_material_cost)}</td>
      <td class="right">${fmt$(item.unit_labor_cost)}</td>
      <td class="right" style="color:var(--text)">${fmt$(item.subtotal)}</td>
    `;
    tbody.appendChild(tr);
  });

  // Totals
  const totalsEl = document.getElementById('est-totals');
  const rangeRow = (data.estimate_low != null && data.estimate_high != null
                && !isNaN(data.estimate_low) && !isNaN(data.estimate_high))
    ? `<div class="total-row range">
         <span>Est. Range</span>
         <span>${fmt$(data.estimate_low)} \u2013 ${fmt$(data.estimate_high)}</span>
       </div>`
    : '';

  totalsEl.innerHTML = `
    <div class="total-row subtotal">
      <span>Subtotal</span>
      <span>${fmt$(data.subtotal)}</span>
    </div>
    <div class="total-row">
      <span>Overhead (${data.overhead_pct ?? ''}%)</span>
      <span>${fmt$(data.overhead_amount)}</span>
    </div>
    <div class="total-row">
      <span>Margin (${data.margin_pct ?? ''}%)</span>
      <span>${fmt$(data.margin_amount)}</span>
    </div>
    <div class="total-row grand">
      <span>TOTAL BID</span>
      <span>${fmt$(data.total_bid)}</span>
    </div>
    ${rangeRow}
  `;

  // Notes
  if (data.notes) {
    const notesEl = document.getElementById('est-notes');
    notesEl.textContent = data.notes;
    notesEl.style.display = 'block';
  }

  estResults.style.display = 'block';
  estExport.style.display  = 'inline-flex';
}

/* ── CSV Export ────────────────────────────────────────────────────── */

estExport.addEventListener('click', () => {
  if (!lastEstimate) return;
  const rows = [
    ['Description', 'Qty', 'Unit', 'Mat $/unit', 'Labor $/unit', 'Total Mat', 'Total Labor', 'Subtotal'],
    ...(lastEstimate.line_items || []).map(i => [
      i.description, i.quantity, i.unit,
      i.unit_material_cost, i.unit_labor_cost,
      i.total_material, i.total_labor, i.subtotal
    ]),
    [],
    ['', '', '', '', '', '', 'Subtotal',  lastEstimate.subtotal],
    ['', '', '', '', '', '', `Overhead (${lastEstimate.overhead_pct}%)`, lastEstimate.overhead_amount],
    ['', '', '', '', '', '', `Margin (${lastEstimate.margin_pct}%)`,     lastEstimate.margin_amount],
    ['', '', '', '', '', '', 'TOTAL BID', lastEstimate.total_bid],
  ];
  const csv = rows.map(r => r.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
  download('takeoffai_estimate.csv', 'text/csv', csv);
  toast('CSV exported');
});

function download(filename, type, content) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([content], { type }));
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ── TAB 2: Bid Strategy ───────────────────────────────────────────── */

const bidBtn     = document.getElementById('bid-btn');
const bidError   = document.getElementById('bid-error');
const bidResults = document.getElementById('bid-results');

bidBtn.addEventListener('click', async () => {
  const rfp_text    = document.getElementById('bid-rfp').value.trim();
  const project_type = document.getElementById('bid-type').value;
  const competRaw   = document.getElementById('bid-competitors').value.trim();
  const estJson     = document.getElementById('bid-est-json').value.trim();

  if (!hdrApiKey.value) { showError(bidError, 'Enter your API key in the header.'); return; }
  if (!rfp_text) { showError(bidError, 'RFP / project details are required.'); return; }
  if (!estJson)  { showError(bidError, 'Paste an estimate JSON or run a Pre-Bid Estimate first.'); return; }

  let estimate;
  try { estimate = JSON.parse(estJson); }
  catch { showError(bidError, 'Estimate JSON is not valid. Check the format.'); return; }

  const known_competitors = competRaw
    ? competRaw.split(',').map(s => s.trim()).filter(Boolean)
    : null;

  clearError(bidError);
  bidResults.style.display = 'none';
  setLoading(bidBtn, true, 'Analyze &amp; Build Strategy');

  try {
    const res = await fetch(`${API_BASE}/api/bid/strategy`, {
      method: 'POST',
      headers: getHeaders(),
      body: JSON.stringify({ estimate, rfp_text, project_type, known_competitors }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    const data = await res.json();
    renderStrategy(data);

  } catch (e) {
    showError(bidError, 'Error: ' + e.message);
  } finally {
    setLoading(bidBtn, false, 'Analyze &amp; Build Strategy');
  }
});

function renderStrategy(data) {
  const rec = (data.recommended_scenario || '').toLowerCase();
  const scenarios = data.bid_scenarios || [];

  const grid = document.getElementById('bid-scenarios');
  grid.innerHTML = '';

  scenarios.forEach(s => {
    const name     = (s.scenario || s.name || '').toLowerCase();
    const isRec    = name === rec || s.recommended === true;
    const winPct   = parseFloat(s.win_probability ?? s.win_pct ?? 0);
    const markup   = s.markup_pct != null ? s.markup_pct : s.markup;
    const price    = s.bid_amount ?? s.total_bid ?? s.price;
    const label    = s.scenario || s.name || name;

    const card = document.createElement('div');
    card.className = 'scenario-card' + (isRec ? ' recommended' : '');
    card.innerHTML = `
      <div class="scenario-name">
        <span class="scenario-label">${esc(label)}</span>
        ${isRec ? '<span class="rec-pill">Recommended</span>' : ''}
      </div>
      <div class="scenario-price">${fmt$(price)}</div>
      <div class="scenario-meta">
        <div class="meta-row">
          <span>Win probability</span>
          <span>${winPct}%</span>
        </div>
        <div class="win-bar-wrap">
          <div class="win-bar" style="width:${Math.min(winPct,100)}%"></div>
        </div>
        ${markup != null ? `
        <div class="meta-row" style="margin-top:4px">
          <span>Markup</span>
          <span>${markup}%</span>
        </div>` : ''}
        ${s.notes ? `<div style="font-size:0.75rem;color:var(--text-muted);margin-top:6px">${esc(s.notes)}</div>` : ''}
      </div>
    `;
    grid.appendChild(card);
  });

  // Proposal narrative
  const narrative = data.proposal_narrative || data.proposal || '';
  document.getElementById('bid-narrative').value = narrative;

  // Strategy notes
  const notes = data.strategy_notes || data.notes || '';
  if (notes) {
    const notesEl = document.getElementById('bid-notes');
    notesEl.textContent = notes;
    notesEl.style.display = 'block';
  }

  bidResults.style.display = 'block';
}

/* ── Copy narrative ────────────────────────────────────────────────── */

document.getElementById('bid-copy').addEventListener('click', () => {
  const ta = document.getElementById('bid-narrative');
  if (!ta.value) return;
  navigator.clipboard.writeText(ta.value).then(() => toast('Proposal copied to clipboard'));
});

/* ── TAB 4: Tournament ─────────────────────────────────────────── */

const trnOverheadSlider = document.getElementById('trn-overhead');
const trnMarginSlider   = document.getElementById('trn-margin');
const trnOverheadVal    = document.getElementById('trn-overhead-val');
const trnMarginVal      = document.getElementById('trn-margin-val');

trnOverheadSlider.addEventListener('input', () => trnOverheadVal.textContent = trnOverheadSlider.value);
trnMarginSlider.addEventListener('input',   () => trnMarginVal.textContent   = trnMarginSlider.value);

const trnBtn     = document.getElementById('trn-btn');
const trnError   = document.getElementById('trn-error');
const trnResults = document.getElementById('trn-results');

trnBtn.addEventListener('click', async () => {
  const description  = document.getElementById('trn-desc').value.trim();
  const zip_code     = document.getElementById('trn-zip').value.trim();
  const trade_type   = document.getElementById('trn-trade').value;
  const client_id    = document.getElementById('trn-client').value.trim() || 'default';
  const overhead_pct = parseFloat(trnOverheadSlider.value);
  const margin_pct   = parseFloat(trnMarginSlider.value);
  const n_agents     = parseInt(document.getElementById('trn-agents').value, 10);
  const n_samples    = parseInt(document.getElementById('trn-samples').value, 10);

  if (!hdrApiKey.value) { showError(trnError, 'Enter your API key in the header.'); return; }
  if (!description)     { showError(trnError, 'Project description is required.'); return; }
  if (!zip_code)        { showError(trnError, 'Zip code is required.'); return; }
  if (isNaN(n_agents)  || n_agents  < 1 || n_agents  > 5) { showError(trnError, 'Agents must be between 1 and 5.'); return; }
  if (isNaN(n_samples) || n_samples < 1 || n_samples > 5) { showError(trnError, 'Samples must be between 1 and 5.'); return; }

  clearError(trnError);
  trnResults.style.display = 'none';
  setLoading(trnBtn, true, 'Run Tournament');

  const trnController = new AbortController();
  const trnTimeout = setTimeout(() => trnController.abort(), 120_000);
  try {
    const res = await fetch(`${API_BASE}/api/tournament/run`, {
      method: 'POST',
      headers: getHeaders(),
      body: JSON.stringify({ description, zip_code, trade_type, client_id,
                             overhead_pct, margin_pct, n_agents, n_samples }),
      signal: trnController.signal,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    const data = await res.json();
    renderTournament(data);

  } catch (e) {
    if (e.name === 'AbortError') {
      showError(trnError, 'Tournament timed out after 2 minutes. The server may be overloaded.');
    } else {
      showError(trnError, 'Error: ' + e.message);
    }
  } finally {
    clearTimeout(trnTimeout);
    setLoading(trnBtn, false, 'Run Tournament');
  }
});

function renderTournament(data) {
  const entries = data.consensus_entries || [];

  // Band
  const bids = entries
    .map(e => e.total_bid)
    .filter(b => b != null && !isNaN(b));

  const bandEl    = document.getElementById('trn-band');
  const bandValEl = document.getElementById('trn-band-value');

  if (bids.length >= 2) {
    const bandLow  = Math.min(...bids);
    const bandHigh = Math.max(...bids);
    bandValEl.textContent = bandLow === bandHigh
      ? `${fmt$(bandLow)} (all agents agree)`
      : `${fmt$(bandLow)} \u2013 ${fmt$(bandHigh)}`;
    bandEl.style.display = 'flex';
  } else {
    bandEl.style.display = 'none';
  }

  // Title
  document.getElementById('trn-title').textContent =
    `Tournament #${data.tournament_id} Results`;

  // Rerank badge — visible when the backend actually sorted by accuracy
  const rerankBadge = document.getElementById('trn-rerank-badge');
  if (rerankBadge) {
    rerankBadge.style.display = data.rerank_active ? 'inline-block' : 'none';
  }

  // Agent cards
  const lowestBid = bids.length ? Math.min(...bids) : null;
  const grid = document.getElementById('trn-grid');
  grid.innerHTML = '';

  if (entries.length === 0) {
    grid.innerHTML = '<div style="color:var(--text-muted);font-size:0.85rem;padding:8px 0">No consensus entries returned by the tournament.</div>';
  }

  // If ANY entry carries accuracy data (or a flag), the annotation layer is
  // live for this client — render meta lines for every card so agents without
  // data read as "no history" rather than silently collapsing.
  const hasAnyAccuracyData = entries.some(e =>
    e.avg_deviation_pct != null
    || (e.closed_job_count || 0) > 0
    || e.is_accuracy_flagged === true
  );

  entries.forEach(e => {
    const isLowest = lowestBid != null && e.total_bid === lowestBid;
    const conf     = (e.confidence || 'medium').toLowerCase();
    const flagged  = e.is_accuracy_flagged === true;

    const card = document.createElement('div');
    card.className = 'agent-card'
      + (isLowest ? ' lowest-bid' : '')
      + (flagged  ? ' flagged'    : '');

    const metaHtml = hasAnyAccuracyData ? renderAccuracyMeta(e) : '';

    card.innerHTML = `
      <div class="agent-name">${esc(e.agent_name || '\u2014')}${isLowest ? ' \u2605' : ''}</div>
      <div class="agent-bid">${fmt$(e.total_bid)}</div>
      <span class="confidence-badge confidence-${esc(conf)}">
        ${esc(conf.charAt(0).toUpperCase() + conf.slice(1))}
      </span>
      ${metaHtml}
    `;
    grid.appendChild(card);
  });

  trnResults.style.display = 'block';
}

/* ── Accuracy meta line (Phase 1 annotations) ──────────────────────── */

function renderAccuracyMeta(entry) {
  const dev    = entry.avg_deviation_pct;
  const jobs   = entry.closed_job_count || 0;
  const flagged = entry.is_accuracy_flagged === true;

  const parts = [];

  if (dev != null) {
    parts.push(`<span>\u00b1${Number(dev).toFixed(1)}%</span>`);
  } else {
    parts.push('<span>no history</span>');
  }

  if (jobs > 0) {
    parts.push('<span class="sep">\u2022</span>');
    parts.push(`<span>${jobs} job${jobs === 1 ? '' : 's'}</span>`);
  }

  if (flagged) {
    parts.push('<span class="sep">\u2022</span>');
    parts.push('<span class="flag-pill" title="This agent has been flagged for historical inaccuracy">Flagged</span>');
  }

  return `<div class="accuracy-meta">${parts.join('')}</div>`;
}

/* ── HTML escape ───────────────────────────────────────────────────── */

function esc(str) {
  return String(str ?? '')
    .replace(/&/g,  '&amp;')
    .replace(/</g,  '&lt;')
    .replace(/>/g,  '&gt;')
    .replace(/"/g,  '&quot;');
}

/* ── TAB 3: Import Bid History ─────────────────────────────────────── */

let parsedRecords = [];
const history = JSON.parse(localStorage.getItem('uploadHistory') || '[]');

function renderHistory() {
  const el = document.getElementById('history-list');
  if (!history.length) { el.textContent = 'No imports yet.'; return; }
  el.innerHTML = history.slice(-5).reverse().map(h =>
    `<div style="padding:.4rem 0;border-bottom:1px solid var(--border)">
      <span style="color:var(--amber)">${esc(h.client_id)}</span> —
      ${Number(h.count)} bids imported on ${esc(h.date)}
    </div>`
  ).join('');
}
renderHistory();

// Template download
document.getElementById('dl-template').addEventListener('click', async (e) => {
  e.preventDefault();
  const res = await fetch(`${API_BASE}/api/upload/template/csv`, {
    headers: { 'X-API-Key': hdrApiKey.value },
  });
  if (!res.ok) { alert('Template not available yet.'); return; }
  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'takeoffai_bid_template.csv';
  a.click();
});

// Drop zone
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('up-file');

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropZone.style.borderColor = 'var(--border-focus)';
});
dropZone.addEventListener('dragleave', () => {
  dropZone.style.borderColor = 'var(--border-solid)';
});
dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.style.borderColor = 'var(--border-solid)';
  if (e.dataTransfer.files[0]) {
    fileInput.files = e.dataTransfer.files;
    document.getElementById('up-filename').textContent = '📄 ' + e.dataTransfer.files[0].name;
  }
});
fileInput.addEventListener('change', () => {
  if (fileInput.files[0])
    document.getElementById('up-filename').textContent = '📄 ' + fileInput.files[0].name;
});

// Upload & Parse
document.getElementById('btn-upload').addEventListener('click', async () => {
  const btn = document.getElementById('btn-upload');
  const errEl = document.getElementById('up-error');
  clearError(errEl);
  document.getElementById('up-results').style.display = 'none';

  if (!fileInput.files[0]) { showError(errEl, 'Please select a file first.'); return; }

  const clientId = document.getElementById('up-client-id').value.trim() || 'default';
  const fd = new FormData();
  fd.append('file', fileInput.files[0]);
  fd.append('client_id', clientId);

  btn.textContent = 'Parsing…'; btn.disabled = true;
  try {
    const fileName = fileInput.files[0]?.name?.toLowerCase() || '';
    const uploadPath = fileName.endsWith('.xlsx') || fileName.endsWith('.xls')
      ? `${API_BASE}/api/upload/bids/excel`
      : `${API_BASE}/api/upload/bids/csv`;
    const res = await fetch(uploadPath, { method: 'POST', headers: { 'X-API-Key': hdrApiKey.value }, body: fd });
    const data = await res.json();
    if (!res.ok) { showError(errEl, data.detail || 'Upload failed.'); return; }

    parsedRecords = data.records || [];
    renderUploadResults(data, clientId);
  } catch (e) {
    showError(errEl, 'Error connecting to server. Make sure TakeoffAI is running.');
  } finally {
    btn.textContent = 'Upload & Parse Bids'; btn.disabled = false;
  }
});

function renderUploadResults(data, clientId) {
  const resEl = document.getElementById('up-results');
  const tbody = document.getElementById('up-tbody');
  const summary = document.getElementById('up-summary');
  const warns = document.getElementById('up-warnings');

  const won = (data.records || []).filter(r => r.won === true).length;
  const lost = (data.records || []).filter(r => r.won === false).length;
  const unknown = (data.records || []).filter(r => r.won === null).length;
  const winRate = won + lost > 0 ? Math.round(won / (won + lost) * 100) : null;

  summary.innerHTML = `
    <strong style="color:var(--amber)">${Number(data.records_parsed)} bids parsed</strong> from ${esc(fileInput.files[0]?.name || 'upload')}<br>
    Won: <span style="color:var(--green)">${Number(won)}</span> &nbsp;|&nbsp;
    Lost: <span style="color:#f87171">${Number(lost)}</span> &nbsp;|&nbsp;
    Unknown: ${Number(unknown)}
    ${winRate !== null ? `&nbsp;|&nbsp; Win rate: <strong>${Number(winRate)}%</strong>` : ''}
    &nbsp;|&nbsp; Client: <span style="color:var(--amber)">${esc(clientId)}</span>
  `;

  tbody.innerHTML = (data.records || []).map(r => `
    <tr>
      <td>${esc(r.project_name || r.description?.slice(0,40) || '—')}</td>
      <td>${esc(r.zip_code || r.location || '—')}</td>
      <td style="color:var(--amber)">$${(r.submitted_amount||0).toLocaleString()}</td>
      <td style="color:${r.won===true?'var(--green)':r.won===false?'#f87171':'var(--text-muted)'}">
        ${r.won===true?'✓ Won':r.won===false?'✗ Lost':'?'}
      </td>
      <td>${r.winning_amount ? '$'+r.winning_amount.toLocaleString() : '—'}</td>
      <td>${esc(r.bid_date || '—')}</td>
    </tr>`).join('');

  if (data.warnings?.length) {
    warns.style.display = 'block';
    warns.innerHTML = '⚠ ' + data.warnings.map(w => esc(w)).join('<br>⚠ ');
  } else {
    warns.style.display = 'none';
  }

  resEl.style.display = 'block';
  toast('Parsed ' + data.records_parsed + ' bids — review and click Import to save');
}

// Import to profile
document.getElementById('btn-import').addEventListener('click', async () => {
  const btn = document.getElementById('btn-import');
  const clientId = document.getElementById('up-client-id').value.trim() || 'default';
  btn.textContent = 'Importing…'; btn.disabled = true;
  try {
    const res = await fetch(`${API_BASE}/api/upload/import`, {
      method: 'POST',
      headers: getHeaders(),
      body: JSON.stringify({ client_id: clientId, records: parsedRecords })
    });
    const data = await res.json();
    if (!res.ok) { alert('Import failed: ' + (data.detail || 'unknown error')); return; }
    history.push({ client_id: clientId, count: parsedRecords.length, date: new Date().toLocaleDateString() });
    localStorage.setItem('uploadHistory', JSON.stringify(history));
    renderHistory();
    document.getElementById('up-results').style.display = 'none';
    toast(parsedRecords.length + ' bids imported to profile "' + clientId + '"');
    parsedRecords = [];
  } catch(e) {
    alert('Connection error — make sure TakeoffAI is running.');
  } finally {
    btn.textContent = '✓ Import to Client Profile'; btn.disabled = false;
  }
});

// Manual single bid
document.getElementById('btn-manual').addEventListener('click', async () => {
  const clientId = document.getElementById('up-client-id').value.trim() || 'default';
  const name = document.getElementById('m-name').value.trim();
  const zip  = document.getElementById('m-zip').value.trim();
  const bid  = parseFloat(document.getElementById('m-bid').value);
  const wonVal = document.getElementById('m-won').value;

  if (!name || !zip || !bid) { alert('Project name, zip code, and bid amount are required.'); return; }

  const record = {
    project_name: name,
    zip_code: zip,
    submitted_amount: bid,
    won: wonVal === 'true' ? true : wonVal === 'false' ? false : null,
    winning_amount: parseFloat(document.getElementById('m-winning').value) || null,
    actual_cost:    parseFloat(document.getElementById('m-actual').value)  || null,
    notes: document.getElementById('m-notes').value.trim() || null,
  };

  const res = await fetch(`${API_BASE}/api/upload/import`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ client_id: clientId, records: [record] })
  });
  const data = await res.json();
  if (!res.ok) { alert('Failed: ' + (data.detail || 'unknown')); return; }
  history.push({ client_id: clientId, count: 1, date: new Date().toLocaleDateString() });
  localStorage.setItem('uploadHistory', JSON.stringify(history));
  renderHistory();
  toast('Bid added to profile "' + clientId + '"');
  ['m-name','m-zip','m-bid','m-winning','m-actual','m-notes'].forEach(id => document.getElementById(id).value = '');
  document.getElementById('m-won').value = '';
});

// ── Blueprint PDF Preprocessor ─────────────────────────────────────────────
(function () {
const dropZone   = document.getElementById('pdf-drop-zone');
const fileInput  = document.getElementById('pdf-file-input');
const filename   = document.getElementById('pdf-filename');
const btn        = document.getElementById('btn-preprocess-pdf');
const errBox     = document.getElementById('pdf-error');
const notice     = document.getElementById('pdf-draft-notice');
const descField  = document.getElementById('est-desc');
let   selectedFile = null;

function showError(msg) {
  errBox.textContent = msg;
  errBox.style.display = 'block';
}
function clearError() {
  errBox.style.display  = 'none';
  errBox.textContent    = '';
}

function setFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    showError('Only PDF files are accepted.');
    return;
  }
  if (file.size > 32 * 1024 * 1024) {
    showError('File too large. Maximum size is 32MB.');
    return;
  }
  clearError();
  selectedFile         = file;
  filename.textContent = file.name;
  btn.style.display    = 'inline-block';
  notice.style.display = 'none';
  descField.value      = '';
}

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropZone.style.borderColor = 'var(--border-focus)';
});
dropZone.addEventListener('dragleave', () => {
  dropZone.style.borderColor = 'var(--border-solid)';
});
dropZone.addEventListener('mouseenter', () => {
  dropZone.style.borderColor = 'var(--border-focus)';
});
dropZone.addEventListener('mouseleave', () => {
  dropZone.style.borderColor = 'var(--border-solid)';
});
dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.style.borderColor = 'var(--border-solid)';
  setFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => setFile(fileInput.files[0]));

btn.addEventListener('click', async () => {
  if (!selectedFile) return;
  btn.disabled    = true;
  btn.innerHTML = '<span class="spinner"></span> Processing…';
  clearError();
  notice.style.display = 'none';

  const zipCode   = document.getElementById('est-zip').value.trim();
  const tradeType = document.getElementById('est-trade').value;
  const jobSlug   = sessionStorage.getItem('activeJobSlug') || '';

  const form = new FormData();
  form.append('pdf',        selectedFile);
  form.append('zip_code',   zipCode);
  form.append('trade_type', tradeType);
  if (jobSlug) form.append('job_slug', jobSlug);

  const apiKey = document.getElementById('hdr-api-key')?.value?.trim() || '';

  try {
    const resp = await fetch(`${API_BASE}/api/estimate/preprocess-pdf`, {
      method:  'POST',
      headers: apiKey ? { 'X-API-Key': apiKey } : {},
      body:    form,
    });
    const data = await resp.json();
    if (!resp.ok) {
      showError(data.detail || `Error ${resp.status}`);
    } else {
      descField.value      = data.draft || '';
      notice.style.display = data.draft ? 'block' : 'none';
      if (!data.draft) showError('No content extracted — try a different PDF.');
    }
  } catch (err) {
    showError('Request failed. Check your connection and try again.');
  } finally {
    btn.disabled    = false;
    btn.textContent = 'Preprocess PDF';
  }
});
})();

/* ── Voice-to-text (Web Speech API) ───────────────────────────────── */

(function initVoiceInput() {
  const SpeechRec  = window.SpeechRecognition || window.webkitSpeechRecognition;
  const micBtn     = document.getElementById('mic-btn');
  const ta         = document.getElementById('est-desc');
  const micError   = document.getElementById('mic-error');
  const micUndo    = document.getElementById('mic-undo');
  const micUndoBtn = document.getElementById('mic-undo-btn');

  // Hide gracefully on unsupported browsers (Firefox, older Safari)
  if (!SpeechRec) {
    if (micBtn) micBtn.style.display = 'none';
    return;
  }

  const recognition = new SpeechRec();
  recognition.continuous     = true;
  recognition.interimResults = true;
  recognition.lang           = 'en-US';

  let _snapshotBefore  = '';  // textarea value before recording started
  let _sessionFinals   = '';  // accumulated final transcript within this session
  let _lastInserted   = '';  // only the segment dictation appended
  let _active         = false;
  let _undoTimer      = null;

  function setMicState(state) {
    micBtn.classList.remove('recording', 'processing');
    if (state === 'recording')  micBtn.classList.add('recording');
    if (state === 'processing') micBtn.classList.add('processing');
    const labels = {
      recording:  'Click to stop recording',
      processing: 'Processing…',
      idle:       'Click to start recording',
    };
    micBtn.setAttribute('aria-label',  labels[state] || labels.idle);
    micBtn.setAttribute('aria-pressed', state === 'recording' ? 'true' : 'false');
  }

  function setMicError(msg) {
    micError.textContent    = msg;
    micError.style.display  = msg ? 'block' : 'none';
  }

  function showUndo() {
    clearTimeout(_undoTimer);
    micUndo.style.display = 'block';
    _undoTimer = setTimeout(() => { micUndo.style.display = 'none'; }, 10000);
  }

  // ── Toggle on/off ───────────────────────────────────────────────
  micBtn.addEventListener('click', () => {
    if (_active) {
      _active = false;
      recognition.stop();
      setMicState('processing');
    } else {
      _active         = true;
      _snapshotBefore = ta.value;
      _sessionFinals  = '';
      setMicError('');
      setMicState('recording');
      try { recognition.start(); } catch (_) { /* already running */ }
    }
  });

  // ── Results: interim preview + final append ─────────────────────
  // In continuous mode e.results accumulates the whole session.
  // Split finals from the current interim chunk manually.
  recognition.addEventListener('result', (e) => {
    let finals = '';
    let interim = '';
    for (let i = 0; i < e.results.length; i++) {
      if (e.results[i].isFinal) {
        finals += e.results[i][0].transcript;
      } else {
        interim += e.results[i][0].transcript;
      }
    }
    _sessionFinals = finals;
    const sep     = _snapshotBefore && !_snapshotBefore.endsWith(' ') ? ' ' : '';
    const committed = sep + _sessionFinals.trim();

    if (interim) {
      // Live preview: show committed finals + current interim
      ta.value = _snapshotBefore + committed + ' ' + interim;
    } else {
      // All final — commit and expose for undo
      _lastInserted = committed;
      ta.value      = _snapshotBefore + _lastInserted;
      showUndo();
    }
  });

  recognition.addEventListener('end', () => {
    if (_active) {
      // Unexpected stop while toggle is on — restart to keep continuous
      try { recognition.start(); } catch (_) {}
    } else {
      setMicState('idle');
    }
  });

  recognition.addEventListener('error', (e) => {
    setMicState('idle');
    _active = false;
    // Restore snapshot so interim preview doesn't linger on error
    ta.value = _snapshotBefore;
    const msgs = {
      'not-allowed':   'Microphone access denied — check browser settings.',
      'audio-capture': 'No microphone found.',
      'network':       'Network error during voice recognition.',
      'no-speech':     '',  // silence timeout — reset quietly
    };
    const msg = Object.prototype.hasOwnProperty.call(msgs, e.error) ? msgs[e.error] : `Voice error: ${e.error}`;
    if (msg) setMicError(msg);
  });

  // ── Undo ────────────────────────────────────────────────────────
  // Remove only the dictated segment, preserving any manual edits made after it.
  // Layout: [_snapshotBefore][_lastInserted][user edits after dictation]
  micUndoBtn.addEventListener('click', () => {
    const insertEnd   = _snapshotBefore.length + _lastInserted.length;
    const afterEdits  = ta.value.slice(insertEnd);   // manual edits after dictation
    ta.value          = _snapshotBefore + afterEdits;
    micUndo.style.display = 'none';
    clearTimeout(_undoTimer);
  });
})();
