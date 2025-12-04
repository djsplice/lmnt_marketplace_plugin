// Extracted from lmnt_marketplace_plugin.py
(function(){
  const $ = (id) => document.getElementById(id);
  const startBtn = $('startBtn');
  const pairingCard = $('pairingCard');
  const drawerToggle = $('pairingDrawerToggle');
  const drawerContent = $('pairingDrawerContent');
  const pairingDisplay = $('pairingDisplay');
  const pairingInfoSection = $('pairInfoSection');
  let sessionId = null;
  let pollTimer = null;
  let statusTimer = null;
  let userOverrideDrawer = false;
  let lastKnownRegistered = null;

  // Fireworks configuration (ms)
  const FIREWORK_SHOW_DURATION = 10000;
  const FIREWORK_BURST_INTERVAL = 250;
  const FIREWORK_PARTICLE_LIFETIME = 3000;
  const FIREWORK_PARTICLES_PER_BURST = 50;

  function setDrawerState(open, { auto = false } = {}) {
    if (!pairingCard || !drawerToggle || !drawerContent) return;
    pairingCard.dataset.open = String(open);
    drawerToggle.setAttribute('aria-expanded', String(open));
    if (open) {
      drawerContent.classList.add('open');
      requestAnimationFrame(() => {
        drawerContent.style.maxHeight = drawerContent.scrollHeight + 'px';
      });
      if (pairingInfoSection) pairingInfoSection.style.display = '';
    } else {
      drawerContent.classList.remove('open');
      drawerContent.style.maxHeight = '0px';
      if (pairingInfoSection) pairingInfoSection.style.display = 'none';
      if (pairingDisplay) pairingDisplay.style.display = 'none';
      const pairInfo = $('pairInfo');
      if (pairInfo) pairInfo.innerHTML = '';
      const pairJson = $('pairJson');
      if (pairJson) {
        pairJson.style.display = 'none';
        pairJson.textContent = '';
      }
    }
    if (!auto) {
      userOverrideDrawer = true;
    }
  }

  function autoSetDrawerState(open) {
    userOverrideDrawer = false;
    setDrawerState(open, { auto: true });
  }

  function toggleDrawer() {
    const isOpen = pairingCard?.dataset.open !== 'false';
    setDrawerState(!isOpen);
  }

  function ensureDrawerHeight() {
    if (drawerContent?.classList.contains('open')) {
      requestAnimationFrame(() => {
        drawerContent.style.maxHeight = drawerContent.scrollHeight + 'px';
      });
    }
  }

  // Access config from the window object, injected by the HTML
  const LMNT_CONFIG = window.LMNT_CONFIG || {};

  async function fetchJSON(path, opts={}){
    const res = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts));
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return await res.json();
  }

  async function postJSON(path, body){
    const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body||{}) });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return await res.json();
  }

  function renderStatus(s){
    try {
      const auth = s && s.auth ? s.auth : {};
      const registered = !!auth.authenticated;
      const printerId = auth.printer_id || '—';
      const printerName = auth.printer_name || '';
      const expiry = auth.token_expiry || null;
      let humanExpiry = null;
      let timeRemaining = null;
      if (expiry) {
        try {
          const expMs = Date.parse(expiry);
          if (!isNaN(expMs)) {
            const diffMs = expMs - Date.now();
            if (diffMs > 0) {
              const mins = Math.floor(diffMs / 60000);
              const hrs = Math.floor(mins / 60);
              const remMins = mins % 60;
              timeRemaining = (hrs > 0 ? (hrs + 'h ') : '') + remMins + 'm';
            } else {
              timeRemaining = 'Expired';
            }
            const d = new Date(expiry);
            if (!isNaN(d)) {
              humanExpiry = d.toLocaleString('en-US', { timeZone: 'UTC', month: 'short', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }) + ' UTC';
            }
          }
        } catch (_) {}
      }

      const summaryClass = registered ? 'status-summary' : 'status-summary status-warning';
      const statusLabel = registered ? 'Connection' : 'Connection';
      const summaryValue = registered ? 'Registered' : 'Awaiting Pairing';
      const summarySubtle = registered
        ? (printerName ? 'Authorized as ' + printerName : 'Secure pairing active.')
        : 'Press “Start Pairing” to connect this printer to LMNT.';
      const iconSuccess = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 12.5l2.2 2.2L19 7"></path><circle cx="12" cy="12" r="9"></circle></svg>';
      const iconWarning = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8v5"></path><path d="M12 17h.01"></path><path d="M10.3 3.86 2.38 18a2 2 0 0 0 1.74 3h15.76a2 2 0 0 0 1.74-3l-7.92-14.14a2 2 0 0 0-3.4 0z"></path></svg>';
      const iconPrinter = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9V4h12v5"></path><path d="M6 18h12v2H6z"></path><rect x="4" y="9" width="16" height="8" rx="2"></rect><path d="M8 13h8"></path></svg>';
      const iconClock = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 3"></path></svg>';
      const iconShield = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 5 5v6c0 5.55 3.84 10.74 7 11 3.16-.26 7-5.45 7-11V5l-7-2z"></path><path d="m9 12 2 2 4-4"></path></svg>';
      const statusIcon = registered ? iconSuccess : iconWarning;
      const configuredMarketUrl = LMNT_CONFIG.marketUrl || '';

      if (registered) {
        const pairJson = $('pairJson');
        if (pairJson) {
          pairJson.style.display = 'none';
          pairJson.textContent = '';
        }
      }

      if (lastKnownRegistered === null) {
        lastKnownRegistered = registered;
        autoSetDrawerState(!registered);
      } else if (registered !== lastKnownRegistered) {
        autoSetDrawerState(!registered);
        lastKnownRegistered = registered;
      }

      const tiles = [];
      const printerSubtitle = printerName
        ? 'Named ' + printerName
        : (registered ? 'Pairing complete.' : 'Pairing assigns a permanent printer ID.');
      tiles.push(`
        <div class="status-tile">
          <div class="status-tile-icon">${iconPrinter}</div>
          <div>
            <div class="status-tile-label">Printer</div>
            <div class="status-tile-value">${printerId && printerId !== '—' ? printerId : 'Not yet assigned'}</div>
            ${printerSubtitle ? `<div class="status-tile-subtle">${printerSubtitle}</div>` : ''}
          </div>
        </div>
      `);

      tiles.push(`
        <div class="status-tile">
          <div class="status-tile-icon">${iconClock}</div>
          <div>
            <div class="status-tile-label">Access Token</div>
            <div class="status-tile-value">${humanExpiry || (registered ? 'Active' : 'Not issued')}</div>
            ${timeRemaining ? `<div class="status-tile-subtle">Renews in ${timeRemaining}</div>` : (expiry ? `<div class="status-tile-subtle">Expires at ${expiry}</div>` : `<div class="status-tile-subtle">${registered ? 'Automatically refreshed' : 'Issued after approval'}</div>`)}
          </div>
        </div>
      `);

      if (configuredMarketUrl) {
        tiles.push(`
          <div class="status-tile">
            <div class="status-tile-icon">${iconShield}</div>
            <div>
              <div class="status-tile-label">Marketplace Host</div>
              <div class="status-tile-value">${configuredMarketUrl}</div>
              <div class="status-tile-subtle">All pairing requests use encrypted TLS.</div>
            </div>
          </div>
        `);
      }

      const statusMarkup = `
        <div class="status-card-body">
          <div class="${summaryClass} ${registered ? 'status-registered' : ''}">
            <div class="status-icon">${statusIcon}</div>
            <div>
              <div class="status-summary-label">${statusLabel}</div>
              <div class="status-summary-value">${summaryValue}</div>
              <div class="status-summary-subtle">${summarySubtle}</div>
            </div>
          </div>
          ${tiles.length ? `<div class="status-grid">${tiles.join('')}</div>` : ''}
        </div>
      `;

      const statusEl = $('status');
      if (statusEl) statusEl.innerHTML = statusMarkup;

      const footer = $('pluginVersion');
      if (footer) {
        footer.textContent = s && s.version ? `LMNT Marketplace Plugin • v${s.version}` : '';
      }
    } catch (e) {
      const statusEl = $('status');
      if (statusEl) {
        statusEl.innerHTML = `
          <div class="status-card-body">
            <div class="status-summary status-warning">
              <div class="status-icon">${'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8v5"></path><path d="M12 17h.01"></path><path d="M10.3 3.86 2.38 18a2 2 0 0 0 1.74 3h15.76a2 2 0 0 0 1.74-3l-7.92-14.14a2 2 0 0 0-3.4 0z"></path></svg>'}</div>
              <div>
                <div class="status-summary-label">Status</div>
                <div class="status-summary-value">Unavailable</div>
                <div class="status-summary-subtle">${e && e.message ? e.message : 'Unable to parse status response.'}</div>
              </div>
            </div>
          </div>
        `;
      }
      const footer = $('pluginVersion');
      if (footer) footer.textContent = '';
    }
  }

  async function loadStatus(){
    try {
      const s = await fetchJSON('/machine/lmnt_marketplace/status');
      const payload = (s && s.result) ? s.result : s;
      renderStatus(payload);
    } catch (e) {
      const statusEl = $('status');
      if (statusEl) {
        statusEl.innerHTML = `
          <div class="status-card-body">
            <div class="status-summary status-warning">
              <div class="status-icon">${'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8v5"></path><path d="M12 17h.01"></path><path d="M10.3 3.86 2.38 18a2 2 0 0 0 1.74 3h15.76a2 2 0 0 0 1.74-3l-7.92-14.14a2 2 0 0 0-3.4 0z"></path></svg>'}</div>
              <div>
                <div class="status-summary-label">Status</div>
                <div class="status-summary-value">Unavailable</div>
                <div class="status-summary-subtle">${e && e.message ? e.message : 'Unable to reach LMNT Marketplace.'}</div>
              </div>
            </div>
          </div>
        `;
      }
      const footer = $('pluginVersion');
      if (footer) footer.textContent = '';
    }
  }

  function showWaitingWithCode(code){
    if (startBtn) startBtn.disabled = true;
    const section = $('pairInfoSection');
    const display = $('pairingDisplay');
    const codeValue = $('pairCodeValue');
    if (section && code) {
      section.style.display = 'block';
      if (display) display.style.display = 'block';
      if (codeValue) {
        codeValue.textContent = code;
      }
      // Wire up copy button
      const copyBtn = $('copyCodeBtn');
      if (copyBtn && navigator.clipboard && navigator.clipboard.writeText) {
        copyBtn.style.display = 'block';
        copyBtn.onclick = async () => {
          try {
            await navigator.clipboard.writeText(code);
            const prev = copyBtn.textContent;
            copyBtn.textContent = 'Copied!';
            setTimeout(() => { copyBtn.textContent = prev; }, 2000);
          } catch(e) {
            copyBtn.textContent = 'Failed';
            setTimeout(() => { copyBtn.textContent = 'Copy Code'; }, 2000);
          }
        };
      } else if (copyBtn) {
        copyBtn.style.display = 'none';
      }
      // Update marketplace URL in instructions from config
      const marketUrl = LMNT_CONFIG.marketUrl || 'https://marketplace.local';
      const pairingUrl = $('pairingUrl');
      if (pairingUrl) {
        pairingUrl.textContent = marketUrl + '/profile';
      }
    } else if (section) {
      section.style.display = 'block';
      const pi = $('pairInfo');
      if (pi) pi.innerHTML = '<div class="waiting-indicator"><div class="waiting-spinner"></div> Waiting for approval…</div>';
    }
  }

  function setLoading(loading){
    if (!startBtn) return;
    if (loading) {
      startBtn.disabled = true;
      startBtn.classList.add('loading');
      startBtn.dataset.label = startBtn.textContent;
      startBtn.innerHTML = '<span class="spinner"></span> Processing…';
    } else {
      startBtn.classList.remove('loading');
      startBtn.innerHTML = startBtn.dataset.label || 'Start Pairing';
      startBtn.disabled = false;
    }
  }

  async function checkStatusAndMaybeComplete(){
    try {
      const st = await postJSON('/machine/lmnt_marketplace/pair/status', { session_id: sessionId });
      const status = (st && (st.status || (st.result && st.result.status))) || 'unknown';
      if (status === 'approved' || status === 'ready' || status === 'authorized') {
        clearInterval(pollTimer);
        await complete();
      }
    } catch(e){ /* ignore transient errors */ }
  }

  function launchFireworks() {
    const container = $('fireworks');
    if (!container) return;
    const colors = ['#7ee4a4', '#baf2d3', '#4ADE80', '#a9ecca', '#DFF2EF', '#34D399', '#10B981'];
    const bursts = Math.floor(FIREWORK_SHOW_DURATION / FIREWORK_BURST_INTERVAL); // 100 bursts over 30 seconds
    
    console.log(`🎆 Launching ${bursts} bursts over ${FIREWORK_SHOW_DURATION / 1000} seconds`);

    for (let b = 0; b < bursts; b++) {
      setTimeout(() => {
        // Random position for each burst
        const centerX = Math.random() * window.innerWidth;
        const centerY = Math.random() * (window.innerHeight * 0.6);
        const particles = FIREWORK_PARTICLES_PER_BURST; // Particles per burst

        for (let i = 0; i < particles; i++) {
          const particle = document.createElement('div');
          particle.className = 'firework';
          const angle = (Math.PI * 2 * i) / particles;
          const velocity = 80 + Math.random() * 120; // Good spread
          const x = Math.cos(angle) * velocity;
          const y = Math.sin(angle) * velocity;
          particle.style.left = centerX + 'px';
          particle.style.top = centerY + 'px';
          particle.style.background = colors[Math.floor(Math.random() * colors.length)];
          particle.style.setProperty('--x', x + 'px');
          particle.style.setProperty('--y', y + 'px');
          // Override animation duration for each particle
          particle.style.animationDuration = FIREWORK_PARTICLE_LIFETIME + 'ms';
          particle.style.setProperty('--firework-duration', FIREWORK_PARTICLE_LIFETIME + 'ms');
          container.appendChild(particle);
          // Remove particle after animation completes
          setTimeout(() => particle.remove(), FIREWORK_PARTICLE_LIFETIME);
        }
      }, b * FIREWORK_BURST_INTERVAL);
    }

    // Clean up container after show is complete
    setTimeout(() => {
      if (container) container.innerHTML = '';
      console.log('🎆 Fireworks show complete!');
    }, FIREWORK_SHOW_DURATION + FIREWORK_PARTICLE_LIFETIME);
  }

  async function complete(){
    try {
      const done = await postJSON('/machine/lmnt_marketplace/pair/complete', { session_id: sessionId });
      const display = $('pairingDisplay');
      if (display) display.style.display = 'none';
      const pi = $('pairInfo');
      if (pi) {
        pi.innerHTML = '<div class="pairing-display success-celebration"><div class="pairing-title" style="color: var(--success);">✓ Pairing Successful!</div><div class="pairing-instructions">Your printer has been successfully registered with the LMNT Marketplace.</div></div>';
      }
      setLoading(false);
      launchFireworks();
      const pairJson = $('pairJson');
      if (pairJson) {
        pairJson.style.display = 'none';
        pairJson.textContent = '';
      }
      setTimeout(() => {
        autoSetDrawerState(false);
        if (pairingInfoSection) pairingInfoSection.style.display = 'none';
      }, FIREWORK_SHOW_DURATION + FIREWORK_PARTICLE_LIFETIME + 200);
      // Optimistically update the Status card immediately using response
      try {
        const optimistic = { auth: {
          authenticated: true,
          printer_id: done && (done.printer_id || (done.result && done.result.printer_id)) || null,
          token_expiry: done && (done.expiry || (done.result && done.result.expiry)) || null,
        }};
        renderStatus(optimistic);
      } catch(_) {}
      // Also pull fresh status from backend
      try { loadStatus(); } catch(_) {}
    } catch(e){
      const pi = $('pairInfo');
      if (pi) pi.innerHTML = 'Complete failed: ' + e.message;
      setLoading(false);
    }
  }

  async function startFlow(){
    try {
      const body = {
        printer_name: $('printerName')?.value || 'Printer',
        manufacturer: $('manufacturer')?.value || 'LMNT',
        model: $('model')?.value || null
      };
      const res = await postJSON('/machine/lmnt_marketplace/pair/start', body);
      sessionId = (res && (res.session_id || (res.result && res.result.session_id))) || null;
      const code = (res && (res.pairing_code || (res.result && res.result.pairing_code))) || null;
      const pj = $('pairJson');
      if (pj) { pj.textContent = JSON.stringify(res, null, 2); pj.style.display = 'block'; }
      if (sessionId){
        showWaitingWithCode(code);
        setLoading(true);
        pollTimer = setInterval(checkStatusAndMaybeComplete, 2000);
        if (typeof loadStatus === 'function'){
          statusTimer = setInterval(() => { try { loadStatus(); } catch(_) {} }, 10000);
        }
      }
    } catch(e){
      const pi = $('pairInfo');
      if (pi) pi.innerHTML = 'Error: ' + e.message;
      setLoading(false);
    }
  }

  if (startBtn) { startBtn.onclick = (ev) => { ev.preventDefault(); startFlow(); }; }
  if (drawerToggle) {
    drawerToggle.addEventListener('click', (ev) => {
      ev.preventDefault();
      toggleDrawer();
    });
  }

  window.addEventListener('resize', () => {
    ensureDrawerHeight();
  });

  // Initialize drawer open state
  ensureDrawerHeight();

  try { loadStatus(); } catch(_) {}
  setInterval(() => { try { loadStatus(); } catch(_) {} }, 10000);
})();
