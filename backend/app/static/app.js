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

// ---------- Tandoor connection badge ----------

async function checkTandoor() {
  const badge = el('tandoor-badge');
  const banner = el('tandoor-error-banner');
  try {
    const res = await fetch('/api/tandoor/status');
    const data = await res.json();
    if (data.connected) {
      badge.textContent = t('tandoorConnected');
      badge.className = 'tandoor-badge ok';
      banner.classList.add('hidden');
    } else {
      badge.textContent = t('tandoorUnreachable');
      badge.className = 'tandoor-badge fail';
      const message = data.error || t('tandoorUnknownError');
      banner.innerHTML = `<strong>${t('tandoorConnFailedPrefix')}</strong> ${escapeHtml(message)}`;
      banner.classList.remove('hidden');
    }
  } catch {
    badge.textContent = t('tandoorUnknown');
    badge.className = 'tandoor-badge fail';
    banner.textContent = t('tandoorNoConfigHint');
    banner.classList.remove('hidden');
  }
}

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
  const file = e.dataTransfer.files[0];
  if (file) uploadFile(file);
});
fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) uploadFile(fileInput.files[0]);
});

async function uploadFile(file) {
  el('upload-error').classList.add('hidden');
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    showUploadError(t('onlyPdfError'));
    return;
  }

  const formData = new FormData();
  formData.append('file', file);

  el('upload-screen').classList.add('hidden');
  el('processing-screen').classList.remove('hidden');
  el('progress-track').classList.add('hidden');
  el('usage-badge').classList.add('hidden');
  el('processing-text').textContent = tf('uploadingFile', { filename: file.name });

  try {
    const res = await fetch('/api/upload', { method: 'POST', body: formData });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `${t('uploadFailedPrefix')} (${res.status})`);
    }
    const data = await res.json();
    state.jobId = data.job_id;
    el('processing-text').textContent = t('processingDefault');
    pollJob();
  } catch (e) {
    el('processing-screen').classList.add('hidden');
    el('upload-screen').classList.remove('hidden');
    showUploadError(e.message);
  }
}

function showUploadError(msg) {
  const box = el('upload-error');
  box.textContent = msg;
  box.classList.remove('hidden');
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
        showReview();
        return;
      }
      if (job.status === 'error') {
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
}

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
}

function renderDetail(r) {
  const detail = el('recipe-detail');

  const ingredientsHtml = r.ingredients.map((ing, i) => {
    const stepOptions = r.steps.map((s, si) => `<option value="${si}" ${((ing.step_index ?? 0) === si) ? 'selected' : ''}>${si + 1}</option>`).join('');
    return `
    <div class="ingredient-row" data-idx="${i}">
      <input class="ing-amount" value="${ing.amount ?? ''}" placeholder="${t('placeholderAmount')}" />
      <input class="ing-unit" value="${escapeHtml(ing.unit ?? '')}" placeholder="${t('placeholderUnit')}" />
      <input class="ing-name" value="${escapeHtml(ing.name)}" placeholder="${t('placeholderIngredient')}" />
      <select class="ing-step" title="${t('fieldStepAssignment')}">${stepOptions || '<option value="0">1</option>'}</select>
    </div>
  `;
  }).join('') || `<p style="color:#8f9689;font-size:0.88rem;">${t('noIngredients')}</p>`;

  const stepsHtml = r.steps.map((s, i) => `
    <div class="step-row" data-idx="${i}">
      <div class="step-num">${i + 1}</div>
      <textarea class="step-instruction">${escapeHtml(s.instruction)}</textarea>
    </div>
  `).join('') || `<p style="color:#8f9689;font-size:0.88rem;">${t('noSteps')}</p>`;

  const tagsHtml = r.tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join('') || `<span style="color:#8f9689;font-size:0.85rem;">${t('noTags')}</span>`;

  const imageChoicesHtml = r.candidate_image_ids.map((iid) => `
    <div class="image-choice ${iid === r.selected_image_id ? 'selected' : ''}" data-image-id="${iid}"
         style="background-image:url('${imageUrl(iid)}')"></div>
  `).join('') + `<div class="image-choice none ${!r.selected_image_id ? 'selected' : ''}" data-image-id="">${t('noImage')}</div>`;

  const duplicateNotice = r.duplicate_match
    ? `<div class="error-banner duplicate-banner" style="margin-bottom:20px;">${escapeHtml(
        tf(r.duplicate_exact ? 'duplicateExactWarning' : 'duplicateSimilarWarning', { name: r.duplicate_match })
      )}</div>`
    : '';

  detail.innerHTML = `
    ${r.import_error ? `<div class="error-banner" style="margin-bottom:20px;">${escapeHtml(r.import_error)}</div>` : ''}
    ${duplicateNotice}
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
    <div id="ingredients-wrap">${ingredientsHtml}</div>

    <div class="section-title">${t('fieldSteps')}</div>
    <div id="steps-wrap">${stepsHtml}</div>
  `;

  // Image selection
  detail.querySelectorAll('.image-choice').forEach((elm) => {
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

    r.ingredients = Array.from(detail.querySelectorAll('.ingredient-row')).map((row) => ({
      amount: numOrNull(row.querySelector('.ing-amount').value),
      unit: row.querySelector('.ing-unit').value || null,
      name: row.querySelector('.ing-name').value,
      note: null,
      group: null,
      step_index: numOrNull(row.querySelector('.ing-step')?.value) ?? 0,
    }));

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

  fileInput.value = '';
  el('upload-error').classList.add('hidden');
  el('review-screen').classList.add('hidden');
  el('action-bar').classList.add('hidden');
  el('processing-screen').classList.add('hidden');
  el('usage-badge').classList.add('hidden');
  el('upload-screen').classList.remove('hidden');
}

// ---------- Init ----------

(async function init() {
  await initI18n();
  checkTandoor();
  setInterval(checkTandoor, 15000);
})();
