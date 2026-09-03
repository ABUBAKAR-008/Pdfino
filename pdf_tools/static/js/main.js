(function () {
  'use strict';

  // ---------------- Theme toggle ----------------
  const themeToggle = document.querySelector('[data-theme-toggle]');
  const root = document.documentElement;
  const savedTheme = window.localStorage ? null : null; // no localStorage per policy; session-only via data attr
  if (themeToggle) {
    themeToggle.addEventListener('click', function () {
      const isDark = root.getAttribute('data-theme') === 'dark';
      root.setAttribute('data-theme', isDark ? 'light' : 'dark');
      const icon = themeToggle.querySelector('i');
      if (icon) icon.className = isDark ? 'fa-solid fa-moon' : 'fa-solid fa-sun';
    });
  }

  // ---------------- Category filter tabs (homepage) ----------------
  document.querySelectorAll('[data-category-tab]').forEach(function (tab) {
    tab.addEventListener('click', function () {
      document.querySelectorAll('[data-category-tab]').forEach((t) => t.classList.remove('active'));
      tab.classList.add('active');
      const cat = tab.getAttribute('data-category-tab');
      document.querySelectorAll('[data-tool-card]').forEach(function (card) {
        card.style.display = (cat === 'all' || card.getAttribute('data-tool-card') === cat) ? '' : 'none';
      });
    });
  });

  // ---------------- Homepage tool search ----------------
  const searchInput = document.querySelector('[data-tool-search]');
  if (searchInput) {
    searchInput.addEventListener('input', function () {
      const q = searchInput.value.trim().toLowerCase();
      document.querySelectorAll('[data-tool-card]').forEach(function (card) {
        const name = (card.getAttribute('data-tool-name') || '').toLowerCase();
        card.style.display = name.includes(q) ? '' : 'none';
      });
    });
  }

  // ---------------- Dropzones ----------------
  function humanSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    const units = ['KB', 'MB', 'GB'];
    let val = bytes;
    for (let i = 0; i < units.length; i++) {
      val /= 1024;
      if (val < 1024 || i === units.length - 1) return val.toFixed(1) + ' ' + units[i];
    }
  }

  function iconFor(name) {
    const ext = (name.split('.').pop() || '').toLowerCase();
    if (ext === 'pdf') return 'fa-file-pdf';
    if (['jpg', 'jpeg', 'png'].includes(ext)) return 'fa-file-image';
    return 'fa-file';
  }

  document.querySelectorAll('[data-dropzone]').forEach(function (zone) {
    const input = zone.querySelector('input[type="file"]');
    const listEl = document.querySelector(zone.getAttribute('data-file-list-target') || '');
    const multiple = input && input.hasAttribute('multiple');
    let files = [];

    function render() {
      if (!listEl) return;
      listEl.innerHTML = '';
      files.forEach(function (file, idx) {
        const item = document.createElement('div');
        item.className = 'pf-file-item';
        item.setAttribute('draggable', multiple ? 'true' : 'false');
        item.dataset.index = idx;
        item.innerHTML =
          (multiple ? '<i class="fa-solid fa-grip-vertical pf-drag-handle"></i>' : '') +
          '<i class="fa-solid ' + iconFor(file.name) + ' pf-file-icon"></i>' +
          '<span class="pf-file-name">' + file.name + '</span>' +
          '<span class="pf-file-size">' + humanSize(file.size) + '</span>' +
          '<button type="button" class="pf-file-remove" aria-label="Remove file" data-remove="' + idx + '">' +
          '<i class="fa-solid fa-xmark"></i></button>';
        listEl.appendChild(item);
      });
      syncInputFiles();
      attachRemoveHandlers();
      if (multiple) attachDragReorder();
    }

    function syncInputFiles() {
      const dt = new DataTransfer();
      files.forEach((f) => dt.items.add(f));
      input.files = dt.files;
    }

    function attachRemoveHandlers() {
      listEl.querySelectorAll('[data-remove]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          const i = parseInt(btn.getAttribute('data-remove'), 10);
          files.splice(i, 1);
          render();
        });
      });
    }

    function attachDragReorder() {
      let dragIdx = null;
      listEl.querySelectorAll('.pf-file-item').forEach(function (item) {
        item.addEventListener('dragstart', function () {
          dragIdx = parseInt(item.dataset.index, 10);
          item.classList.add('dragging');
        });
        item.addEventListener('dragend', function () { item.classList.remove('dragging'); });
        item.addEventListener('dragover', function (e) { e.preventDefault(); });
        item.addEventListener('drop', function (e) {
          e.preventDefault();
          const dropIdx = parseInt(item.dataset.index, 10);
          if (dragIdx === null || dragIdx === dropIdx) return;
          const moved = files.splice(dragIdx, 1)[0];
          files.splice(dropIdx, 0, moved);
          render();
        });
      });
    }

    function addFiles(newFiles) {
      Array.from(newFiles).forEach(function (f) {
        if (!multiple) { files = [f]; return; }
        files.push(f);
      });
      render();
    }

    zone.addEventListener('click', function (e) {
      if (e.target.closest('.pf-file-remove')) return;
      input.click();
    });
    zone.addEventListener('dragover', function (e) { e.preventDefault(); zone.classList.add('dragover'); });
    zone.addEventListener('dragleave', function () { zone.classList.remove('dragover'); });
    zone.addEventListener('drop', function (e) {
      e.preventDefault();
      zone.classList.remove('dragover');
      if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
    });
    input.addEventListener('change', function () {
      if (input.files.length) addFiles(input.files);
    });
  });

  // ---------------- Page range quick-picker ----------------
  document.querySelectorAll('[data-page-picker]').forEach(function (picker) {
    const targetInput = document.querySelector(picker.getAttribute('data-target'));
    const count = parseInt(picker.getAttribute('data-page-count') || '0', 10);
    const grid = picker.querySelector('.pf-page-grid');
    if (!grid || !count) return;
    const selected = new Set();
    for (let i = 1; i <= count; i++) {
      const el = document.createElement('div');
      el.className = 'pf-page-thumb';
      el.textContent = i;
      el.addEventListener('click', function () {
        if (selected.has(i)) { selected.delete(i); el.classList.remove('selected'); }
        else { selected.add(i); el.classList.add('selected'); }
        if (targetInput) targetInput.value = Array.from(selected).sort((a, b) => a - b).join(',');
      });
      grid.appendChild(el);
    }
  });

  // ---------------- Real upload progress via XHR, form replaces itself with response ----------------
  document.querySelectorAll('form[data-ajax-upload]').forEach(function (form) {
    const progressWrap = form.querySelector('.pf-progress-wrap');
    const progressBar = form.querySelector('.pf-progress-bar');
    const progressLabel = form.querySelector('.pf-progress-label');
    const submitBtn = form.querySelector('[type="submit"]');

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      const fileInput = form.querySelector('input[type="file"]');
      if (fileInput && fileInput.files.length === 0) return;

      if (submitBtn) submitBtn.disabled = true;
      if (progressWrap) progressWrap.style.display = 'block';

      const xhr = new XMLHttpRequest();
      xhr.open('POST', form.action || window.location.href, true);
      xhr.responseType = 'text';

      xhr.upload.addEventListener('progress', function (evt) {
        if (!evt.lengthComputable) return;
        const pct = Math.round((evt.loaded / evt.total) * 100);
        if (progressBar) progressBar.style.width = pct + '%';
        if (progressLabel) progressLabel.textContent = pct < 100 ? ('Uploading… ' + pct + '%') : 'Processing your file…';
      });

      xhr.onload = function () {
        const html = xhr.responseText;
        document.open();
        document.write(html);
        document.close();
      };

      xhr.onerror = function () {
        if (submitBtn) submitBtn.disabled = false;
        if (progressLabel) progressLabel.textContent = 'Upload failed. Please check your connection and try again.';
      };

      xhr.send(new FormData(form));
    });
  });
})();
