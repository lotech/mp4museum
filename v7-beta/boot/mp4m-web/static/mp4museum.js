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
  fitPlaylist();

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
  const href = document.body.dataset.iconsUrl + '#' + name;
  // Only when it changes: setting it again redraws the icon, which flickers
  if (use && use.getAttribute('href') !== href) {
    use.setAttribute('href', href);
  }
}

function showToast(message, category) {
  const toast = document.createElement('div');
  toast.className = 'toast ' + (category || 'success');
  toast.setAttribute('role', 'status');
  const mark = icon({error: 'circle-alert', warning: 'triangle-alert'}[category] || 'circle-check');
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
  setTimeout(hide, toast.classList.contains('success') ? 5000 : 12000);
}

// ----- Player ----- //
const KIND_ICONS = {video: 'film', image: 'image', audio: 'music', other: 'file'};
let playerView = null;
let playerViewTime = 0;
// While a button press is on its way, the player's buttons stay disabled
let playerBusy = false;

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
  const engine = document.getElementById('playerEngine');
  engine.hidden = !active || !view.engine;
  engine.querySelector('span').textContent = {vlc: 'VLC'}[view.engine] || view.engine || '';
  setIcon(card.querySelector('.player-kind'), active ? KIND_ICONS[view.kind] || 'file' : 'square-play');

  const controls = view.running && ['playing', 'paused'].includes(state) && !playerBusy;
  document.getElementById('pauseButton').disabled = !controls;
  document.getElementById('nextButton').disabled = !controls;
  // an older player script can't go back to the previous file or the start. (A page from
  // before an update may not have these buttons.)
  const previous = document.getElementById('previousButton');
  if (previous) previous.disabled = !controls || !view.previous;
  const rewind = document.getElementById('rewindButton');
  if (rewind) rewind.disabled = !controls || !view.rewind;
  document.querySelectorAll('.playlist-item').forEach(item => {
    const current = active && item.dataset.path === view.file;
    if (current && !item.classList.contains('current')) {
      showInPlaylist(item);
    }
    item.classList.toggle('current', current);
    if (!item.classList.contains('not-played')) {
      // An older player script can't jump to a file. When the player isn't running the buttons
      // stay on, so pressing one says so.
      const off = (view.running && !view.play_file) || state === 'sync' || playerBusy;
      item.querySelectorAll('.js-play').forEach(button => button.disabled = off);
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
    .then(response => {
      if (response.status === 401) {
        // Logged out (e.g. the password was changed): back to the login page
        window.location.reload();
        return null;
      }
      return response.ok ? response.json() : null;
    })
    .then(view => showPlayer(view))
    .catch(() => {
      // Try again next time
    });
}

// Pause, Next and the playlist's play buttons, without reloading the page
function sendPlayerForm(form) {
  if (playerBusy) {
    return;
  }
  playerBusy = true;
  showPlayer(playerView);
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
      if (!(response.headers.get('Content-Type') || '').includes('json')) {
        showToast('Something went wrong (error ' + response.status + '). Try reloading the page.', 'error');
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
      playerBusy = false;
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
  const messages = [];
  // One at a time; then reload the page to show the new files, and the messages after it
  const next = index => {
    if (index >= files.length) {
      let kept = false;
      try {
        sessionStorage.setItem('toasts', JSON.stringify(messages));
        kept = true;
      } catch (e) {
        // Storage can be unavailable, e.g. in private browsing: show them before reloading
        messages.forEach(([category, message]) => showToast(message, category));
      }
      setTimeout(() => window.location.reload(), kept ? 0 : 5000);
      return;
    }
    uploadFile(form.action, files[index], index, files.length)
      // the new files show in the list: only errors and warnings are worth a message
      .then(answer => messages.push(...answer.filter(([category]) => category !== 'success')))
      .catch(error => messages.push(['error', error]))
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
      if (Array.isArray(answer.messages)) {
        resolve(answer.messages);
      } else {
        reject('Uploading ' + file.name + ' failed (error ' + request.status + ').');
      }
    });
    request.addEventListener('error', () => reject('Uploading ' + file.name + " failed: the player couldn't be reached."));
    const data = new FormData();
    data.append('file', file);
    request.send(data);
  });
}

function askNewName(form) {
  const name = prompt('New name for ' + form.filename.value + '\n\nFiles play in alphabetical order. ' +
                      'Put "-loop" before the extension (e.g. intro-loop.mp4) to repeat a video until Next.',
                      form.filename.value);
  if (name === null || !name.trim() || name.trim() === form.filename.value) {
    return false;
  }
  form.new_name.value = name.trim();
  return true;
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
      if (answer.update.note) {
        // e.g. from another branch than the installed version
        const note = document.createElement('span');
        note.className = 'update-note';
        note.textContent = ' ' + answer.update.note;
        text.append(note);
      }
      bar.hidden = false;
      fitPlaylist();
    })
    .catch(() => {
      // No internet: nothing to show
    });
}

function hideUpdateBar() {
  document.getElementById('updateBar').hidden = true;
  fitPlaylist();
  try {
    sessionStorage.setItem('updateBarHidden', '1');
  } catch (e) {
    // Storage can be unavailable, e.g. in private browsing
  }
}

// ----- Network ----- //
function showAddressFields(select) {
  select.form.querySelector('.address-fields').hidden = select.value !== 'static';
}

function scanWifi() {
  const card = document.getElementById('wifi');
  const button = document.getElementById('wifiScan');
  const result = document.getElementById('wifiScanResult');
  button.disabled = true;
  result.hidden = false;
  result.textContent = 'Looking for networks…';
  fetch(card.dataset.scanUrl, {headers: {'X-Requested-With': 'fetch'}, cache: 'no-store'})
    .then(response => response.json().catch(() => ({networks: [], error: "The player didn't answer."})))
    .then(answer => {
      const list = document.getElementById('wifiNetworks');
      list.replaceChildren(...answer.networks.map(network => {
        const option = document.createElement('option');
        option.value = network.ssid;
        option.label = network.ssid + (network.signal !== null ? ' (' + Math.round(network.signal) + ' dBm)' : '') +
                       (network.secure ? '' : ', no password');
        return option;
      }));
      result.textContent = answer.error ? answer.error :
        answer.networks.length ? answer.networks.length + ' found: click the name field to choose one.' : 'No networks found.';
      document.getElementById('wifi_ssid').focus();
    })
    .catch(() => { result.textContent = "Couldn't look for networks."; })
    .finally(() => { button.disabled = false; });
}

// How long a network change has left to be kept
function countDownNetworkChange() {
  const bar = document.getElementById('networkBar');
  if (!bar) {
    return;
  }
  const end = Date.now() + Number(bar.dataset.seconds) * 1000;
  const label = document.getElementById('networkSeconds');
  const timer = setInterval(() => {
    const left = Math.max(0, Math.round((end - Date.now()) / 1000));
    label.textContent = Math.floor(left / 60) + ':' + String(left % 60).padStart(2, '0');
    if (left === 0) {
      clearInterval(timer);
      // the previous settings are back: this address may be gone again
      setTimeout(() => window.location.reload(), 5000);
    }
  }, 1000);
}

// ----- Reboot ----- //
function reboot(askFirst) {
  if (askFirst && !confirm('Are you sure you want to reboot the system?')) {
    return;
  }
  rebootFailed = false;
  document.getElementById('rebootScreen').classList.add('active');

  // Start checking for when the player is back
  checkServerAndRedirect();

  fetch(document.body.dataset.rebootUrl, {method: 'POST', headers: {'X-Requested-With': 'fetch'}})
    .then(response => {
      if (response.status === 401) {
        alert('You have been logged out. Please log in again.');
        window.location = '/';
      } else if (!response.ok) {
        // the reboot command failed: the player is still running
        rebootFailed = true;
        document.getElementById('rebootScreen').classList.remove('active');
        response.text().then(text => showToast(text || "The player couldn't reboot.", 'error'));
      }
    })
    .catch(() => {
      // Expected: the connection drops while the player reboots
    });
}

let rebootFailed = false;

function checkServerAndRedirect() {
  const serverUrl = window.location.origin || window.location.protocol + '//' + window.location.host;
  // Give the player time to go down before checking
  setTimeout(() => tryReconnect(serverUrl), 10000);
}

function tryReconnect(serverUrl) {
  if (rebootFailed) {
    return;
  }
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

function showStoredToasts() {
  try {
    const stored = JSON.parse(sessionStorage.getItem('toasts') || '[]');
    sessionStorage.removeItem('toasts');
    stored.forEach(([category, message]) => showToast(message, category));
  } catch (e) {
    // Nothing stored, or storage unavailable
  }
}

// The playlist fills the window down to its bottom edge, and scrolls inside its card when its
// files don't fit
function fitPlaylist() {
  const list = document.getElementById('playlist');
  if (!list || !list.offsetParent) {
    // (on a tab that isn't shown)
    return;
  }
  const box = list.getBoundingClientRect();
  const card = list.closest('.card');
  const below = card ? card.getBoundingClientRect().bottom - box.bottom : 0;
  let height = window.innerHeight - (box.top + window.scrollY) - below - 16;
  if (height < 320) {
    // below the player (a phone): most of the window, once scrolled down to it
    height = window.innerHeight * 0.8;
  }
  list.style.maxHeight = Math.round(height) + 'px';
}

// A long playlist scrolls inside its card: keep the file playing in view when it changes
// (the list only, not the page)
function showInPlaylist(item) {
  const list = item.parentElement;
  const box = list.getBoundingClientRect();
  const row = item.getBoundingClientRect();
  if (row.top < box.top) {
    list.scrollTop -= box.top - row.top;
  } else if (row.bottom > box.bottom) {
    list.scrollTop += row.bottom - box.bottom;
  }
}

// ----- Copying a file from a USB stick to this player ----- //
function followCopies() {
  // the page again when they're done, which says how they went
  const playlist = document.getElementById('playlist');
  fetch(playlist.dataset.copyStatusUrl, {headers: {'X-Requested-With': 'fetch'}, cache: 'no-store'})
    .then(response => response.ok ? response.json() : null)
    .then(status => {
      if (status && status.copying.length === 0) {
        location.reload();
      } else if (status) {
        setTimeout(followCopies, 2000);
      }
    })
    .catch(() => setTimeout(followCopies, 5000));
}

// ----- Copying this player to an SD card ----- //
function confirmClone(form) {
  const card = form.device.selectedOptions[0].dataset.name;
  return confirm('Erase everything on the ' + card + ' card and copy this player to it?');
}

function showCards(cards, running) {
  // the cards in USB readers now: one taken out goes, the next one put in comes. (While one is
  // being made it isn't listed: it's in use.)
  const select = document.getElementById('clone_device');
  if (!select || !cards || running) {
    return;
  }
  const values = cards.map(card => card.value);
  const shown = Array.from(select.options).slice(1).map(option => option.value);
  if (values.join('\n') !== shown.join('\n')) {
    const chosen = select.value;
    while (select.options.length > 1) {
      select.remove(1);
    }
    cards.forEach(card => {
      const option = new Option(card.label, card.value);
      option.dataset.name = card.name;
      select.add(option);
    });
    select.value = values.includes(chosen) ? chosen : '';
  }
  document.getElementById('cloneNoCard').hidden = cards.length > 0;
  return cards.length > 0;
}

function showClone(state) {
  const running = state.running;
  document.getElementById('cloneProgress').hidden = !running;
  const anyCard = showCards(state.cards, running);
  const form = document.getElementById('cloneForm');
  if (form) form.hidden = running || anyCard === false;
  document.getElementById('cloneStep').textContent = state.step + '…';
  const bar = document.getElementById('cloneBar');
  bar.parentElement.classList.toggle('indeterminate', state.percent === null);
  bar.style.width = state.percent === null ? '' : state.percent + '%';
  const result = document.getElementById('cloneResult');
  // (said for a while after it finished)
  result.hidden = running || !(state.recent && (state.done || state.error));
  result.classList.toggle('done', !!state.done);
  result.classList.toggle('failed', !state.done);
  if (state.done) {
    document.getElementById('cloneResultTitle').textContent = 'Done: the card is ready.';
    document.getElementById('cloneResultText').textContent =
      'Take it out and put it in another Pi. To make another, put the next card in and choose it below.' +
      (state.same_id_before ? ' Reboot this player once too: the card had the same partition IDs as this one before.' : '');
  } else if (state.error) {
    document.getElementById('cloneResultTitle').textContent = "The card couldn't be made.";
    document.getElementById('cloneResultText').textContent = state.error;
  }
  return running;
}

function followClone() {
  // often while a card is made; otherwise every few seconds while the System tab is shown, for
  // cards put in and taken out
  const card = document.getElementById('clone');
  if (!card.offsetParent || document.hidden) {
    setTimeout(followClone, 3000);
    return;
  }
  fetch(card.dataset.statusUrl, {headers: {'X-Requested-With': 'fetch'}, cache: 'no-store'})
    .then(response => {
      if (response.status === 401) {
        // logged out: the login page
        location.reload();
        return null;
      }
      return response.ok ? response.json() : undefined;
    })
    .then(state => {
      if (state === undefined) {
        setTimeout(followClone, 5000);
      } else if (state) {
        const running = showClone(state);
        // (no card list when copying isn't available: nothing to follow then)
        if (running || document.getElementById('clone_device')) {
          setTimeout(followClone, running ? 1500 : 3000);
        }
      }
    })
    .catch(() => setTimeout(followClone, 5000));
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.toast').forEach(setUpToast);
  showStoredToasts();
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
  fitPlaylist();
  window.addEventListener('resize', fitPlaylist);
  if (document.getElementById('clone')) {
    followClone();
  }
  const playlist = document.getElementById('playlist');
  if (playlist && playlist.hasAttribute('data-copying')) {
    setTimeout(followCopies, 2000);
  }
  checkForUpdate();
  countDownNetworkChange();
});
