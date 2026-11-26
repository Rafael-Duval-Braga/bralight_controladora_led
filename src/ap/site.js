(() => {
  const THROTTLE_MS = 67; // ~15 envios por segundo

  const canvas = document.getElementById("colorWheel");
  const dimmerSlider = document.getElementById("dimmerSlider");
  const dimmerValue = document.getElementById("dimmerValue");
  const rgbReadout = document.getElementById("rgbReadout");
  const statusEl = document.getElementById("status");

  if (!canvas || !dimmerSlider) {
    console.error("Elementos #colorWheel ou #dimmerSlider não encontrados no DOM.");
    return;
  }

  const ctx = canvas.getContext("2d");
  const size = Math.min(canvas.width || 260, canvas.height || 260);
  canvas.width = size;
  canvas.height = size;

  const cx = size / 2;
  const cy = size / 2;
  const radius = size / 2 - 4;

  // Estado base: (r, g, b) em brilho máximo (HSV V=1) + intensidade d (0..255)
  const state = {
    r: 255,
    g: 0,
    b: 0,
    d: 255,
    hue: 0,
    sat: 1,
  };

  let lastSendTime = 0;
  let pendingTimer = null;
  let isDraggingWheel = false;

  // Converte HSV (h em [0..360), s em [0..1], v em [0..1]) para RGB [0..255]
  function hsvToRgb(h, s, v) {
    h = ((h % 360) + 360) % 360;
    const c = v * s;
    const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
    const m = v - c;

    let rp = 0, gp = 0, bp = 0;
    if (h < 60) {
      rp = c; gp = x; bp = 0;
    } else if (h < 120) {
      rp = x; gp = c; bp = 0;
    } else if (h < 180) {
      rp = 0; gp = c; bp = x;
    } else if (h < 240) {
      rp = 0; gp = x; bp = c;
    } else if (h < 300) {
      rp = x; gp = 0; bp = c;
    } else {
      rp = c; gp = 0; bp = x;
    }

    return [
      Math.round((rp + m) * 255),
      Math.round((gp + m) * 255),
      Math.round((bp + m) * 255),
    ];
  }

  // Estima HSV aproximado a partir de um RGB em brilho máximo para posicionar o cursor ao carregar /state
  function rgbToWheelPos(r, g, b) {
    const rn = r / 255;
    const gn = g / 255;
    const bn = b / 255;
    const max = Math.max(rn, gn, bn);
    const min = Math.min(rn, gn, bn);
    const delta = max - min;

    let h = 0;
    if (delta > 0) {
      if (max === rn) {
        h = 60 * (((gn - bn) / delta) % 6);
      } else if (max === gn) {
        h = 60 * ((bn - rn) / delta + 2);
      } else {
        h = 60 * ((rn - gn) / delta + 4);
      }
    }
    if (h < 0) h += 360;

    const s = max === 0 ? 0 : delta / max;
    return { hue: h, sat: Math.min(1, s) };
  }

  // A roda em brilho máximo (V = 1) é calculada UMA vez num canvas fora da tela;
  // a cada movimento só copiamos essa imagem e redesenhamos o marcador.
  const wheelCache = document.createElement("canvas");
  wheelCache.width = size;
  wheelCache.height = size;

  (function buildWheelCache() {
    const cctx = wheelCache.getContext("2d");
    const imgData = cctx.createImageData(size, size);
    const data = imgData.data;

    for (let y = 0; y < size; y++) {
      const dy = y - cy;
      for (let x = 0; x < size; x++) {
        const dx = x - cx;
        const dist = Math.hypot(dx, dy);

        if (dist <= radius) {
          const sat = dist / radius;
          const angleDeg = (Math.atan2(dy, dx) * 180) / Math.PI;
          const [r, g, b] = hsvToRgb(angleDeg, sat, 1);
          const idx = (y * size + x) * 4;
          data[idx] = r;
          data[idx + 1] = g;
          data[idx + 2] = b;
          data[idx + 3] = 255;
        }
      }
    }

    cctx.putImageData(imgData, 0, 0);
  })();

  function drawWheel() {
    ctx.clearRect(0, 0, size, size);
    ctx.drawImage(wheelCache, 0, 0);

    // Borda suave da roda
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(255, 255, 255, 0.25)";
    ctx.lineWidth = 2;
    ctx.stroke();

    // Marcador da cor selecionada
    const rad = (state.hue * Math.PI) / 180;
    const dist = state.sat * radius;
    const px = cx + Math.cos(rad) * dist;
    const py = cy + Math.sin(rad) * dist;

    ctx.beginPath();
    ctx.arc(px, py, 9, 0, Math.PI * 2);
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2.5;
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(px, py, 10.5, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(0, 0, 0, 0.7)";
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  function updateUI() {
    dimmerSlider.value = String(state.d);
    if (dimmerValue) {
      const pct = Math.round((state.d / 255) * 100);
      dimmerValue.textContent = `${state.d} (${pct}%)`;
    }
    if (rgbReadout) {
      rgbReadout.textContent = `Cor base: (${state.r}, ${state.g}, ${state.b}) · Dimmer (d): ${state.d}`;
    }
    drawWheel();
  }

  let sending = false;
  let dirty = false;

  async function sendStateNow() {
    // Um pedido por vez: cada fetch abre uma conexão TCP nova no ESP32.
    // Se já houver um em andamento, marca que o estado mudou e reenvia depois.
    if (sending) {
      dirty = true;
      return;
    }
    sending = true;
    const url = `/set?r=${state.r}&g=${state.g}&b=${state.b}&d=${state.d}`;
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 2500);
    try {
      const resp = await fetch(url, { cache: "no-store", signal: ctrl.signal });
      if (statusEl) {
        statusEl.textContent = resp.ok ? "Sincronizado" : `HTTP ${resp.status}`;
      }
    } catch (_) {
      if (statusEl) {
        statusEl.textContent = "Sem conexão com o AP";
      }
    } finally {
      clearTimeout(timeout);
      sending = false;
      if (dirty) {
        dirty = false;
        scheduleSend();
      }
    }
  }

  // Garante ~15 envios/s enviando sempre o estado mais recente acumulado
  function scheduleSend() {
    const now = performance.now();
    const elapsed = now - lastSendTime;

    if (elapsed >= THROTTLE_MS && !pendingTimer) {
      lastSendTime = now;
      sendStateNow();
      return;
    }

    if (!pendingTimer) {
      const waitMs = Math.max(0, THROTTLE_MS - elapsed);
      pendingTimer = setTimeout(() => {
        pendingTimer = null;
        lastSendTime = performance.now();
        sendStateNow();
      }, waitMs);
    }
  }

  function updateFromPointer(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;

    const x = (clientX - rect.left) * scaleX;
    const y = (clientY - rect.top) * scaleY;

    const dx = x - cx;
    const dy = y - cy;
    const dist = Math.min(radius, Math.hypot(dx, dy));

    const hue = ((Math.atan2(dy, dx) * 180) / Math.PI + 360) % 360;
    const sat = radius === 0 ? 0 : dist / radius;

    // Cor em brilho máximo (V = 1); a dimerização e o gamma ficam a cargo do ESP32-C3
    const [r, g, b] = hsvToRgb(hue, sat, 1);

    state.hue = hue;
    state.sat = sat;
    state.r = r;
    state.g = g;
    state.b = b;

    updateUI();
    scheduleSend();
  }

  canvas.style.touchAction = "none";

  canvas.addEventListener("pointerdown", (e) => {
    isDraggingWheel = true;
    canvas.setPointerCapture(e.pointerId);
    updateFromPointer(e.clientX, e.clientY);
  });

  canvas.addEventListener("pointermove", (e) => {
    if (!isDraggingWheel) return;
    updateFromPointer(e.clientX, e.clientY);
  });

  const stopDragging = (e) => {
    if (!isDraggingWheel) return;
    isDraggingWheel = false;
    if (e && canvas.hasPointerCapture(e.pointerId)) {
      canvas.releasePointerCapture(e.pointerId);
    }
    // Dispara envio final ao soltar para garantir que o último ponto seja aplicado
    if (pendingTimer) {
      clearTimeout(pendingTimer);
      pendingTimer = null;
    }
    lastSendTime = performance.now();
    sendStateNow();
  };

  canvas.addEventListener("pointerup", stopDragging);
  canvas.addEventListener("pointercancel", stopDragging);

  dimmerSlider.addEventListener("input", () => {
    const val = Math.max(0, Math.min(255, parseInt(dimmerSlider.value, 10) || 0));
    state.d = val;
    updateUI();
    scheduleSend();
  });

  dimmerSlider.addEventListener("change", () => {
    if (pendingTimer) {
      clearTimeout(pendingTimer);
      pendingTimer = null;
    }
    lastSendTime = performance.now();
    sendStateNow();
  });

  async function loadInitialState() {
    try {
      const resp = await fetch("/state", { cache: "no-store" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const data = await resp.json();

      state.r = Math.max(0, Math.min(255, Number(data.r ?? state.r)));
      state.g = Math.max(0, Math.min(255, Number(data.g ?? state.g)));
      state.b = Math.max(0, Math.min(255, Number(data.b ?? state.b)));
      state.d = Math.max(0, Math.min(255, Number(data.d ?? state.d)));

      const pos = rgbToWheelPos(state.r, state.g, state.b);
      state.hue = pos.hue;
      state.sat = pos.sat;
      if (statusEl) statusEl.textContent = "Conectado";
      updateUI();
    } catch (_) {
      // Ainda sem resposta do ESP32: mantém o padrão e tenta de novo em 2 s
      if (statusEl) statusEl.textContent = "Sem conexão com o AP";
      setTimeout(loadInitialState, 2000);
    }
  }

  updateUI();
  loadInitialState();
})();