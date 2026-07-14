const $ = (id) => document.getElementById(id);

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

let latestViz = null;
let mapView = null;

async function api(path, data) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data || {})
  });
  return res.json();
}

function fmt(n, digits = 2) {
  return Number.isFinite(n) ? n.toFixed(digits) : '--';
}

function speed() {
  return {
    linear: Number($('linearSpeed').value),
    angular: Number($('angularSpeed').value),
  };
}

async function move(kind) {
  const s = speed();
  const cmd = { linear_x: 0, linear_y: 0, angular_z: 0 };
  if (kind === 'forward') cmd.linear_x = s.linear;
  if (kind === 'backward') cmd.linear_x = -s.linear;
  if (kind === 'strafeLeft') cmd.linear_y = s.linear;
  if (kind === 'strafeRight') cmd.linear_y = -s.linear;
  if (kind === 'left') cmd.angular_z = s.angular;
  if (kind === 'right') cmd.angular_z = -s.angular;
  await api(kind === 'stop' ? '/api/stop' : '/api/move', cmd);
}


function bindJoystickControl(rootId, knobId, stateId) {
  const root = $(rootId);
  const knob = $(knobId);
  if (!root || !knob) return;
  let activePointer = null;
  let timer = null;
  let currentKind = 'stop';
  const radius = () => Math.max(36, root.clientWidth * 0.34);
  const setKnob = (x, y) => {
    knob.style.transform = `translate(calc(-50% + ${x}px), calc(-50% + ${y}px))`;
  };
  const send = () => {
    if (currentKind === 'stop') return;
    move(currentKind).catch(() => setText(stateId, '运动命令发送失败'));
  };
  const update = (event) => {
    const rect = root.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    let dx = event.clientX - cx;
    let dy = event.clientY - cy;
    const max = radius();
    const dist = Math.hypot(dx, dy);
    if (dist > max) {
      dx = dx / dist * max;
      dy = dy / dist * max;
    }
    setKnob(dx, dy);
    const dead = max * 0.28;
    if (Math.hypot(dx, dy) < dead) currentKind = 'stop';
    else if (Math.abs(dy) >= Math.abs(dx)) currentKind = dy < 0 ? 'forward' : 'backward';
    else currentKind = dx < 0 ? 'left' : 'right';
    setText(stateId, currentKind === 'stop' ? '' : `操控：${root.dataset[currentKind] || currentKind}`);
  };
  const start = (event) => {
    event.preventDefault();
    if (activePointer !== null) return;
    activePointer = event.pointerId;
    root.setPointerCapture?.(event.pointerId);
    update(event);
    send();
    timer = setInterval(send, 120);
  };
  const movePointer = (event) => {
    if (activePointer !== event.pointerId) return;
    event.preventDefault();
    update(event);
  };
  const stop = (event) => {
    if (activePointer !== null && event?.pointerId != null && event.pointerId !== activePointer) return;
    if (timer) clearInterval(timer);
    timer = null;
    activePointer = null;
    currentKind = 'stop';
    setKnob(0, 0);
    setText(stateId, '');
    move('stop').catch(() => {});
  };
  root.addEventListener('pointerdown', start);
  root.addEventListener('pointermove', movePointer);
  root.addEventListener('pointerup', stop);
  root.addEventListener('pointercancel', stop);
  root.addEventListener('lostpointercapture', stop);
}

function bindStrafeControl(rootId, knobId, stateId) {
  const root = $(rootId);
  const knob = $(knobId);
  if (!root || !knob) return;
  let activePointer = null;
  let timer = null;
  let currentKind = 'stop';
  const maxOffset = () => Math.max(44, root.clientWidth * 0.36);
  const setKnob = (x) => {
    knob.style.transform = `translate(calc(-50% + ${x}px), -50%)`;
  };
  const send = () => {
    if (currentKind === 'stop') return;
    move(currentKind).catch(() => setText(stateId, '运动命令发送失败'));
  };
  const update = (event) => {
    const rect = root.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const max = maxOffset();
    const dx = Math.max(-max, Math.min(max, event.clientX - cx));
    setKnob(dx);
    const dead = max * 0.22;
    currentKind = Math.abs(dx) < dead ? 'stop' : (dx < 0 ? 'strafeLeft' : 'strafeRight');
    setText(stateId, currentKind === 'stop' ? '' : `操控：${currentKind === 'strafeLeft' ? '左平移' : '右平移'}`);
  };
  const start = (event) => {
    event.preventDefault();
    if (activePointer !== null) return;
    activePointer = event.pointerId;
    root.setPointerCapture?.(event.pointerId);
    update(event);
    send();
    timer = setInterval(send, 120);
  };
  const movePointer = (event) => {
    if (activePointer !== event.pointerId) return;
    event.preventDefault();
    update(event);
  };
  const stop = (event) => {
    if (activePointer !== null && event?.pointerId != null && event.pointerId !== activePointer) return;
    if (timer) clearInterval(timer);
    timer = null;
    activePointer = null;
    currentKind = 'stop';
    setKnob(0);
    setText(stateId, '');
    move('stop').catch(() => {});
  };
  root.addEventListener('pointerdown', start);
  root.addEventListener('pointermove', movePointer);
  root.addEventListener('pointerup', stop);
  root.addEventListener('pointercancel', stop);
  root.addEventListener('lostpointercapture', stop);
}

function bindHold(button) {
  const kind = button.dataset.move;
  let timer = null;
  let activePointer = null;
  let startedAt = 0;
  let stopTimer = null;
  const send = () => move(kind).catch(() => {});
  const finishStop = () => {
    if (stopTimer) clearTimeout(stopTimer);
    stopTimer = null;
    if (kind !== 'stop') move('stop').catch(() => {});
  };
  const start = (e) => {
    e.preventDefault();
    if (activePointer !== null || timer) return;
    if (stopTimer) clearTimeout(stopTimer);
    stopTimer = null;
    activePointer = e.pointerId != null ? e.pointerId : 'mouse';
    startedAt = Date.now();
    if (button.setPointerCapture && e.pointerId != null) button.setPointerCapture(e.pointerId);
    send();
    if (kind !== 'stop') timer = setInterval(send, 120);
  };
  const stop = (e) => {
    if (activePointer !== null && e && e.pointerId != null && e.pointerId !== activePointer) return;
    if (timer) clearInterval(timer);
    timer = null;
    activePointer = null;
    if (kind === 'stop') return;
    const heldMs = Date.now() - startedAt;
    const delay = Math.max(0, 320 - heldMs);
    stopTimer = setTimeout(finishStop, delay);
  };
  button.addEventListener('pointerdown', start);
  button.addEventListener('pointerup', stop);
  button.addEventListener('pointercancel', stop);
  button.addEventListener('lostpointercapture', stop);
  button.addEventListener('mouseleave', (e) => {
    if (e.buttons === 0) stop(e);
  });
}

async function refreshStatus() {
  const res = await fetch('/api/status', { cache: 'no-store' });
  const s = await res.json();
  setText('voltage', s.voltage == null ? '--' : `${s.voltage.toFixed(2)} V`);
  setText('frontDistance', s.front_distance == null ? '--' : `${s.front_distance.toFixed(2)} m`);
  setText('guardState', s.obstacle_guard ? `ON (${s.stop_distance.toFixed(2)}m)` : 'OFF');
  setText('mapState', s.map ? `${s.map.width}x${s.map.height}` : '未接收');
  setText('planLen', s.plan_len ?? '--');
  setText('poseState', s.pose ? `${fmt(s.pose.x)}, ${fmt(s.pose.y)}, ${fmt(s.pose.theta)}` : '--');
  setText('cameraState', s.camera?.available ? (s.camera.open ? '已连接' : '待打开') : '不可用');
  setText('bringupState', s.processes?.bringup?.running ? '运行中' : '未运行');
  setText('voiceState', s.processes?.voice_ctrl?.running ? '运行中' : '未运行');
  setText('warning', s.obstacle ? '前方障碍，前进命令会被拦截' : '');
}


async function refreshViz() {
  if (!$('mapCanvas')) return;
  const res = await fetch('/api/viz', { cache: 'no-store' });
  latestViz = await res.json();
  drawMap(latestViz);
}

function makeMapImage(map) {
  const canvas = document.createElement('canvas');
  canvas.width = map.width;
  canvas.height = map.height;
  const ctx = canvas.getContext('2d');
  const image = ctx.createImageData(map.width, map.height);
  for (let y = 0; y < map.height; y += 1) {
    for (let x = 0; x < map.width; x += 1) {
      const src = (map.height - 1 - y) * map.width + x;
      const dst = (y * map.width + x) * 4;
      const v = map.data[src];
      let shade = 45;
      if (v < 0) shade = 74;
      else if (v === 0) shade = 226;
      else shade = Math.max(18, 210 - v * 1.9);
      image.data[dst] = shade;
      image.data[dst + 1] = shade;
      image.data[dst + 2] = shade;
      image.data[dst + 3] = 255;
    }
  }
  ctx.putImageData(image, 0, 0);
  return canvas;
}

function updateMapView(canvas, map) {
  const pad = 18;
  const scale = Math.min(
    (canvas.width - pad * 2) / map.width,
    (canvas.height - pad * 2) / map.height
  );
  const drawWidth = map.width * scale;
  const drawHeight = map.height * scale;
  mapView = {
    x: (canvas.width - drawWidth) / 2,
    y: (canvas.height - drawHeight) / 2,
    width: drawWidth,
    height: drawHeight,
    scale,
    map,
    image: makeMapImage(map),
  };
}

function worldToCanvas(x, y) {
  if (!mapView) return null;
  const { map } = mapView;
  const mx = (x - map.origin.x) / map.resolution;
  const my = (y - map.origin.y) / map.resolution;
  return {
    x: mapView.x + mx * mapView.scale,
    y: mapView.y + (map.height - my) * mapView.scale,
  };
}

function canvasToWorld(x, y) {
  if (!mapView) return null;
  const { map } = mapView;
  const mx = (x - mapView.x) / mapView.scale;
  const my = map.height - (y - mapView.y) / mapView.scale;
  return {
    x: map.origin.x + mx * map.resolution,
    y: map.origin.y + my * map.resolution,
  };
}

function drawRobot(ctx, pose) {
  const p = worldToCanvas(pose.x, pose.y);
  if (!p) return;
  ctx.save();
  ctx.translate(p.x, p.y);
  ctx.rotate(-pose.theta);
  ctx.fillStyle = '#fb7185';
  ctx.beginPath();
  ctx.moveTo(14, 0);
  ctx.lineTo(-10, -8);
  ctx.lineTo(-7, 0);
  ctx.lineTo(-10, 8);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

function drawPolyline(ctx, points, color, width) {
  if (!points || points.length < 2) return;
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.beginPath();
  let started = false;
  points.forEach((pt) => {
    const p = worldToCanvas(pt.x, pt.y);
    if (!p) return;
    if (!started) {
      ctx.moveTo(p.x, p.y);
      started = true;
    } else {
      ctx.lineTo(p.x, p.y);
    }
  });
  ctx.stroke();
}

function drawScan(ctx, scan, pose) {
  if (!scan || !pose) return;
  const cos = Math.cos(pose.theta);
  const sin = Math.sin(pose.theta);
  ctx.fillStyle = '#38bdf8';
  scan.forEach((pt) => {
    const wx = pose.x + pt.x * cos - pt.y * sin;
    const wy = pose.y + pt.x * sin + pt.y * cos;
    const p = worldToCanvas(wx, wy);
    if (!p) return;
    ctx.fillRect(p.x - 1, p.y - 1, 2, 2);
  });
}

function drawFallback(ctx, canvas) {
  ctx.fillStyle = '#0f172a';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = '#243047';
  ctx.lineWidth = 1;
  for (let x = 0; x < canvas.width; x += 40) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, canvas.height);
    ctx.stroke();
  }
  for (let y = 0; y < canvas.height; y += 40) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(canvas.width, y);
    ctx.stroke();
  }
  ctx.fillStyle = '#94a3b8';
  ctx.font = '16px system-ui';
  ctx.fillText('等待 /map 数据；启动建图或导航后显示地图', 24, 36);
}

function drawMap(viz) {
  const canvas = $('mapCanvas');
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!viz?.map) {
    mapView = null;
    drawFallback(ctx, canvas);
    $('vizState').textContent = '无地图';
    return;
  }

  updateMapView(canvas, viz.map);
  ctx.fillStyle = '#0f172a';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(mapView.image, mapView.x, mapView.y, mapView.width, mapView.height);
  ctx.strokeStyle = '#64748b';
  ctx.strokeRect(mapView.x, mapView.y, mapView.width, mapView.height);

  drawScan(ctx, viz.scan, viz.pose);
  drawPolyline(ctx, viz.plan, '#fbbf24', 3);
  if (viz.pose) drawRobot(ctx, viz.pose);

  const age = viz.ages?.map == null ? '--' : `${viz.ages.map.toFixed(1)}s`;
  $('vizState').textContent = `地图 ${viz.map.width}x${viz.map.height} / ${age}`;
}

function bindMapClick() {
  const canvas = $('mapCanvas');
  if (!canvas) return;
  canvas.addEventListener('click', (event) => {
    if (!mapView) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const scaleX = event.currentTarget.width / rect.width;
    const scaleY = event.currentTarget.height / rect.height;
    const world = canvasToWorld((event.clientX - rect.left) * scaleX, (event.clientY - rect.top) * scaleY);
    if (!world) return;
    if ($('goalX')) $('goalX').value = world.x.toFixed(2);
    if ($('goalY')) $('goalY').value = world.y.toFixed(2);
  });
}

async function sendAiVoiceCommand(text) {
  const cleaned = (text || '').trim();
  if (!cleaned) {
    setText('aiVoiceState', '没有命令');
    return;
  }
  setText('aiVoiceState', '分析中');
  setText('aiVoiceResult', `用户命令：${cleaned}
正在调用 Agent...`);
  try {
    const result = await api('/api/agent/voice', { text: cleaned });
    const action = result.plan?.action || 'none';
    const stepCount = Array.isArray(result.plan?.steps) ? result.plan.steps.length : 0;
    setText('aiVoiceState', result.ok ? `已执行：${action}${stepCount ? ` / ${stepCount}步` : ''}` : '未执行');
    setText('aiVoiceResult', JSON.stringify(result, null, 2));
    await refreshStatus();
  } catch (err) {
    setText('aiVoiceState', '失败');
    setText('aiVoiceResult', `执行失败：${err.message || err}`);
  }
}

function initAiVoiceControl() {
  const input = $('aiVoiceText');
  const sendBtn = $('sendAiVoiceText');
  const voiceBtn = $('startAiVoice');
  if (!input || !sendBtn || !voiceBtn) return;
  sendBtn.onclick = () => sendAiVoiceCommand(input.value);
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') sendAiVoiceCommand(input.value);
  });
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    voiceBtn.textContent = '语音不可用';
    voiceBtn.disabled = true;
    setText('aiVoiceState', '可输入文字');
    return;
  }
  const recognition = new SpeechRecognition();
  recognition.lang = 'zh-CN';
  recognition.interimResults = false;
  recognition.continuous = false;
  recognition.onstart = () => setText('aiVoiceState', '正在听');
  recognition.onerror = (event) => setText('aiVoiceState', `语音失败：${event.error || 'unknown'}`);
  recognition.onend = () => {
    if ($('aiVoiceState')?.textContent === '正在听') setText('aiVoiceState', '待命');
  };
  recognition.onresult = (event) => {
    const text = event.results?.[0]?.[0]?.transcript || '';
    input.value = text;
    sendAiVoiceCommand(text);
  };
  voiceBtn.onclick = () => {
    try { recognition.start(); }
    catch (err) { setText('aiVoiceState', `语音启动失败：${err.message || err}`); }
  };
}

function init() {
  document.querySelectorAll('[data-move]').forEach(bindHold);
  initAiVoiceControl();
  bindJoystickControl('driveJoystick', 'driveKnob', 'warning');
  bindStrafeControl('strafeSlider', 'strafeKnob', 'warning');
  if ($('stopAll')) $('stopAll').onclick = () => api('/api/stop');
  if ($('linearSpeed')) $('linearSpeed').oninput = () => setText('linearVal', $('linearSpeed').value);
  if ($('angularSpeed')) $('angularSpeed').oninput = () => setText('angularVal', $('angularSpeed').value);
  if ($('applyGuard')) {
    $('applyGuard').onclick = () => api('/api/guard', {
      enabled: $('guardEnabled') ? $('guardEnabled').checked : true,
      stop_distance: $('stopDistance') ? Number($('stopDistance').value) : 0.35
    });
  }
  document.querySelectorAll('[data-start]').forEach((btn) => {
    btn.onclick = async () => {
      const name = btn.dataset.start;
      await api('/api/process/start', { name });
      if (name === 'camera' && $('cameraView')) {
        $('cameraView').src = `/api/camera/stream?t=${Date.now()}`;
      }
      await refreshStatus();
    };
  });
  document.querySelectorAll('[data-stop]').forEach((btn) => {
    btn.onclick = () => api('/api/process/stop', { name: btn.dataset.stop });
  });
  if ($('sendGoal')) {
    $('sendGoal').onclick = () => api('/api/nav/goal', {
      x: Number($('goalX').value),
      y: Number($('goalY').value),
      theta: Number($('goalTheta').value)
    });
  }
  bindMapClick();
  setInterval(refreshStatus, 700);
  if ($('mapCanvas')) setInterval(refreshViz, 1200);
  refreshStatus();
  refreshViz();
}


init();
