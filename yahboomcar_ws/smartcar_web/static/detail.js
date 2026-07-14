const $ = (id) => document.getElementById(id);

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

async function post(path, data) {
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

function voltagePercent(v) {
  if (!Number.isFinite(v)) return null;
  const min = 9.6;
  const max = 12.6;
  return Math.max(0, Math.min(100, ((v - min) / (max - min)) * 100));
}

async function refreshPower() {
  const res = await fetch('/api/status', { cache: 'no-store' });
  const s = await res.json();
  const v = s.voltage;
  $('powerVoltage').textContent = v == null ? '--' : `${v.toFixed(2)} V`;
  const pct = voltagePercent(v);
  $('batteryFill').style.width = pct == null ? '0%' : `${pct}%`;
  $('batteryPercent').textContent = pct == null ? '未收到 /voltage 数据' : `估算电量 ${pct.toFixed(0)}%`;
  $('powerBringup').textContent = s.processes?.bringup?.running ? '运行中' : '未运行';
  $('powerScanAge').textContent = s.scan_age == null ? '--' : `${s.scan_age.toFixed(2)} s`;
  $('powerFrontDistance').textContent = s.front_distance == null ? '--' : `${s.front_distance.toFixed(2)} m`;
}

let mapView = null;

let slamPoseMode = 'goal';
let slamInitialPose = null;
let slamGoalPose = null;
let selectedSlamMap = null;
let slamPlanVisible = false;
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
      let r = 30;
      let g = 41;
      let b = 59;
      if (v < 0) {
        r = 48; g = 64; b = 86;
      } else if (v === 0) {
        r = 248; g = 250; b = 252;
      } else {
        const shade = Math.max(0, 245 - v * 3.1);
        r = Math.min(80, shade);
        g = Math.min(92, shade);
        b = Math.min(108, shade);
      }
      image.data[dst] = r;
      image.data[dst + 1] = g;
      image.data[dst + 2] = b;
      image.data[dst + 3] = 255;
    }
  }
  ctx.putImageData(image, 0, 0);
  return canvas;
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
  const my = map.height - ((y - mapView.y) / mapView.scale);
  if (mx < 0 || my < 0 || mx > map.width || my > map.height) return null;
  return {
    x: map.origin.x + mx * map.resolution,
    y: map.origin.y + my * map.resolution,
  };
}

function drawMetricGrid(ctx) {
  if (!mapView) return;
  const { map } = mapView;
  const spacingM = map.resolution * map.width > 18 ? 2 : 1;
  const minX = map.origin.x;
  const maxX = map.origin.x + map.width * map.resolution;
  const minY = map.origin.y;
  const maxY = map.origin.y + map.height * map.resolution;
  ctx.save();
  ctx.strokeStyle = 'rgba(14, 165, 233, 0.22)';
  ctx.fillStyle = 'rgba(191, 219, 254, 0.9)';
  ctx.lineWidth = 1;
  ctx.font = '12px system-ui';
  const startX = Math.ceil(minX / spacingM) * spacingM;
  for (let wx = startX; wx <= maxX; wx += spacingM) {
    const top = worldToCanvas(wx, maxY);
    const bottom = worldToCanvas(wx, minY);
    if (!top || !bottom) continue;
    ctx.beginPath();
    ctx.moveTo(top.x, top.y);
    ctx.lineTo(bottom.x, bottom.y);
    ctx.stroke();
    ctx.fillText(`${wx.toFixed(0)}m`, bottom.x + 3, Math.min(mapView.y + mapView.height - 6, bottom.y - 3));
  }
  const startY = Math.ceil(minY / spacingM) * spacingM;
  for (let wy = startY; wy <= maxY; wy += spacingM) {
    const left = worldToCanvas(minX, wy);
    const right = worldToCanvas(maxX, wy);
    if (!left || !right) continue;
    ctx.beginPath();
    ctx.moveTo(left.x, left.y);
    ctx.lineTo(right.x, right.y);
    ctx.stroke();
    ctx.fillText(`${wy.toFixed(0)}m`, mapView.x + 6, left.y - 4);
  }
  ctx.restore();
}

function drawRobot(ctx, pose) {
  const p = worldToCanvas(pose.x, pose.y);
  if (!p) return;
  ctx.save();
  ctx.translate(p.x, p.y);
  ctx.rotate(-pose.theta);
  ctx.fillStyle = '#fb7185';
  ctx.beginPath();
  ctx.moveTo(16, 0);
  ctx.lineTo(-11, -8);
  ctx.lineTo(-8, 0);
  ctx.lineTo(-11, 8);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
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
    if (p) ctx.fillRect(p.x - 1, p.y - 1, 2, 2);
  });
}

function drawPoseMarker(ctx, pose, color, label) {
  if (!pose) return;
  const p = worldToCanvas(pose.x, pose.y);
  if (!p) return;
  ctx.save();
  ctx.translate(p.x, p.y);
  ctx.rotate(-Number(pose.theta || 0));
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(0, 0, 9, 0, Math.PI * 2);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(13, 0);
  ctx.lineTo(0, -5);
  ctx.lineTo(0, 5);
  ctx.closePath();
  ctx.fill();
  ctx.rotate(Number(pose.theta || 0));
  ctx.font = '13px system-ui';
  ctx.fillText(label, 13, -12);
  ctx.restore();
}

function drawPlan(ctx, plan) {
  if (!plan || plan.length < 2) return;
  ctx.strokeStyle = '#fbbf24';
  ctx.lineWidth = 3;
  ctx.beginPath();
  let started = false;
  plan.forEach((pt) => {
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

function drawDetailMap(viz) {
  const canvas = $('detailMapCanvas');
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!viz?.map) {
    mapView = null;
    ctx.fillStyle = '#0f172a';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#94a3b8';
    ctx.font = '18px system-ui';
    ctx.fillText('等待 /map 数据；点击启动建图后显示地图', 28, 42);
    $('detailVizState').textContent = '无地图';
    return;
  }
  const pad = 20;
  const scale = Math.min((canvas.width - pad * 2) / viz.map.width, (canvas.height - pad * 2) / viz.map.height);
  const width = viz.map.width * scale;
  const height = viz.map.height * scale;
  mapView = {
    x: (canvas.width - width) / 2,
    y: (canvas.height - height) / 2,
    width,
    height,
    scale,
    map: viz.map,
  };
  ctx.fillStyle = '#0f172a';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(makeMapImage(viz.map), mapView.x, mapView.y, mapView.width, mapView.height);
  drawMetricGrid(ctx);
  ctx.strokeStyle = '#0ea5e9';
  ctx.lineWidth = 2;
  ctx.strokeRect(mapView.x, mapView.y, mapView.width, mapView.height);
  drawScan(ctx, viz.scan, viz.pose);
  if (slamPlanVisible) drawPlan(ctx, viz.plan);
  drawPoseMarker(ctx, slamInitialPose, '#a7f3d0', '初始');
  drawPoseMarker(ctx, slamGoalPose, '#f97316', '目标');
  if (viz.pose) drawRobot(ctx, viz.pose);
  const age = viz.ages?.map == null ? '--' : `${viz.ages.map.toFixed(1)}s`;
  $('detailVizState').textContent = `地图 ${viz.map.width}x${viz.map.height} / ${age}`;
}

async function refreshSlam() {
  const [statusRes, vizRes] = await Promise.all([
    fetch('/api/status', { cache: 'no-store' }),
    fetch('/api/viz', { cache: 'no-store' })
  ]);
  const s = await statusRes.json();
  const viz = await vizRes.json();
  $('slamMapState').textContent = s.map ? `${s.map.width}x${s.map.height}` : '未接收';
  $('slamPoseState').textContent = s.pose ? `${fmt(s.pose.x)}, ${fmt(s.pose.y)}, ${fmt(s.pose.theta)}` : '--';
  $('slamScanAge').textContent = s.scan_age == null ? '--' : `${s.scan_age.toFixed(2)} s`;
  if ($('slamPlanState')) $('slamPlanState').textContent = (slamPlanVisible && s.plan_len) ? `${s.plan_len} 个路径点` : '未生成';
  if ($('navRunState')) {
    const navRunning = (s.processes && ((s.processes.nav_dwa && s.processes.nav_dwa.running) || (s.processes.nav_teb && s.processes.nav_teb.running)));
    $('navRunState').textContent = navRunning ? '运行中' : '未运行';
  }
  if ($('slamDriveCard')) $('slamDriveCard').hidden = false;
  drawDetailMap(viz);
}

function sensorValue(props, key, digits = 2) {
  const n = Number(props?.[key]);
  return Number.isFinite(n) ? n.toFixed(digits) : '--';
}

async function refreshSensors() {
  const host = $('sensorHost')?.value?.trim() || '192.168.1.11';
  const port = $('sensorPort')?.value || '8888';
  setText('sensorState', '读取中');
  setText('sensorError', '');
  try {
    const res = await fetch(`/api/sensors?host=${encodeURIComponent(host)}&port=${encodeURIComponent(port)}`, { cache: 'no-store' });
    const data = await res.json();
    if (!data.ok) {
      setText('sensorState', '连接失败');
      setText('sensorError', `无法读取 ${data.host || host}:${data.port || port}，请确认运行 web 的设备已连接 ohcar WiFi。${data.error || ''}`);
      return;
    }
    const p = data.properties || {};
    setText('sensorState', '已连接');
    setText('sensorTemperature', sensorValue(p, 'temperature'));
    setText('sensorHumidity', sensorValue(p, 'humidity'));
    setText('sensorIllumination', sensorValue(p, 'illumination'));
    setText('sensorSmoke', sensorValue(p, 'smoke'));
    setText('sensorPressure', sensorValue(p, 'pressure'));
    setText('sensorBattery', sensorValue(p, 'battery', 0));
    setText('sensorLongitude', p.longitude ?? '--');
    setText('sensorLatitude', p.latitude ?? '--');
    setText('sensorLatency', data.latency_ms == null ? '--' : `${data.latency_ms} ms`);
    setText('sensorRaw', JSON.stringify(data.raw, null, 2));
  } catch (err) {
    setText('sensorState', '连接失败');
    setText('sensorError', `读取失败：${err.message || err}`);
  }
}


function initSensors() {
  if ($('refreshSensors')) $('refreshSensors').onclick = refreshSensors;
  refreshSensors();
  setInterval(refreshSensors, 1000);
}

function initPower() {
  $('recoverBringup').onclick = async () => {
    await post('/api/process/start', { name: 'bringup' });
    await refreshPower();
  };
  refreshPower();
  setInterval(refreshPower, 1000);
}

function slamSpeed() {
  const linearEl = $('slamLinearSpeed');
  const angularEl = $('slamAngularSpeed');
  return {
    linear: linearEl ? Number(linearEl.value) : 0.10,
    angular: angularEl ? Number(angularEl.value) : 0.35,
  };
}

async function moveSlamCar(kind) {
  const s = slamSpeed();
  const cmd = { linear_x: 0, linear_y: 0, angular_z: 0 };
  if (kind === 'forward') cmd.linear_x = s.linear;
  if (kind === 'backward') cmd.linear_x = -s.linear;
  if (kind === 'strafeLeft') cmd.linear_y = s.linear;
  if (kind === 'strafeRight') cmd.linear_y = -s.linear;
  if (kind === 'left') cmd.angular_z = s.angular;
  if (kind === 'right') cmd.angular_z = -s.angular;
  const res = await post(kind === 'stop' ? '/api/stop' : '/api/move', cmd);
  if ($('slamMoveState')) {
    $('slamMoveState').textContent = res.blocked ? '前方障碍，前进命令已被拦截' : '';
  }
  return res;
}


function bindSlamJoystickControl(rootId, knobId, stateId) {
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
    moveSlamCar(currentKind).catch(() => setText(stateId, '运动命令发送失败'));
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
    moveSlamCar('stop').catch(() => {});
  };
  root.addEventListener('pointerdown', start);
  root.addEventListener('pointermove', movePointer);
  root.addEventListener('pointerup', stop);
  root.addEventListener('pointercancel', stop);
  root.addEventListener('lostpointercapture', stop);
}

function bindSlamStrafeControl(rootId, knobId, stateId) {
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
    moveSlamCar(currentKind).catch(() => setText(stateId, '运动命令发送失败'));
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
    moveSlamCar('stop').catch(() => {});
  };
  root.addEventListener('pointerdown', start);
  root.addEventListener('pointermove', movePointer);
  root.addEventListener('pointerup', stop);
  root.addEventListener('pointercancel', stop);
  root.addEventListener('lostpointercapture', stop);
}

function bindSlamHold(button) {
  const kind = button.dataset.move;
  let timer = null;
  let activePointer = null;
  let startedAt = 0;
  let stopTimer = null;
  const send = () => moveSlamCar(kind).catch(() => {
    if ($('slamMoveState')) $('slamMoveState').textContent = '运动命令发送失败';
  });
  const finishStop = () => {
    if (stopTimer) clearTimeout(stopTimer);
    stopTimer = null;
    if (kind !== 'stop') moveSlamCar('stop').catch(() => {});
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
    stopTimer = setTimeout(finishStop, Math.max(0, 320 - heldMs));
  };
  button.addEventListener('pointerdown', start);
  button.addEventListener('pointerup', stop);
  button.addEventListener('pointercancel', stop);
  button.addEventListener('lostpointercapture', stop);
  button.addEventListener('mouseleave', (e) => {
    if (e.buttons === 0) stop(e);
  });
}

function initSlamDriveControls() {
  document.querySelectorAll('[data-move]').forEach(bindSlamHold);
  bindSlamJoystickControl('slamJoystick', 'slamKnob', 'slamMoveState');
  bindSlamStrafeControl('slamStrafeSlider', 'slamStrafeKnob', 'slamMoveState');
  if ($('slamLinearSpeed')) $('slamLinearSpeed').oninput = () => setText('slamLinearVal', $('slamLinearSpeed').value);
  if ($('slamAngularSpeed')) $('slamAngularSpeed').oninput = () => setText('slamAngularVal', $('slamAngularSpeed').value);
}

function sleepMs(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function setNavState(text) {
  setText('navWorkflowState', text || '');
}

function poseFromFields(prefix) {
  return {
    x: Number($(`${prefix}X`).value),
    y: Number($(`${prefix}Y`).value),
    theta: Number($(`${prefix}Theta`).value),
  };
}

function setPoseMode(mode) {
  slamPoseMode = mode;
  setText('slamPoseModeState', mode === 'initial' ? '初始位置：点击地图设置小车当前位置' : '目标位置：点击地图设置目的地');
}

function updatePoseFromCanvas(world) {
  if (!world) return;
  setText('mapPointerState', `选中坐标：${world.x.toFixed(2)}, ${world.y.toFixed(2)}`);
  if (slamPoseMode === 'initial') {
    $('initialX').value = world.x.toFixed(2);
    $('initialY').value = world.y.toFixed(2);
    slamInitialPose = poseFromFields('initial');
  } else {
    $('goalX').value = world.x.toFixed(2);
    $('goalY').value = world.y.toFixed(2);
    slamGoalPose = poseFromFields('goal');
  }
  refreshSlam().catch(() => {});
}

function bindSlamMapClick() {
  const canvas = $('detailMapCanvas');
  if (!canvas) return;
  canvas.addEventListener('click', (event) => {
    const rect = canvas.getBoundingClientRect();
    const x = (event.clientX - rect.left) * (canvas.width / rect.width);
    const y = (event.clientY - rect.top) * (canvas.height / rect.height);
    updatePoseFromCanvas(canvasToWorld(x, y));
  });
  canvas.addEventListener('mousemove', (event) => {
    const rect = canvas.getBoundingClientRect();
    const x = (event.clientX - rect.left) * (canvas.width / rect.width);
    const y = (event.clientY - rect.top) * (canvas.height / rect.height);
    const world = canvasToWorld(x, y);
    if (world) setText('mapPointerState', `坐标：${world.x.toFixed(2)}, ${world.y.toFixed(2)}`);
  });
}

async function loadSlamMaps() {
  if (!$('slamMapList')) return;
  const res = await fetch('/api/maps', { cache: 'no-store' });
  const data = await res.json();
  const list = $('slamMapList');
  list.innerHTML = '';
  if (data.selected) {
    selectedSlamMap = data.selected;
    const selectedItem = (data.maps || []).find((item) => item.yaml === data.selected);
    setText('selectedMapName', selectedItem ? selectedItem.name : data.selected);
    if ($('navWorkflowCard')) $('navWorkflowCard').hidden = false;
  }
  if (!data.maps || !data.maps.length) {
    list.innerHTML = '<div class="empty-state">还没有保存地图</div>';
    return;
  }
  data.maps.forEach((item) => {
    const btn = document.createElement('button');
    btn.className = 'map-item';
    btn.innerHTML = `<strong>${item.name}</strong><span>${item.updated_text || ''}</span><small>${item.yaml}</small>`;
    if (data.selected === item.yaml || selectedSlamMap === item.yaml) btn.classList.add('selected');
    btn.onclick = async () => {
      const sel = await post('/api/maps/select', { yaml: item.yaml });
      if (!sel.ok) {
        setNavState(sel.error || '选择地图失败');
        return;
      }
      selectedSlamMap = item.yaml;
      setText('selectedMapName', item.name);
      if ($('navWorkflowCard')) $('navWorkflowCard').hidden = false;
      await loadSlamMaps();
    };
    list.appendChild(btn);
  });
}

async function saveNamedMap() {
  const name = $('mapSaveName') ? $('mapSaveName').value.trim() : '';
  setText('saveMapState', '正在保存地图...');
  const res = await post('/api/maps/save', { name });
  if (!res.ok) {
    setText('saveMapState', res.error || '保存失败');
    return;
  }
  setText('saveMapState', `保存任务已启动：${res.map_name || name}`);
  setTimeout(loadSlamMaps, 2500);
}

async function waitForBringupReady(timeoutMs = 14000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch('/api/status', { cache: 'no-store' });
      const status = await res.json();
      const ready = status.processes && status.processes.bringup && status.processes.bringup.running
        && status.scan_age != null && status.scan_age < 2;
      if (ready) return status;
    } catch (err) {}
    await sleepMs(700);
  }
  return null;
}

async function startNavStack() {
  if (!selectedSlamMap) {
    setNavState('请先选择一张地图');
    return;
  }
  const algorithm = $('navAlgorithm').value;
  setNavState('正在启动底盘/雷达...');
  await post('/api/process/start', { name: 'bringup' });
  const ready = await waitForBringupReady();
  if (!ready) {
    setNavState('底盘/雷达未就绪，请重新点击启动底盘/雷达');
    return;
  }
  setNavState('正在重启导航栈...');
  await post('/api/process/stop', { name: 'nav_dwa', force_external: true });
  await post('/api/process/stop', { name: 'nav_teb', force_external: true });
  await sleepMs(1200);
  const res = await post('/api/process/start', { name: algorithm });
  setNavState(res.ok ? '导航功能已启动，请应用初始位姿后再开始导航' : (res.error || '导航启动失败'));
  if (slamInitialPose) await post('/api/nav/initial_pose', slamInitialPose);
  setTimeout(refreshSlam, 1600);
  await refreshSlam();
}

async function applyInitialPose() {
  slamInitialPose = poseFromFields('initial');
  const res = await post('/api/nav/initial_pose', slamInitialPose);
  setNavState(res.ok ? '初始位姿已应用，等待 AMCL 定位稳定后再选择目标点' : (res.error || '初始位姿设置失败'));
  setTimeout(refreshSlam, 1200);
  await refreshSlam();
}

async function startNavigationGoal() {
  if (!selectedSlamMap) {
    setNavState('请先选择一张地图');
    return;
  }
  if (!slamInitialPose) {
    setNavState('请先在地图上选择初始位置，然后点击“应用初始位姿”');
    setPoseMode('initial');
    return;
  }
  slamGoalPose = poseFromFields('goal');
  if (!Number.isFinite(slamGoalPose.x) || !Number.isFinite(slamGoalPose.y)) {
    setNavState('请先在地图上选择目标位置');
    setPoseMode('goal');
    return;
  }
  setNavState('正在发布初始位姿，等待定位坐标系建立...');
  const initRes = await post('/api/nav/initial_pose', slamInitialPose);
  if (!initRes.ok) {
    setNavState(initRes.error || '初始位姿发布失败');
    return;
  }
  await sleepMs(1400);
  setNavState('正在发送目标点，等待规划路线...');
  slamPlanVisible = true;
  const res = await post('/api/nav/goal', slamGoalPose);
  setNavState(res.ok ? '目标点已发送，规划路线会显示在地图上' : (res.error || '目标点发送失败'));
  setTimeout(refreshSlam, 1200);
  await refreshSlam();
}

function clearNavigationOverlay() {
  slamInitialPose = null;
  slamGoalPose = null;
  slamPlanVisible = false;
  ['initialX', 'initialY', 'initialTheta', 'goalX', 'goalY', 'goalTheta'].forEach((id) => {
    if ($(id)) $(id).value = '0';
  });
  setText('slamPlanState', '未生成');
  setText('mapPointerState', '已清空导航点和规划路线');
}

async function stopNavStack() {
  await post('/api/process/stop', { name: 'nav_dwa', force_external: true });
  await post('/api/process/stop', { name: 'nav_teb', force_external: true });
  await post('/api/nav/clear');
  clearNavigationOverlay();
  setPoseMode('goal');
  setNavState('导航已停止，初始位置、目标位置和规划路线已清空');
  await refreshSlam();
}

async function startNativeMapWorkflow() {
  setText('saveMapState', '正在启动原生建图流程...');
  await post('/api/process/stop', { name: 'nav_dwa', force_external: true });
  await post('/api/process/stop', { name: 'nav_teb', force_external: true });
  await post('/api/process/stop', { name: 'rviz_nav', force_external: true });
  await post('/api/process/start', { name: 'bringup' });
  await sleepMs(1200);
  await post('/api/process/start', { name: 'slam' });
  await sleepMs(800);
  const res = await post('/api/process/start', { name: 'rviz_map' });
  setText('saveMapState', res.ok ? '原生建图 RViz 已启动，请在页面上方 RViz 工具栏操作' : (res.error || '原生建图启动失败'));
  await refreshSlam();
}

async function startNativeNavWorkflow() {
  if (!selectedSlamMap) {
    setNavState('请先选择一张地图');
    return;
  }
  setNavState('正在启动原生导航流程...');
  await post('/api/process/stop', { name: 'slam', force_external: true });
  await post('/api/process/stop', { name: 'rviz_map', force_external: true });
  await post('/api/process/start', { name: 'bringup' });
  await waitForBringupReady();
  await post('/api/process/stop', { name: 'nav_teb', force_external: true });
  await post('/api/process/start', { name: 'nav_dwa' });
  await sleepMs(1000);
  const res = await post('/api/process/start', { name: 'rviz_nav' });
  setNavState(res.ok ? '原生导航 RViz 已启动，请在页面上方 RViz 中设置初始位姿和目标点' : (res.error || '原生导航启动失败'));
  await refreshSlam();
}

function initRvizEmbed() {
  const frame = $('rvizFrame');
  if (!frame) return;
  const host = window.location.hostname || '192.168.43.84';
  const protocol = window.location.protocol || 'http:';
  const src = `${protocol}//${host}:6080/vnc.html?host=${encodeURIComponent(host)}&port=6080&autoconnect=true&resize=scale&shared=true`;
  if (frame.src !== src) frame.src = src;
  setText('rvizEmbedState', `RViz ${host}:6080`);
}

function initSlamNavigationWorkflow() {
  if ($('refreshMapList')) $('refreshMapList').onclick = loadSlamMaps;
  if ($('saveNamedMap')) $('saveNamedMap').onclick = saveNamedMap;
  if ($('startNavStack')) $('startNavStack').onclick = startNavStack;
  if ($('startNativeMap')) $('startNativeMap').onclick = startNativeMapWorkflow;
  if ($('startNativeNav')) $('startNativeNav').onclick = startNativeNavWorkflow;
  if ($('stopNavStack')) $('stopNavStack').onclick = stopNavStack;
  if ($('sendInitialPose')) $('sendInitialPose').onclick = applyInitialPose;
  if ($('startNavigationGoal')) $('startNavigationGoal').onclick = startNavigationGoal;
  document.querySelectorAll('[data-slam-pose-mode]').forEach((btn) => {
    btn.onclick = () => setPoseMode(btn.dataset.slamPoseMode);
  });
  ['initialTheta', 'goalTheta'].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.oninput = () => {
      if (id === 'initialTheta') slamInitialPose = poseFromFields('initial');
      if (id === 'goalTheta') slamGoalPose = poseFromFields('goal');
      refreshSlam().catch(() => {});
    };
  });
  bindSlamMapClick();
  setPoseMode('goal');
  loadSlamMaps();
}

function initSlam() {
  initRvizEmbed();
  initSlamNavigationWorkflow();
  initSlamDriveControls();
  if (!new URLSearchParams(window.location.search).has('no_auto')) {
    setTimeout(async () => {
      try {
        const status = await getJson('/api/process/status');
        const navRunning = Boolean(status.nav_dwa?.running || status.nav_teb?.running || status.rviz_nav?.running);
        if (!navRunning) await startNativeMapWorkflow();
      } catch (err) {
        setText('saveMapState', `自动启动原生建图失败：${err.message || err}`);
      }
    }, 800);
  }
  document.querySelectorAll('[data-start]').forEach((btn) => {
    btn.onclick = async () => {
      await post('/api/process/start', { name: btn.dataset.start });
      await refreshSlam();
    };
  });
  document.querySelectorAll('[data-stop]').forEach((btn) => {
    btn.onclick = async () => {
      await post('/api/process/stop', { name: btn.dataset.stop });
      await refreshSlam();
    };
  });
  refreshSlam();
  setInterval(refreshSlam, 1200);
}


let selectedBjtuFeature = null;
let latestBjtuStatus = null;

async function bjtuPost(path, data) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data || {})
  });
  return res.json();
}

function poseText(pose) {
  if (!pose) return '--';
  const age = Number.isFinite(pose.age) ? `${pose.age.toFixed(1)}s` : '--';
  return `${fmt(pose.x)}, ${fmt(pose.y)}, ${fmt(pose.z)} / ${age}`;
}

function renderBjtuFeatures(status) {
  const root = status.root ? status.root : '脚本未部署到 web 运行环境';
  setText('bjtuRoot', root);
  const box = $('bjtuFeatures');
  box.innerHTML = '';
  Object.entries(status.features || {}).forEach(([name, feature]) => {
    const row = document.createElement('div');
    row.className = 'feature-row';
    const state = feature.running ? '运行中' : (feature.available ? '可启动' : '不可用');
    row.innerHTML = `
      <div class="feature-copy">
        <strong>${feature.title}</strong>
        <span>${feature.description}</span>
        <small>${state} · ${feature.mode} · ${feature.start_script || '未找到脚本'}</small>
      </div>
      <div class="feature-actions">
        <button data-bjtu-start="${name}" ${feature.available ? '' : 'disabled'}>启动</button>
        <button data-bjtu-stop="${name}" ${feature.stop_available ? '' : 'disabled'}>停止</button>
        <button data-bjtu-log="${name}">日志</button>
      </div>`;
    box.appendChild(row);
  });
  box.querySelectorAll('[data-bjtu-start]').forEach((btn) => {
    btn.onclick = async () => {
      selectedBjtuFeature = btn.dataset.bjtuStart;
      setText('bjtuLogName', selectedBjtuFeature);
      setText('bjtuLog', '启动中...');
      const res = await bjtuPost('/api/bjtu/start', { name: selectedBjtuFeature });
      if (res.ok) {
        const msg = res.running ? '已经在运行' : '启动成功';
        const logLine = res.log ? `
日志：${res.log}` : '';
        setText('bjtuLog', `${msg}：${selectedBjtuFeature}${logLine}`);
      } else {
        setText('bjtuLog', res.error || JSON.stringify(res, null, 2));
      }
      await refreshBjtu();
    };
  });
  box.querySelectorAll('[data-bjtu-stop]').forEach((btn) => {
    btn.onclick = async () => {
      selectedBjtuFeature = btn.dataset.bjtuStop;
      setText('bjtuLogName', selectedBjtuFeature);
      setText('bjtuLog', '停止中...');
      const res = await bjtuPost('/api/bjtu/stop', { name: selectedBjtuFeature });
      if (res.ok) {
        setText('bjtuLog', `停止成功：${selectedBjtuFeature}`);
      } else {
        setText('bjtuLog', res.error || JSON.stringify(res, null, 2));
      }
      await refreshBjtu();
    };
  });
  box.querySelectorAll('[data-bjtu-log]').forEach((btn) => {
    btn.onclick = () => {
      selectedBjtuFeature = btn.dataset.bjtuLog;
      refreshBjtuLog(selectedBjtuFeature);
    };
  });
}

function renderBjtuPlanned(planned) {
  const box = $('bjtuPlanned');
  box.innerHTML = '';
  Object.entries(planned || {}).forEach(([name, item]) => {
    const row = document.createElement('div');
    row.className = 'planned-row';
    row.innerHTML = `<strong>${item.title}</strong><span>${item.description}</span><small>${item.state}</small>`;
    box.appendChild(row);
  });
}

async function refreshBjtu() {
  const [featureRes, carRes] = await Promise.all([
    fetch('/api/bjtu/status', { cache: 'no-store' }),
    fetch('/api/status', { cache: 'no-store' })
  ]);
  const status = await featureRes.json();
  const car = await carRes.json();
  latestBjtuStatus = status;
  renderBjtuFeatures(status);
  renderBjtuPlanned(status.planned);
  const bjtu = car.bjtu || {};
  setText('stopPoseBase', poseText(bjtu.stop_poses?.base));
  setText('stopPoseMap', poseText(bjtu.stop_poses?.map));
  setText('bjtuChatter', bjtu.chatter ? `${bjtu.chatter} (${fmt(bjtu.chatter_age, 1)}s)` : '--');
  const hasStop = Boolean(bjtu.stop_poses?.base || bjtu.stop_poses?.map);
  setText('bjtuStopState', hasStop ? '已接收' : '等待');
  if (selectedBjtuFeature) refreshBjtuLog(selectedBjtuFeature);
}

async function refreshBjtuLog(name) {
  if (!name) return;
  setText('bjtuLogName', name);
  const res = await fetch(`/api/bjtu/log?name=${encodeURIComponent(name)}&lines=180`, { cache: 'no-store' });
  const data = await res.json();
  setText('bjtuLog', data.text || data.error || '--');
}

async function refreshDetection() {
  const host = $('detectHost')?.value?.trim() || '127.0.0.1';
  const port = $('detectPort')?.value || '5002';
  setText('detectState', '读取中');
  try {
    const res = await fetch(`/api/bjtu/detections?host=${encodeURIComponent(host)}&port=${encodeURIComponent(port)}`, { cache: 'no-store' });
    const data = await res.json();
    setText('detectState', data.ok ? `${(data.detections || []).length} 个目标` : '未连接');
    setText('detectRaw', JSON.stringify(data.ok ? data.raw : data, null, 2));
  } catch (err) {
    setText('detectState', '失败');
    setText('detectRaw', err.message || String(err));
  }
}

function initBjtu() {
  if ($('bjtuRefresh')) $('bjtuRefresh').onclick = refreshBjtu;
  if ($('refreshDetection')) $('refreshDetection').onclick = refreshDetection;
  refreshBjtu();
  setInterval(refreshBjtu, 2500);
}

const page = document.body.dataset.page;
if (page === 'power') initPower();
if (page === 'slam') initSlam();
if (page === 'sensors') initSensors();
if (page === 'bjtu') initBjtu();
