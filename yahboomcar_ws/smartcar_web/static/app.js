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

function bindHold(button) {
  const kind = button.dataset.move;
  let timer = null;
  let activePointer = null;
  const send = () => move(kind).catch(() => {});
  const start = (e) => {
    e.preventDefault();
    if (activePointer !== null || timer) return;
    activePointer = e.pointerId ?? 'mouse';
    if (button.setPointerCapture && e.pointerId != null) button.setPointerCapture(e.pointerId);
    send();
    if (kind !== 'stop') timer = setInterval(send, 150);
  };
  const stop = (e) => {
    if (activePointer !== null && e?.pointerId != null && e.pointerId !== activePointer) return;
    if (timer) clearInterval(timer);
    timer = null;
    activePointer = null;
    if (kind !== 'stop') move('stop').catch(() => {});
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

function init() {
  document.querySelectorAll('[data-move]').forEach(bindHold);
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
