const state = {
  jobId: null,
  job: null,
  activeRecipeId: null,
  pollTimer: null,
};

const el = (id) => document.getElementById(id);

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str ?? '';
  return d.innerHTML;
}

// ---------- Job URL (survive an accidental reload / allow bookmarking) ----------

function setJobUrl(jobId) {
  const url = new URL(window.location);
  if (jobId) {
    url.searchParams.set('job', jobId);
  } else {
    url.searchParams.delete('job');
  }
  window.history.replaceState({}, '', url);
}

async function tryRestoreJobFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const jobId = params.get('job');
  if (!jobId) return;

  try {
    const res = await fetch(`/api/jobs/${jobId}`);
    if (!res.ok) throw new Error('not found');
    const job = await res.json();
    state.jobId = jobId;
    state.job = job;
    updateUsageDisplay(job.token_usage);

    if (job.status === 'ready') {
      showReview();
      return;
    }

    el('upload-screen').classList.add('hidden');
    el('processing-screen').classList.remove('hidden');

    if (job.status === 'error') {
      el('processing-text').textContent = `${t('processingErrorPrefix')} ${job.error}`;
      el('progress-track').classList.add('hidden');
      return;
    }

    updateProgressUI(job);
    pollJob();
  } catch {
    // Job no longer exists (e.g. cleaned up) or the id is invalid - just
    // drop it from the URL and show the normal upload screen.
    setJobUrl(null);
  }
}

// ---------- Tandoor connection badge ----------

async function checkTandoor() {
  const badge = el('tandoor-badge');
  const banner = el('tandoor-error-banner');
  try {
    const res = await fetch('/api/tandoor/status');
    const data = await res.json();
    if (data.connected) {
      badge.textContent = t('tandoorConnected');
      badge.className = 'tandoor-badge ok tandoor-badge-clickable';
      badge.title = t('tandoorOpenHint');
      banner.classList.add('hidden');
    } else {
      badge.textContent = t('tandoorUnreachable');
      badge.className = 'tandoor-badge fail';
      badge.removeAttribute('title');
      const message = data.error || t('tandoorUnknownError');
      banner.innerHTML = `<strong>${t('tandoorConnFailedPrefix')}</strong> ${escapeHtml(message)}`;
      banner.classList.remove('hidden');
    }
  } catch {
    badge.textContent = t('tandoorUnknown');
    badge.className = 'tandoor-badge fail';
    badge.removeAttribute('title');
    banner.textContent = t('tandoorNoConfigHint');
    banner.classList.remove('hidden');
  }
}

el('tandoor-badge').addEventListener('click', () => {
  if (APP_CONFIG.tandoor_url && el('tandoor-badge').classList.contains('ok')) {
    window.open(APP_CONFIG.tandoor_url, '_blank', 'noopener');
  }
});

// ---------- Token usage badge ----------

function updateUsageDisplay(usage) {
  if (!usage) return;
  const total = (usage.input_tokens || 0) + (usage.output_tokens || 0);
  const badge = el('usage-badge');
  if (total <= 0) {
    badge.classList.add('hidden');
    return;
  }
  badge.textContent = tf('usageBadge', { total: total.toLocaleString() });
  badge.title = tf('usageBadgeTitle', {
    input: (usage.input_tokens || 0).toLocaleString(),
    output: (usage.output_tokens || 0).toLocaleString(),
  });
  badge.classList.remove('hidden');
}

// ---------- Upload ----------

const dropzone = el('dropzone');
const fileInput = el('file-input');

const FALLBACK_IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.heic', '.heif'];
const FALLBACK_PDF_EXTENSIONS = ['.pdf'];
const FALLBACK_EPUB_EXTENSIONS = ['.epub'];

function extOf(filename) {
  const i = filename.lastIndexOf('.');
  return i === -1 ? '' : filename.slice(i).toLowerCase();
}

function classifyFiles(files) {
  const imageExts = APP_CONFIG.image_extensions || FALLBACK_IMAGE_EXTENSIONS;
  const pdfExts = FALLBACK_PDF_EXTENSIONS;
  const epubExts = FALLBACK_EPUB_EXTENSIONS;
  const exts = files.map((f) => extOf(f.name));

  if (files.length === 1 && pdfExts.includes(exts[0])) return { ok: true };
  if (files.length === 1 && epubExts.includes(exts[0])) return { ok: true };
  if (exts.length > 0 && exts.every((e) => imageExts.includes(e))) return { ok: true };
  if (files.length > 1 && exts.some((e) => pdfExts.includes(e) || epubExts.includes(e))) {
    return { ok: false, error: t('onlyOnePdfOrEpubError') };
  }
  return { ok: false, error: t('unsupportedFileTypeError') };
}

['dragenter', 'dragover'].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add('dragover');
  })
);
['dragleave', 'drop'].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
  })
);
dropzone.addEventListener('drop', (e) => {
  const files = Array.from(e.dataTransfer.files || []);
  if (files.length) uploadFiles(files);
});
fileInput.addEventListener('change', () => {
  const files = Array.from(fileInput.files || []);
  if (files.length) uploadFiles(files);
});

async function uploadFiles(files) {
  el('upload-error').classList.add('hidden');
  requestNotificationPermission();

  const check = classifyFiles(files);
  if (!check.ok) {
    showUploadError(check.error);
    return;
  }

  const formData = new FormData();
  files.forEach((f) => formData.append('files', f));

  const label = files.length === 1 ? files[0].name : tf('photosCount', { n: files.length });

  el('upload-screen').classList.add('hidden');
  el('processing-screen').classList.remove('hidden');
  el('progress-track').classList.add('hidden');
  el('usage-badge').classList.add('hidden');
  el('processing-text').textContent = tf('uploadingFile', { filename: label });

  try {
    const res = await fetch('/api/upload', { method: 'POST', body: formData });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `${t('uploadFailedPrefix')} (${res.status})`);
    }
    const data = await res.json();
    state.jobId = data.job_id;
    setJobUrl(data.job_id);
    el('processing-text').textContent = t('processingDefault');
    pollJob();
  } catch (e) {
    el('processing-screen').classList.add('hidden');
    el('upload-screen').classList.remove('hidden');
    showUploadError(e.message);
  }
}

// Single recipe from a web page: same processing / review / import flow.
el('url-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const url = el('url-input').value.trim();
  if (!url) return;
  el('upload-error').classList.add('hidden');
  requestNotificationPermission();
  el('upload-screen').classList.add('hidden');
  el('processing-screen').classList.remove('hidden');
  el('progress-track').classList.add('hidden');
  el('usage-badge').classList.add('hidden');
  el('processing-text').textContent = t('urlImportLoading');
  try {
    const res = await fetch('/api/import-url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `${t('uploadFailedPrefix')} (${res.status})`);
    }
    const data = await res.json();
    state.jobId = data.job_id;
    setJobUrl(data.job_id);
    el('url-input').value = '';
    pollJob();
  } catch (err) {
    el('processing-screen').classList.add('hidden');
    el('upload-screen').classList.remove('hidden');
    showUploadError(err.message);
  }
});

function showUploadError(msg) {
  const box = el('upload-error');
  box.textContent = msg;
  box.classList.remove('hidden');
}

// ---------- Browser notifications (so you can leave the tab during a long extraction) ----------

function requestNotificationPermission() {
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission().catch(() => {});
  }
}

function notifyJobFinished(job) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  if (document.visibilityState === 'visible') return; // don't bug the user if they're already looking at it

  const count = (job.recipes && job.recipes.length) || 0;
  let title;
  let body;
  if (job.status === 'ready') {
    title = t('notificationReadyTitle');
    body = tf('notificationReadyBody', { n: count });
  } else {
    title = t('notificationErrorTitle');
    body = job.error || '';
  }

  try {
    const notification = new Notification(title, { body, icon: undefined, tag: `job-${job.id}` });
    notification.onclick = () => {
      window.focus();
      notification.close();
    };
  } catch {
    // Some browsers/contexts (e.g. no service worker, insecure origin) can
    // reject the Notification constructor - fail silently, it's a nice-to-have.
  }
}

// ---------- Polling ----------

function pollJob() {
  clearTimeout(state.pollTimer);
  const tick = async () => {
    try {
      const res = await fetch(`/api/jobs/${state.jobId}`);
      if (!res.ok) throw new Error(t('jobNotFoundError'));
      const job = await res.json();
      state.job = job;
      updateUsageDisplay(job.token_usage);

      if (job.status === 'ready') {
        notifyJobFinished(job);
        showReview();
        return;
      }
      if (job.status === 'error') {
        notifyJobFinished(job);
        el('processing-text').textContent = `${t('processingErrorPrefix')} ${job.error}`;
        el('progress-track').classList.add('hidden');
        return;
      }

      updateProgressUI(job);
      state.pollTimer = setTimeout(tick, 2000);
    } catch (e) {
      el('processing-text').textContent = `${t('processingErrorPrefix')} ${e.message}`;
    }
  };
  tick();
}

function updateProgressUI(job) {
  el('processing-text').textContent = job.progress_label || t('processingDefault');
  const track = el('progress-track');
  const fill = el('progress-fill');
  if (job.progress_total > 0) {
    track.classList.remove('hidden');
    const pct = Math.min(100, Math.round((job.progress_current / job.progress_total) * 100));
    fill.style.width = `${pct}%`;
  } else {
    track.classList.add('hidden');
  }
}

// ---------- Review screen ----------

function showReview() {
  el('processing-screen').classList.add('hidden');
  el('review-screen').classList.remove('hidden');
  el('action-bar').classList.remove('hidden');
  el('cookbook-name-input').value = state.job.cookbook_name || state.job.suggested_cookbook_name || '';
  updateUsageDisplay(state.job.token_usage);
  renderRecipeList();
  updateSelectionCount();
  if (state.job.recipes.length > 0) {
    selectRecipe(state.job.recipes[0].id);
  }
}

el('cookbook-name-input').addEventListener('blur', () => {
  const value = el('cookbook-name-input').value.trim();
  state.job.cookbook_name = value;
  fetch(`/api/jobs/${state.jobId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cookbook_name: value }),
  }).catch(() => {});
});

function imageUrl(imageId) {
  if (!imageId) return null;
  return `/api/jobs/${state.jobId}/images/${imageId}`;
}

function renderRecipeList() {
  const container = el('recipe-list-items');
  container.innerHTML = '';
  const recipes = state.job.recipes;
  el('recipe-count-label').textContent = tf(recipes.length === 1 ? 'recipesFound' : 'recipesFoundPlural', { n: recipes.length });

  recipes.forEach((r) => {
    const item = document.createElement('div');
    item.className = 'recipe-item' + (r.id === state.activeRecipeId ? ' active' : '');
    const opensInTandoor = r.import_status === 'imported' && r.tandoor_recipe_id && APP_CONFIG.tandoor_url;
    if (opensInTandoor) item.title = t('tandoorOpenRecipeHint');
    item.innerHTML = `
      <input type="checkbox" ${r.selected ? 'checked' : ''} data-id="${r.id}" class="select-cb" />
      <div class="thumb" style="${r.selected_image_id ? `background-image:url('${imageUrl(r.selected_image_id)}')` : ''}"></div>
      <div class="meta">
        <div class="title">${escapeHtml(r.title)}</div>
        <div class="sub">${t('pageLabel')} ${r.source_page_start}${r.source_page_end !== r.source_page_start ? '–' + r.source_page_end : ''}</div>
        ${duplicateBadge(r)}
        ${statusPill(r)}
      </div>
    `;
    item.addEventListener('click', (e) => {
      if (e.target.classList.contains('select-cb')) return;
      if (opensInTandoor) {
        window.open(`${APP_CONFIG.tandoor_url}/view/recipe/${r.tandoor_recipe_id}`, 'tandoorRecipePopup', 'width=900,height=850,noopener');
        return;
      }
      selectRecipe(r.id);
    });
    item.querySelector('.select-cb').addEventListener('click', (e) => {
      e.stopPropagation();
      toggleSelected(r.id, e.target.checked);
    });
    container.appendChild(item);
  });

  updateRetryButtonVisibility();
}

function updateRetryButtonVisibility() {
  const hasFailed = state.job.recipes.some((r) => r.import_status === 'error');
  el('retry-failed-btn').classList.toggle('hidden', !hasFailed);

  const hasImported = state.job.recipes.some((r) => r.import_status === 'imported');
  el('undo-all-btn').classList.toggle('hidden', !hasImported);
}

el('undo-all-btn').addEventListener('click', async () => {
  if (!confirm(t('confirmUndoAll'))) return;

  const btn = el('undo-all-btn');
  btn.disabled = true;
  btn.textContent = t('undoingAll');

  try {
    const res = await fetch(`/api/jobs/${state.jobId}/undo-all-imports`, { method: 'POST' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || t('undoAllFailed'));
    }
    const data = await res.json();
    data.results.forEach((result) => {
      const r = findRecipe(result.id);
      if (!r) return;
      if (result.status === 'undone') {
        r.import_status = 'pending';
        r.tandoor_recipe_id = null;
        r.import_error = null;
      } else {
        r.import_error = result.error;
      }
    });
    renderRecipeList();
    if (state.activeRecipeId) renderDetail(findRecipe(state.activeRecipeId));
  } catch (e) {
    alert(`${t('undoAllFailed')}: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = t('undoAllBtn');
  }
});

function duplicateBadge(r) {
  if (!r.duplicate_match) return '';
  const cls = r.duplicate_exact ? 'duplicate' : 'duplicate-similar';
  const label = r.duplicate_exact ? t('duplicateBadgeExact') : t('duplicateBadgeSimilar');
  return `<span class="status-pill ${cls}">⚠ ${label}</span>`;
}

function statusPill(r) {
  if (r.import_status === 'pending') return '';
  const labels = { importing: t('statusImporting'), imported: t('statusImported'), error: t('statusError') };
  return `<span class="status-pill ${r.import_status}">${labels[r.import_status] || r.import_status}</span>`;
}

function findRecipe(id) {
  return state.job.recipes.find((r) => r.id === id);
}

function toggleSelected(id, checked) {
  const r = findRecipe(id);
  r.selected = checked;
  patchRecipe(id, { selected: checked });
  updateSelectionCount();
}

function updateSelectionCount() {
  const n = state.job.recipes.filter((r) => r.selected).length;
  el('selection-count').textContent = tf('selectionCount', { n, total: state.job.recipes.length });
}

el('toggle-all-btn').addEventListener('click', () => {
  const allSelected = state.job.recipes.every((r) => r.selected);
  state.job.recipes.forEach((r) => { r.selected = !allSelected; });
  renderRecipeList();
  updateSelectionCount();
  state.job.recipes.forEach((r) => patchRecipe(r.id, { selected: r.selected }));
});

function selectRecipe(id) {
  state.activeRecipeId = id;
  renderRecipeList();
  renderDetail(findRecipe(id));
  scrollActiveRecipeIntoView();
}

function scrollActiveRecipeIntoView() {
  const activeEl = document.querySelector('.recipe-item.active');
  if (activeEl) activeEl.scrollIntoView({ block: 'nearest' });
}

// ---------- Keyboard navigation (Up/Down = move, Space = toggle selected) ----------

document.addEventListener('keydown', (e) => {
  if (!state.job || !state.job.recipes || state.job.recipes.length === 0) return;
  if (el('review-screen').classList.contains('hidden')) return;

  // Don't hijack typing in a field, or interact while a modal is open
  const activeTag = document.activeElement ? document.activeElement.tagName : '';
  if (activeTag === 'INPUT' || activeTag === 'TEXTAREA' || activeTag === 'SELECT') return;
  if (!el('success-modal').classList.contains('hidden')) return;

  const recipes = state.job.recipes;

  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const currentIdx = recipes.findIndex((r) => r.id === state.activeRecipeId);
    let nextIdx;
    if (currentIdx === -1) {
      nextIdx = 0;
    } else if (e.key === 'ArrowDown') {
      nextIdx = Math.min(currentIdx + 1, recipes.length - 1);
    } else {
      nextIdx = Math.max(currentIdx - 1, 0);
    }
    selectRecipe(recipes[nextIdx].id);
  } else if (e.code === 'Space' || e.key === ' ') {
    if (!state.activeRecipeId) return;
    e.preventDefault();
    const r = findRecipe(state.activeRecipeId);
    if (r) {
      toggleSelected(r.id, !r.selected);
      renderRecipeList();
    }
  }
});

function matchBadgeHtml(ing) {
  if (ing.tandoor_match === 'exists') {
    return `<span class="ing-match exists" title="${escapeHtml(t('matchExistsTitle'))}">✓</span>`;
  }
  if (ing.tandoor_match === 'matched') {
    const title = tf('matchMatchedTitle', { original: ing.original_name || '' });
    return `<span class="ing-match matched" role="button" title="${escapeHtml(title)}">↺</span>`;
  }
  if (ing.tandoor_match === 'new') {
    return `<span class="ing-match new" title="${escapeHtml(t('matchNewTitle'))}">${t('matchNewLabel')}</span>`;
  }
  return '';
}

function renderDetail(r) {
  const detail = el('recipe-detail');

  const ingredientsHtml = r.ingredients.map((ing, i) => {
    const stepOptions = r.steps.map((s, si) => `<option value="${si}" ${((ing.step_index ?? 0) === si) ? 'selected' : ''}>${si + 1}</option>`).join('');
    return `
    <div class="ingredient-row" data-idx="${i}" draggable="true">
      <span class="ing-drag-handle" title="${t('dragToReorder')}">⠿</span>
      <input class="ing-amount" value="${ing.amount ?? ''}" placeholder="${t('placeholderAmount')}" />
      <input class="ing-unit" value="${escapeHtml(ing.unit ?? '')}" placeholder="${t('placeholderUnit')}" />
      <div class="ing-name-wrap">
        <input class="ing-name" value="${escapeHtml(ing.name)}" placeholder="${t('placeholderIngredient')}" />
        ${matchBadgeHtml(ing)}
      </div>
      <select class="ing-step" title="${t('fieldStepAssignment')}">${stepOptions || '<option value="0">1</option>'}</select>
      <input class="ing-note" value="${escapeHtml(ing.note ?? '')}" placeholder="${t('placeholderNote')}" />
    </div>
  `;
  }).join('') || `<p style="color:#8f9689;font-size:0.88rem;">${t('noIngredients')}</p>`;

  const matchCounts = { exists: 0, matched: 0, new: 0 };
  r.ingredients.forEach((ing) => { if (ing.tandoor_match) matchCounts[ing.tandoor_match] += 1; });
  const matchSummary = (matchCounts.exists + matchCounts.matched + matchCounts.new) > 0
    ? `<div class="ing-match-summary">${escapeHtml(tf('matchSummary', matchCounts))}</div>`
    : '';

  const stepsHtml = r.steps.map((s, i) => `
    <div class="step-row" data-idx="${i}">
      <div class="step-num">${i + 1}</div>
      <textarea class="step-instruction">${escapeHtml(s.instruction)}</textarea>
    </div>
  `).join('') || `<p style="color:#8f9689;font-size:0.88rem;">${t('noSteps')}</p>`;

  const tagsHtml = r.tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join('') || `<span style="color:#8f9689;font-size:0.85rem;">${t('noTags')}</span>`;

  const generateTileHtml = APP_CONFIG.image_gen_available
    ? `<div class="image-choice generate-tile" data-generate="1" title="${t('generateImageBtn')}">
         <span class="generate-tile-icon">✨</span>
       </div>`
    : '';

  const imageChoicesHtml = r.candidate_image_ids.map((iid) => `
    <div class="image-choice ${iid === r.selected_image_id ? 'selected' : ''}" data-image-id="${iid}"
         style="background-image:url('${imageUrl(iid)}')"></div>
  `).join('') + `<div class="image-choice none ${!r.selected_image_id ? 'selected' : ''}" data-image-id="">${t('noImage')}</div>` + generateTileHtml;

  const duplicateNotice = r.duplicate_match
    ? `<div class="error-banner duplicate-banner" style="margin-bottom:20px;">${escapeHtml(
        tf(r.duplicate_exact ? 'duplicateExactWarning' : 'duplicateSimilarWarning', { name: r.duplicate_match })
      )}</div>`
    : '';

  const tandoorLink = (APP_CONFIG.tandoor_url && r.tandoor_recipe_id)
    ? `<a class="btn secondary" href="${APP_CONFIG.tandoor_url}/view/recipe/${r.tandoor_recipe_id}" target="_blank" rel="noopener">${t('openInTandoorBtn')}</a>`
    : '';

  const importedNotice = r.import_status === 'imported'
    ? `<div class="imported-banner">
         <span>${t('importedBannerText')}</span>
         <div class="imported-banner-actions">
           ${tandoorLink}
           <button class="btn secondary undo-import-btn" type="button">${t('undoImportBtn')}</button>
         </div>
       </div>`
    : '';

  detail.innerHTML = `
    ${r.import_error ? `<div class="error-banner" style="margin-bottom:20px;">${escapeHtml(r.import_error)}</div>` : ''}
    ${duplicateNotice}
    ${importedNotice}
    <div class="field title-field">
      <input id="f-title" value="${escapeHtml(r.title)}" />
    </div>
    <div class="field description-field">
      <textarea id="f-description" placeholder="${t('descriptionPlaceholder')}">${escapeHtml(r.description ?? '')}</textarea>
    </div>

    <div class="section-title">${t('fieldImage')}</div>
    <div class="image-picker">${imageChoicesHtml}</div>

    <div class="section-title">${t('fieldDetails')}</div>
    <div class="field-row">
      <div class="field"><label>${t('fieldServings')}</label><input id="f-servings" type="number" value="${r.servings ?? ''}" /></div>
      <div class="field"><label>${t('fieldPrep')}</label><input id="f-prep" type="number" value="${r.prep_time_minutes ?? ''}" /></div>
      <div class="field"><label>${t('fieldCook')}</label><input id="f-cook" type="number" value="${r.cook_time_minutes ?? ''}" /></div>
    </div>

    <div class="section-title">${t('fieldTags')}</div>
    <div class="tags-row">${tagsHtml}</div>

    <div class="section-title">${t('fieldIngredients')} <span class="hint-inline">(${t('fieldStepAssignment')})</span></div>
    ${matchSummary}
    <div id="ingredients-wrap">${ingredientsHtml}</div>

    <div class="section-title">${t('fieldSteps')}</div>
    <div id="steps-wrap">${stepsHtml}</div>
  `;

  // Image selection / AI generation tile
  detail.querySelectorAll('.image-choice').forEach((elm) => {
    if (elm.dataset.generate) {
      elm.addEventListener('click', () => triggerImageGeneration(r, elm));
      return;
    }
    elm.addEventListener('click', () => {
      const imageId = elm.dataset.imageId || null;
      r.selected_image_id = imageId;
      patchRecipe(r.id, { selected_image_id: imageId });
      renderDetail(r);
      renderRecipeList();
    });
  });

  // Collect field edits and save them on blur/change
  const save = () => {
    r.title = detail.querySelector('#f-title').value;
    r.description = detail.querySelector('#f-description').value || null;
    r.servings = numOrNull(detail.querySelector('#f-servings').value);
    r.prep_time_minutes = numOrNull(detail.querySelector('#f-prep').value);
    r.cook_time_minutes = numOrNull(detail.querySelector('#f-cook').value);

    const previous = r.ingredients;
    r.ingredients = Array.from(detail.querySelectorAll('.ingredient-row')).map((row) => {
      const before = previous[Number(row.dataset.idx)] || {};
      const name = row.querySelector('.ing-name').value;
      // Keep the Tandoor match info only while the name is untouched - a
      // hand-edited name hasn't been checked against Tandoor.
      const unchanged = name === before.name;
      return {
        amount: numOrNull(row.querySelector('.ing-amount').value),
        unit: row.querySelector('.ing-unit').value || null,
        name,
        note: row.querySelector('.ing-note').value || null,
        group: null,
        step_index: numOrNull(row.querySelector('.ing-step')?.value) ?? 0,
        tandoor_match: unchanged ? (before.tandoor_match ?? null) : null,
        original_name: unchanged ? (before.original_name ?? null) : null,
      };
    });

    r.steps = Array.from(detail.querySelectorAll('.step-row')).map((row) => ({
      instruction: row.querySelector('.step-instruction').value,
      title: null,
      time_minutes: null,
    }));

    patchRecipe(r.id, {
      title: r.title,
      description: r.description,
      servings: r.servings,
      prep_time_minutes: r.prep_time_minutes,
      cook_time_minutes: r.cook_time_minutes,
      ingredients: r.ingredients,
      steps: r.steps,
    });
    renderRecipeList();
  };

  detail.querySelectorAll('input, textarea').forEach((inp) => {
    inp.addEventListener('blur', save);
  });
  detail.querySelectorAll('select').forEach((sel) => {
    sel.addEventListener('change', save);
  });

  // Undo a match: back to the extracted name, which becomes a new ingredient.
  detail.querySelectorAll('.ing-match.matched').forEach((badge) => {
    badge.addEventListener('click', () => {
      const ing = r.ingredients[Number(badge.closest('.ingredient-row').dataset.idx)];
      if (!ing || !ing.original_name) return;
      ing.name = ing.original_name;
      ing.original_name = null;
      ing.tandoor_match = 'new';
      patchRecipe(r.id, { ingredients: r.ingredients });
      renderDetail(r);
    });
  });

  // Drag & drop to reorder ingredients
  const ingredientsWrap = el('ingredients-wrap');
  let dragSrcRow = null;

  detail.querySelectorAll('.ingredient-row').forEach((row) => {
    row.addEventListener('dragstart', (e) => {
      dragSrcRow = row;
      row.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      try { e.dataTransfer.setData('text/plain', row.dataset.idx || ''); } catch {}
    });

    row.addEventListener('dragend', () => {
      row.classList.remove('dragging');
      ingredientsWrap.querySelectorAll('.ingredient-row').forEach((el2) => {
        el2.classList.remove('drag-over-top', 'drag-over-bottom');
      });
      dragSrcRow = null;
    });

    row.addEventListener('dragover', (e) => {
      if (!dragSrcRow || dragSrcRow === row) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      const rect = row.getBoundingClientRect();
      const before = (e.clientY - rect.top) < rect.height / 2;
      row.classList.toggle('drag-over-top', before);
      row.classList.toggle('drag-over-bottom', !before);
    });

    row.addEventListener('dragleave', () => {
      row.classList.remove('drag-over-top', 'drag-over-bottom');
    });

    row.addEventListener('drop', (e) => {
      if (!dragSrcRow || dragSrcRow === row) return;
      e.preventDefault();
      const rect = row.getBoundingClientRect();
      const before = (e.clientY - rect.top) < rect.height / 2;
      ingredientsWrap.insertBefore(dragSrcRow, before ? row : row.nextSibling);
      row.classList.remove('drag-over-top', 'drag-over-bottom');
      save();
    });
  });

  const undoBtn = detail.querySelector('.undo-import-btn');
  if (undoBtn) {
    undoBtn.addEventListener('click', async () => {
      undoBtn.disabled = true;
      undoBtn.textContent = t('undoingImport');
      try {
        const res = await fetch(`/api/jobs/${state.jobId}/recipes/${r.id}/undo-import`, { method: 'POST' });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || t('undoImportFailed'));
        }
        const updated = await res.json();
        Object.assign(r, updated);
        renderRecipeList();
        renderDetail(r);
      } catch (e) {
        alert(`${t('undoImportFailed')}: ${e.message}`);
        undoBtn.disabled = false;
        undoBtn.textContent = t('undoImportBtn');
      }
    });
  }
}

async function triggerImageGeneration(recipe, tileEl) {
  tileEl.classList.add('loading');
  tileEl.innerHTML = '<span class="generate-tile-icon spin">✨</span>';
  const allTiles = tileEl.parentElement.querySelectorAll('.image-choice');
  allTiles.forEach((t) => { if (t !== tileEl) t.style.pointerEvents = 'none'; });

  try {
    const res = await fetch(`/api/jobs/${state.jobId}/recipes/${recipe.id}/generate-image`, { method: 'POST' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || t('imageGenFailed'));
    }
    const data = await res.json();
    Object.assign(recipe, data.recipe);
    state.job.images[data.image_id] = { page: recipe.source_page_start, filename: `${data.image_id}.png` };
    renderRecipeList();
    renderDetail(recipe);
  } catch (e) {
    alert(`${t('imageGenFailed')}: ${e.message}`);
    tileEl.classList.remove('loading');
    tileEl.innerHTML = '<span class="generate-tile-icon">✨</span>';
    allTiles.forEach((t) => { t.style.pointerEvents = ''; });
  }
}

function numOrNull(v) {
  if (v === '' || v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isNaN(n) ? null : n;
}

let patchTimer = null;
function patchRecipe(id, partial) {
  clearTimeout(patchTimer);
  patchTimer = setTimeout(() => {
    fetch(`/api/jobs/${state.jobId}/recipes/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(partial),
    }).catch(() => {});
  }, 150);
}

// ---------- Import ----------

async function runImport(recipeIds) {
  if (recipeIds.length === 0) return;

  const importBtn = el('import-btn');
  const retryBtn = el('retry-failed-btn');
  importBtn.disabled = true;
  retryBtn.disabled = true;
  const originalImportLabel = importBtn.textContent;
  importBtn.textContent = t('importBtnLoading');

  const idSet = new Set(recipeIds);
  state.job.recipes.forEach((r) => {
    if (idSet.has(r.id)) r.import_status = 'importing';
  });
  renderRecipeList();

  const cookbookName = el('cookbook-name-input').value.trim();

  try {
    const res = await fetch(`/api/jobs/${state.jobId}/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ recipe_ids: recipeIds, cookbook_name: cookbookName }),
    });
    const data = await res.json();
    data.results.forEach((result) => {
      const r = findRecipe(result.id);
      if (!r) return;
      r.import_status = result.status;
      r.import_error = result.error;
      r.tandoor_recipe_id = result.tandoor_recipe_id;
    });
    showImportSummary(data.results, data.cookbook_name, data.cookbook_warning);
  } catch (e) {
    alert(`${t('importFailedAlertPrefix')} ${e.message}`);
  } finally {
    importBtn.disabled = false;
    retryBtn.disabled = false;
    importBtn.textContent = originalImportLabel;
    renderRecipeList();
    if (state.activeRecipeId) renderDetail(findRecipe(state.activeRecipeId));
  }
}

el('import-btn').addEventListener('click', () => {
  const selectedIds = state.job.recipes.filter((r) => r.selected).map((r) => r.id);
  runImport(selectedIds);
});

el('retry-failed-btn').addEventListener('click', () => {
  const failedIds = state.job.recipes.filter((r) => r.import_status === 'error').map((r) => r.id);
  runImport(failedIds);
});

function showImportSummary(results, cookbookName, cookbookWarning) {
  const total = results.length;
  const ok = results.filter((r) => r.status === 'imported').length;
  const failed = total - ok;

  const icon = el('modal-icon');
  const title = el('modal-title');
  const body = el('modal-body');

  if (failed === 0) {
    icon.textContent = '🎉';
    title.textContent = t('modalTitleSuccess');
  } else if (ok === 0) {
    icon.textContent = '⚠️';
    title.textContent = t('modalTitleFailed');
  } else {
    icon.textContent = '⚠️';
    title.textContent = t('modalTitlePartial');
  }

  let lines = [];
  lines.push(tf('modalSummaryLine', { ok, total }));
  if (cookbookName) {
    lines.push(tf('modalCookbookLine', { name: cookbookName }));
  }
  if (cookbookWarning) {
    lines.push(tf('modalCookbookWarningLine', { warning: cookbookWarning }));
  }
  if (failed > 0) {
    lines.push(tf('modalFailedLine', { failed }));
  }
  if (ok > 0) {
    lines.push(t('modalOpenInListHint'));
  }
  body.textContent = lines.join('\n');

  el('success-modal').classList.remove('hidden');
}

el('modal-close-btn').addEventListener('click', () => {
  el('success-modal').classList.add('hidden');
});

el('modal-new-upload-btn').addEventListener('click', () => {
  el('success-modal').classList.add('hidden');
  resetToUpload();
});

function resetToUpload() {
  clearTimeout(state.pollTimer);
  state.jobId = null;
  state.job = null;
  state.activeRecipeId = null;
  setJobUrl(null);

  fileInput.value = '';
  el('upload-error').classList.add('hidden');
  el('review-screen').classList.add('hidden');
  el('action-bar').classList.add('hidden');
  el('processing-screen').classList.add('hidden');
  el('usage-badge').classList.add('hidden');
  el('tools-screen').classList.add('hidden');
  el('upload-screen').classList.remove('hidden');
}

el('brand-link').addEventListener('click', goHome);
el('brand-link').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    goHome();
  }
});

function goHome() {
  const alreadyHome = !el('upload-screen').classList.contains('hidden');
  if (alreadyHome) return;

  if (state.jobId) {
    if (!confirm(t('confirmGoHome'))) return;
  }
  el('success-modal').classList.add('hidden');
  el('tools-screen').classList.add('hidden');
  resetToUpload();
}

// ---------- Maintenance tools ----------

const toolsState = { jobId: null, pollTimer: null, job: null, selected: new Set(), busy: false };

el('tools-nav-btn').addEventListener('click', () => {
  el('upload-screen').classList.add('hidden');
  el('review-screen').classList.add('hidden');
  el('processing-screen').classList.add('hidden');
  el('action-bar').classList.add('hidden');
  el('tools-screen').classList.remove('hidden');
  el('tools-cards-view').classList.remove('hidden');
  el('tools-run-view').classList.add('hidden');
  loadNewRecipesStatus();
  loadOpenRuns();
  loadMealPlanOptions();
});

// ---------- Meal plan ----------

function nextMonday() {
  const d = new Date();
  d.setDate(d.getDate() + ((8 - d.getDay()) % 7 || 7));
  return d.toISOString().slice(0, 10);
}

async function loadMealPlanOptions() {
  if (!el('mp-start').value) el('mp-start').value = nextMonday();
  const select = el('mp-meal');
  const hint = el('mp-hint');
  try {
    const res = await fetch('/api/tools/meal-plan/options');
    const data = await res.json();
    const previous = select.value;
    select.innerHTML = data.meal_types.map((m) => `<option value="${m.id}">${escapeHtml(m.name)}</option>`).join('');
    if (previous) select.value = previous;
    // Default to dinner if there is one.
    if (!previous) {
      const dinner = data.meal_types.find((m) => /abend|dinner|dîner|cena/i.test(m.name));
      if (dinner) select.value = dinner.id;
    }
    const none = data.meal_types.length === 0;
    hint.textContent = none ? t('mealPlanNoMealTypes') : '';
    hint.classList.toggle('hidden', !none);
    el('mp-start-btn').disabled = none;
  } catch (e) {
    hint.textContent = `${t('toolNewRecipesStatusFailed')}: ${e.message}`;
    hint.classList.remove('hidden');
  }
}

el('meal-plan-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const select = el('mp-meal');
  startTool('/api/tools/meal-plan', t('toolMealPlanTitle'), {
    start_date: el('mp-start').value,
    days: Number(el('mp-days').value),
    meal_type: { id: Number(select.value), name: select.options[select.selectedIndex]?.textContent || '' },
    servings: Number(el('mp-servings').value),
    wishes: el('mp-wishes').value,
    add_to_shopping: el('mp-shopping').checked,
  });
});

// Runs of the other tools that still have suggestions to review - they're
// saved on the server, so they can be reopened after a reload or restart.
// ("Process new recipes" lists its own runs in its card.)
async function loadOpenRuns() {
  const box = el('tools-open-runs');
  try {
    const res = await fetch('/api/tools/jobs');
    const runs = (await res.json()).filter((r) => r.tool !== 'new_recipes');
    if (!runs.length) { box.classList.add('hidden'); return; }
    const titleOf = (tool) => {
      const card = document.querySelector(`.tool-start-btn[data-tool="${tool}"]`);
      return card ? card.closest('.tool-card').querySelector('h3').textContent : tool;
    };
    el('tools-open-runs-list').innerHTML = runs.map((r) => {
      const when = new Date(r.created_at * 1000).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' });
      const label = r.status === 'scanning'
        ? tf('toolOpenRunRunning', { tool: titleOf(r.tool), when })
        : tf('toolOpenRunPending', { tool: titleOf(r.tool), when, count: r.pending });
      return `<div class="new-recipes-open-job"><span>${escapeHtml(label)}</span>
        <button class="btn secondary" type="button" data-job-id="${r.id}" data-tool="${r.tool}">${t('toolNewRecipesOpenJob')}</button></div>`;
    }).join('');
    el('tools-open-runs-list').querySelectorAll('button').forEach((b) => {
      b.addEventListener('click', () => openToolJob(b.dataset.jobId, titleOf(b.dataset.tool)));
    });
    box.classList.remove('hidden');
  } catch (e) {
    box.classList.add('hidden');
  }
}

async function loadNewRecipesStatus() {
  const label = el('new-recipes-status');
  const btn = el('new-recipes-start-btn');
  btn.disabled = true;
  label.textContent = t('toolNewRecipesChecking');
  try {
    const res = await fetch('/api/tools/new-recipes/status');
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    if (data.baseline_created) {
      label.textContent = tf('toolNewRecipesBaseline', { count: data.baseline_count });
    } else {
      label.textContent = data.new_count > 0 ? tf('toolNewRecipesCount', { count: data.new_count }) : t('toolNewRecipesNone');
    }
    btn.disabled = data.new_count === 0;

    const auto = el('new-recipes-auto');
    if (data.auto_interval_hours > 0) {
      const next = data.next_auto_run_at
        ? new Date(data.next_auto_run_at * 1000).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })
        : '–';
      auto.textContent = tf('toolNewRecipesAuto', { hours: data.auto_interval_hours, next });
      auto.classList.remove('hidden');
    } else {
      auto.classList.add('hidden');
    }

    const list = el('new-recipes-open-jobs');
    list.innerHTML = (data.open_jobs || []).map((job) => {
      const when = new Date(job.created_at * 1000).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' });
      const label = job.status === 'scanning'
        ? tf('toolNewRecipesJobRunning', { when })
        : tf(job.auto ? 'toolNewRecipesJobAuto' : 'toolNewRecipesJobManual', { when, count: job.pending });
      return `<div class="new-recipes-open-job"><span>${escapeHtml(label)}</span>
        <button class="btn secondary" type="button" data-job-id="${job.id}">${t('toolNewRecipesOpenJob')}</button></div>`;
    }).join('');
    list.querySelectorAll('button').forEach((b) => {
      b.addEventListener('click', () => openToolJob(b.dataset.jobId, t('toolNewRecipesTitle')));
    });
  } catch (e) {
    label.textContent = `${t('toolNewRecipesStatusFailed')}: ${e.message}`;
  }
}

el('tools-back-btn').addEventListener('click', () => {
  clearTimeout(toolsState.pollTimer);
  toolsState.jobId = null;
  toolsState.job = null;
  toolsState.selected.clear();
  el('tools-run-view').classList.add('hidden');
  el('tools-cards-view').classList.remove('hidden');
  loadNewRecipesStatus();
  loadOpenRuns();
});

document.querySelectorAll('.tool-start-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    const title = btn.closest('.tool-card').querySelector('h3').textContent;
    startTool(btn.dataset.endpoint, title);
  });
});

async function startTool(endpoint, title, body) {
  resetToolRunView(title);
  try {
    const res = await fetch(endpoint, body
      ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
      : { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    toolsState.jobId = data.job_id;
    pollToolJob();
  } catch (e) {
    showToolError(`${t('toolStartFailed')}: ${e.message}`);
  }
}

// Opens a run that already exists (e.g. one the automatic schedule started).
function openToolJob(jobId, title) {
  resetToolRunView(title);
  toolsState.jobId = jobId;
  pollToolJob();
}

function resetToolRunView(title) {
  el('tools-cards-view').classList.add('hidden');
  el('tools-run-view').classList.remove('hidden');
  el('tools-run-title').textContent = title;
  el('tools-run-progress').classList.remove('hidden');
  el('tools-run-progress-label').textContent = t('toolScanStarting');
  el('tools-run-usage').classList.add('hidden');
  el('tools-run-error').classList.add('hidden');
  el('tools-cancelled-note').classList.add('hidden');
  el('tools-cancel-btn').disabled = false;
  el('tools-cancel-btn').textContent = t('toolCancelBtn');
  el('tools-suggestions-list').innerHTML = '';
  el('tools-bulk-bar').classList.add('hidden');
  el('tools-bulk-status').textContent = '';
  toolsState.job = null;
  toolsState.selected.clear();
}

el('tools-cancel-btn').addEventListener('click', async () => {
  if (!toolsState.jobId) return;
  const btn = el('tools-cancel-btn');
  btn.disabled = true;
  btn.textContent = t('toolCancelling');
  try {
    await fetch(`/api/tools/jobs/${toolsState.jobId}/cancel`, { method: 'POST' });
  } catch {
    // the next poll tick will show whatever state the job ends up in either way
  }
});

function pollToolJob() {
  clearTimeout(toolsState.pollTimer);
  const tick = async () => {
    try {
      const res = await fetch(`/api/tools/jobs/${toolsState.jobId}`);
      if (!res.ok) throw new Error(t('toolJobNotFoundError'));
      const job = await res.json();
      renderToolUsage(job);

      if (job.status === 'error') {
        showToolError(job.error || t('toolStartFailed'));
        return;
      }
      if (job.status === 'cancelled') {
        el('tools-run-progress').classList.add('hidden');
        el('tools-cancelled-note').classList.remove('hidden');
        renderToolSuggestions(job);
        return;
      }
      if (job.status === 'ready' || job.status === 'done') {
        el('tools-run-progress').classList.add('hidden');
        renderToolSuggestions(job);
        return;
      }

      el('tools-run-progress-label').textContent = job.progress_label || t('toolScanStarting');
      toolsState.pollTimer = setTimeout(tick, 1500);
    } catch (e) {
      showToolError(e.message);
    }
  };
  tick();
}

function showToolError(msg) {
  el('tools-run-progress').classList.add('hidden');
  const box = el('tools-run-error');
  box.textContent = msg;
  box.classList.remove('hidden');
}

function renderToolUsage(job) {
  const box = el('tools-run-usage');
  const hasActual = job.token_usage && job.token_usage.input_tokens > 0;

  if (hasActual) {
    // Once real usage starts coming in, switch from the pre-start estimate
    // to the actual running total - the estimate has done its job by then.
    box.textContent = tf('toolTokenUsage', {
      input: job.token_usage.input_tokens,
      output: job.token_usage.output_tokens,
    });
    box.classList.remove('hidden');
  } else if (job.cost_estimate) {
    box.textContent = job.cost_estimate;
    box.classList.remove('hidden');
  } else {
    box.classList.add('hidden');
  }
}

// Selection + bulk apply: every pending suggestion gets a checkbox, and the
// bar above the list applies/skips all checked ones. They're sent one after
// another (not in parallel) - merges touch shared recipes, and running them
// sequentially keeps the same order and safety as clicking them one by one.
function renderToolSuggestions(job) {
  toolsState.job = job;
  const list = el('tools-suggestions-list');
  const pendingIds = new Set(job.suggestions.filter((s) => s.status === 'pending').map((s) => s.id));
  // Drop selections that are no longer pending (applied/skipped/failed).
  toolsState.selected.forEach((id) => { if (!pendingIds.has(id)) toolsState.selected.delete(id); });

  if (job.suggestions.length === 0) {
    el('tools-bulk-bar').classList.add('hidden');
    list.innerHTML = `<p style="color:#8f9689;">${t('toolNoSuggestions')}</p>`;
    return;
  }
  el('tools-bulk-bar').classList.toggle('hidden', pendingIds.size === 0 && !toolsState.busy);

  list.innerHTML = job.suggestions.map((s) => `
    <div class="tool-suggestion-row ${s.status}" data-suggestion-id="${s.id}">
      ${s.status === 'pending'
        ? `<input type="checkbox" class="suggestion-check" ${toolsState.selected.has(s.id) ? 'checked' : ''} ${toolsState.busy ? 'disabled' : ''}>`
        : ''}
      <div class="suggestion-text">
        ${escapeHtml(s.summary)}
        ${s.preview ? `<details class="suggestion-preview"><summary>${t('toolShowPreview')}</summary><pre>${escapeHtml(s.preview)}</pre></details>` : ''}
      </div>
      ${s.status === 'pending' ? '' : `<span class="suggestion-status-label">${s.status === 'applied' ? t('toolStatusApplied') : s.status === 'skipped' ? t('toolStatusSkipped') : escapeHtml(s.error || t('toolStatusError'))}</span>`}
    </div>
  `).join('');

  list.querySelectorAll('.tool-suggestion-row.pending').forEach((row) => {
    const box = row.querySelector('.suggestion-check');
    const toggle = (checked) => {
      if (checked) toolsState.selected.add(row.dataset.suggestionId);
      else toolsState.selected.delete(row.dataset.suggestionId);
      updateBulkBar();
    };
    box.addEventListener('change', () => toggle(box.checked));
    // Clicking anywhere on the row toggles it too - except the preview.
    row.addEventListener('click', (e) => {
      if (toolsState.busy || e.target === box || e.target.closest('details')) return;
      box.checked = !box.checked;
      toggle(box.checked);
    });
  });
  updateBulkBar();
}

function updateBulkBar() {
  const job = toolsState.job;
  const pending = job ? job.suggestions.filter((s) => s.status === 'pending').length : 0;
  const n = toolsState.selected.size;
  const all = el('tools-select-all');
  all.checked = pending > 0 && n === pending;
  all.indeterminate = n > 0 && n < pending;
  all.disabled = toolsState.busy || pending === 0;
  el('tools-apply-selected-btn').textContent = tf('toolApplySelectedBtn', { count: n });
  el('tools-skip-selected-btn').textContent = tf('toolSkipSelectedBtn', { count: n });
  el('tools-apply-selected-btn').disabled = toolsState.busy || n === 0;
  el('tools-skip-selected-btn').disabled = toolsState.busy || n === 0;
}

el('tools-select-all').addEventListener('change', (e) => {
  const job = toolsState.job;
  if (!job) return;
  toolsState.selected = new Set(e.target.checked ? job.suggestions.filter((s) => s.status === 'pending').map((s) => s.id) : []);
  renderToolSuggestions(job);
});

el('tools-apply-selected-btn').addEventListener('click', () => runBulkAction('apply'));
el('tools-skip-selected-btn').addEventListener('click', () => runBulkAction('skip'));

async function runBulkAction(action) {
  const job = toolsState.job;
  if (!job || toolsState.busy) return;
  // Keep the list order, so e.g. merges run in the order they're shown.
  const ids = job.suggestions.filter((s) => toolsState.selected.has(s.id)).map((s) => s.id);
  if (ids.length === 0) return;

  const jobId = toolsState.jobId;
  toolsState.busy = true;
  renderToolSuggestions(job);
  const status = el('tools-bulk-status');
  let failed = 0;
  try {
    for (let i = 0; i < ids.length; i++) {
      if (toolsState.jobId !== jobId) return; // user left this run
      status.textContent = tf(action === 'apply' ? 'toolApplyingProgress' : 'toolSkippingProgress', { current: i + 1, total: ids.length });
      try {
        const res = await fetch(`/api/tools/jobs/${jobId}/suggestions/${ids[i]}/${action}`, { method: 'POST' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const s = await res.json();
        if (s.status === 'error') failed++;
      } catch (e) {
        failed++;
      }
      toolsState.selected.delete(ids[i]);
      const res2 = await fetch(`/api/tools/jobs/${jobId}`);
      if (res2.ok) renderToolSuggestions(await res2.json());
    }
  } finally {
    toolsState.busy = false;
    status.textContent = failed ? tf('toolBulkFailed', { count: failed }) : '';
    if (toolsState.job) renderToolSuggestions(toolsState.job);
  }
}

// ---------- Init ----------

(async function init() {
  await initI18n();
  await tryRestoreJobFromUrl();
  checkTandoor();
  setInterval(checkTandoor, 15000);
})();
