// MP4MUSEUM web interface
// based on the MP4MUSEUM v7 beta web service by julius schmiedel - http://mp4museum.org
// modified 2026 in https://github.com/lotech/mp4museum; licensed under the GNU GPL v3, see LICENSE

// ----- Tabs ----- //
function showTab(tabId) {
  const content = document.getElementById(tabId);
  if (!content) {
    return;
  }
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(tab => tab.classList.toggle('active', tab.dataset.tab === tabId));
  content.classList.add('active');

  // Remember the active tab for the next page load
  try {
    localStorage.setItem('activeTab', tabId);
  } catch (e) {
    // Storage can be unavailable, e.g. in private browsing
  }
}

function restoreActiveTab() {
  let lastTab = null;
  try {
    lastTab = localStorage.getItem('activeTab');
  } catch (e) {
    // Storage can be unavailable, e.g. in private browsing
  }
  showTab(lastTab || 'media');
}

// ----- Messages ----- //
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', document.body.dataset.iconsUrl + '#' + name);
  svg.appendChild(use);
  return svg;
}

function setIcon(svg, name) {
  const use = svg && svg.querySelector('use');
  if (use) {
    use.setAttribute('href', document.body.dataset.iconsUrl + '#' + name);
  }
}

function showToast(message, category) {
  const toast = document.createElement('div');
  toast.className = 'toast ' + (category || 'success');
  toast.setAttribute('role', 'status');
  const mark = icon(category === 'error' ? 'circle-alert' : 'circle-check');
  mark.classList.add('toast-icon');
  const text = document.createElement('span');
  text.textContent = message;
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'toast-close';
  close.title = 'Close';
  close.setAttribute('aria-label', 'Close');
  close.appendChild(icon('x'));
  toast.append(mark, text, close);
  document.getElementById('toasts').appendChild(toast);
  setUpToast(toast);
}

function setUpToast(toast) {
  const hide = () => {
    toast.classList.add('hiding');
    setTimeout(() => toast.remove(), 300);
  };
  toast.querySelector('.toast-close').addEventListener('click', hide);
  // Errors stay longer, so there is time to read them
  setTimeout(hide, toast.classList.contains('error') ? 12000 : 5000);
}

// ----- Player ----- //
const KIND_ICONS = {video: 'film', image: 'image', audio: 'music', other: 'file'};
let playerView = null;
let playerViewTime = 0;

function formatTime(seconds) {
  seconds = Math.max(0, Math.floor(seconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor(seconds % 3600 / 60);
  const s = String(seconds % 60).padStart(2, '0');
  return h ? h + ':' + String(m).padStart(2, '0') + ':' + s : m + ':' + s;
}

function showPlayer(view) {
  const card = document.getElementById('player');
  if (!card || !view) {
    return;
  }
  playerView = view;
  playerViewTime = Date.now();
  const state = view.running ? (view.state || 'stopped') : 'stopped';
  card.className = card.className.replace(/\bstate-\S+/g, '').trim() + ' state-' + state;

  const active = ['playing', 'paused', 'sync'].includes(state) && view.name;
  document.getElementById('playerTitle').textContent = active ? view.name : view.text;
  document.getElementById('playerState').textContent =
    {playing: 'playing', paused: 'paused', idle: 'idle', sync: 'sync mode'}[state] || 'not running';
  const folder = document.getElementById('playerFolder');
  folder.hidden = !active || !view.folder;
  folder.textContent = view.folder || '';
  document.getElementById('playerLoop').hidden = !active || !view.loop;
  setIcon(card.querySelector('.player-kind'), active ? KIND_ICONS[view.kind] || 'file' : 'square-play');

  const controls = view.running && ['playing', 'paused'].includes(state);
  document.getElementById('pauseButton').disabled = !controls;
  document.getElementById('nextButton').disabled = !controls;
  document.querySelectorAll('.playlist-item').forEach(item => {
    const current = active && item.dataset.path === view.file;
    item.classList.toggle('current', current);
    const button = item.querySelector('.item-play');
    if (button && item.classList.contains('not-played') === false) {
      // An older player script can't jump to a file
      button.disabled = !view.play_file || state === 'sync' || !view.running;
    }
  });
  showProgress();
}

function showProgress() {
  if (!playerView) {
    return;
  }
  const view = playerView;
  const timeText = document.getElementById('playerTime');
  const lengthText = document.getElementById('playerLength');
  const bar = document.getElementById('playerProgress');
  const progress = bar.parentElement;
  const moving = view.state === 'playing';
  const passed = moving ? (Date.now() - playerViewTime) / 1000 : 0;
  if (typeof view.position === 'number') {
    const position = view.length ? Math.min(view.position + passed, view.length) : view.position + passed;
    timeText.textContent = formatTime(position);
    lengthText.textContent = view.length ? formatTime(view.length) : '';
    progress.classList.toggle('indeterminate', !view.length);
    bar.style.width = view.length ? (100 * position / view.length) + '%' : '';
  } else {
    // Loop videos in omxplayer, and older player scripts: how long it has been playing
    timeText.textContent = typeof view.elapsed === 'number' ? formatTime(view.elapsed + passed) : '';
    lengthText.textContent = '';
    progress.classList.toggle('indeterminate', ['playing', 'paused', 'sync'].includes(view.state));
    bar.style.width = '';
  }
}

function refreshPlayer() {
  const card = document.getElementById('player');
  if (!card || document.hidden) {
    return;
  }
  fetch(card.dataset.statusUrl, {headers: {'X-Requested-With': 'fetch'}})
    .then(response => response.ok ? response.json() : null)
    .then(view => showPlayer(view))
    .catch(() => {
      // Try again next time
    });
}

// Pause, Next and the playlist's play buttons, without reloading the page
function sendPlayerForm(form) {
  const buttons = form.querySelectorAll('button');
  buttons.forEach(button => button.disabled = true);
  fetch(form.action, {
    method: 'POST',
    headers: {'X-Requested-With': 'fetch'},
    body: new URLSearchParams(new FormData(form)),
  })
    .then(response => {
      if (response.status === 401) {
        window.location.reload();
        return null;
      }
      return response.json();
    })
    .then(view => {
      if (!view) {
        return;
      }
      if (view.error) {
        showToast(view.error, 'error');
      }
      showPlayer(view);
    })
    .catch(() => showToast("Couldn't reach the player.", 'error'))
    .finally(() => {
      buttons.forEach(button => button.disabled = false);
      showPlayer(playerView);
    });
}

// ----- Media ----- //
function uploadFiles(input) {
  const form = input.form;
  const files = Array.from(input.files);
  if (!files.length) {
    return;
  }
  const total = files.reduce((sum, file) => sum + file.size, 0);
  if (total > Number(form.dataset.freeSpace)) {
    alert('Not enough free space: ' + Math.ceil(total / 1048576) + ' MB selected but only ' +
          form.dataset.freeSpaceText + ' is free.');
    input.value = '';
    return;
  }
  document.getElementById('uploadProgress').hidden = false;
  // One at a time; then reload the page to show the new files and the messages
  const next = index => {
    if (index >= files.length) {
      window.location.reload();
      return;
    }
    uploadFile(form.action, files[index], index, files.length)
      .catch(error => showToast(error, 'error'))
      .then(() => next(index + 1));
  };
  next(0);
}

function uploadFile(url, file, index, count) {
  const name = document.getElementById('uploadName');
  const percent = document.getElementById('uploadPercent');
  const bar = document.getElementById('uploadBar');
  name.textContent = (count > 1 ? (index + 1) + ' of ' + count + ': ' : '') + file.name;
  percent.textContent = '0%';
  bar.style.width = '0';
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', url);
    request.setRequestHeader('X-Requested-With', 'fetch');
    request.upload.addEventListener('progress', event => {
      if (event.lengthComputable) {
        const done = Math.round(100 * event.loaded / event.total);
        percent.textContent = done + '%';
        bar.style.width = done + '%';
      }
    });
    request.addEventListener('load', () => {
      if (request.status === 401) {
        reject('You have been logged out. Please log in again.');
        return;
      }
      let answer = {};
      try {
        answer = JSON.parse(request.responseText);
      } catch (e) {
        // Not JSON: the page shows what went wrong after the reload
      }
      if (request.status === 200 && answer.ok) {
        resolve();
      } else {
        reject(answer.error || 'Uploading ' + file.name + ' failed.');
      }
    });
    request.addEventListener('error', () => reject('Uploading ' + file.name + " failed: the player couldn't be reached."));
    const data = new FormData();
    data.append('file', file);
    request.send(data);
  });
}

// ----- Software update ----- //
// Checks quietly when the page is opened; the server asks GitHub at most every few hours
function checkForUpdate() {
  const bar = document.getElementById('updateBar');
  if (!bar || !bar.hidden) {
    return;
  }
  try {
    if (sessionStorage.getItem('updateBarHidden')) {
      return;
    }
  } catch (e) {
    // Storage can be unavailable, e.g. in private browsing
  }
  fetch(bar.dataset.checkUrl, {
    method: 'POST',
    headers: {'X-Requested-With': 'fetch'},
    body: new URLSearchParams({auto: '1'}),
  })
    .then(response => response.ok ? response.json() : null)
    .then(answer => {
      if (!answer || !answer.update) {
        return;
      }
      const text = document.getElementById('updateText');
      text.textContent = 'Update available: ';
      const commit = document.createElement('strong');
      commit.textContent = answer.update.commit;
      text.append(commit, ' (' + answer.update.date + ') ' + (answer.update.message || ''));
      bar.hidden = false;
    })
    .catch(() => {
      // No internet: nothing to show
    });
}

function hideUpdateBar() {
  document.getElementById('updateBar').hidden = true;
  try {
    sessionStorage.setItem('updateBarHidden', '1');
  } catch (e) {
    // Storage can be unavailable, e.g. in private browsing
  }
}

// ----- Reboot ----- //
function reboot(askFirst) {
  if (askFirst && !confirm('Are you sure you want to reboot the system?')) {
    return;
  }
  document.getElementById('rebootScreen').classList.add('active');

  // Start checking for when the player is back
  checkServerAndRedirect();

  fetch(document.body.dataset.rebootUrl, {method: 'POST', headers: {'X-Requested-With': 'fetch'}})
    .then(response => {
      if (response.status === 401) {
        alert('You have been logged out. Please log in again.');
        window.location = '/';
      }
    })
    .catch(() => {
      // Expected: the connection drops while the player reboots
    });
}

function checkServerAndRedirect() {
  const serverUrl = window.location.origin || window.location.protocol + '//' + window.location.host;
  // Give the player time to go down before checking
  setTimeout(() => tryReconnect(serverUrl), 10000);
}

function tryReconnect(serverUrl) {
  fetch(serverUrl, {signal: AbortSignal.timeout(5000)})
    .then(response => {
      if (response.ok) {
        window.location.href = '/';
      } else {
        setTimeout(() => tryReconnect(serverUrl), 2000);
      }
    })
    .catch(() => {
      setTimeout(() => tryReconnect(serverUrl), 2000);
    });
}

// ----- Sound ----- //
function validateSoundDevice() {
  const value = document.getElementById('device').value.trim();
  const errorDiv = document.getElementById('deviceError');

  if (value !== 'auto' && !/^[0-9]{1,2}$/.test(value)) {
    errorDiv.textContent = 'Please enter a number between 0 and 99';
    errorDiv.style.display = 'block';
    return false;
  }
  errorDiv.style.display = 'none';
  return true;
}

// ----- Player script ----- //
function loadScript() {
  if (confirm('Discard unsaved changes and reload from disk?')) {
    window.location.reload();
  }
}

function saveAndReboot() {
  if (!confirm('Save the script and reboot the system?')) {
    return;
  }
  const form = document.getElementById('scriptForm');
  fetch(form.action, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-Requested-With': 'fetch',
    },
    body: 'script_content=' + encodeURIComponent(document.getElementById('script_content').value)
  })
    .then(response => {
      if (response.ok) {
        reboot(false);
      } else {
        response.text().then(message => alert('Failed to save script: ' + message));
      }
    })
    .catch(error => alert('Error saving script: ' + error));
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.toast').forEach(setUpToast);
  if (document.querySelector('.tabs')) {
    restoreActiveTab();
  }
  document.querySelectorAll('form.js-player').forEach(form => {
    form.addEventListener('submit', event => {
      event.preventDefault();
      sendPlayerForm(form);
    });
  });
  const data = document.getElementById('playerData');
  if (data) {
    showPlayer(JSON.parse(data.textContent));
    setInterval(refreshPlayer, 3000);
    setInterval(showProgress, 250);
    document.addEventListener('visibilitychange', refreshPlayer);
  }
  checkForUpdate();
});
