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

// ----- Media ----- //
function checkUploadSize(form) {
  const file = form.querySelector('input[type="file"]').files[0];
  const freeSpace = Number(form.dataset.freeSpace);
  if (file && file.size > freeSpace) {
    alert('Not enough free space: the file is ' + Math.ceil(file.size / 1048576) + ' MB but only ' +
          form.dataset.freeSpaceText + ' is free.');
    return false;
  }
  return true;
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

  // The player reads a single digit
  if (value !== 'auto' && !/^[0-9]$/.test(value)) {
    errorDiv.textContent = 'Please enter a number between 0 and 9';
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
  if (document.querySelector('.tabs')) {
    restoreActiveTab();
  }
});
