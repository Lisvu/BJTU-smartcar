const $ = (id) => document.getElementById(id);

let latestViz = null;
let mapView = null;
let activePage = 'control';
let poseMode = 'goal';
let initialPose = null;
let goalPose = null;
let navigationActive = false;
let arrivalNotified = false;
let toastTimer = null;
const activeTimers = new Set();
const pendingStopTimers = new Set();

async function api(path, data) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data || {})
  });
  return res.json();
}

async function getJson(path) {
  const res = await fetch(path, { cache: 'no-store' });
  return res.json();
}

function notify(message, type = 'ok') {
  const toast = $('toast');
  toast.textContent = message;
  toast.className = `toast show ${type}`;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.className = 'toast';
  }, 3200);
}

async function runAction(label, fn) {
  try {
    const res = await fn();
    if (res && res.ok === false) {
      notify(`${label}失败：${res.error || '未知错误'}`, 'error');
      return res;
    }
    notify(`${label}成功`);
    return res;
  } catch (err) {
    notify(`${label}失败：${err.message || err}`, 'error');
    return null;
  }
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

async function stopMotion() {
  activeTimers.forEach((timer) => clearInterval(timer));
  pendingStopTimers.forEach((timer) => clearTimeout(timer));
  activeTimers.clear();
  pendingStopTimers.clear();
  return api('/api/stop');
}

function bindHold(button) {
  const kind = button.dataset.move;
  let timer = null;
  let startedAt = 0;
  let stopTimer = null;
  const start = async (e) => {
    e.preventDefault();
    if (kind === 'stop') {
      await stopMotion();
      return;
    }
    if (stopTimer) clearTimeout(stopTimer);
    pendingStopTimers.delete(stopTimer);
    startedAt = Date.now();
    await move(kind);
    timer = setInterval(() => move(kind), 180);
    activeTimers.add(timer);
  };
  const stop = async () => {
    if (timer) {
      clearInterval(timer);
      activeTimers.delete(timer);
    }
    timer = null;
    if (kind === 'stop') return;
    const elapsed = Date.now() - startedAt;
    const delay = Math.max(0, 800 - elapsed);
    stopTimer = setTimeout(() => {
      pendingStopTimers.delete(stopTimer);
      move('stop');
    }, delay);
    pendingStopTimers.add(stopTimer);
  };
  button.addEventListener('mousedown', start);
  button.addEventListener('touchstart', start, { passive: false });
  button.addEventListener('mouseup', stop);
  button.addEventListener('mouseleave', stop);
  button.addEventListener('touchend', stop);
  button.addEventListener('touchcancel', stop);
}

async function refreshStatus() {
  const s = await getJson('/api/status');
  $('voltage').textContent = s.voltage == null ? '--' : `${s.voltage.toFixed(2)} V`;
  $('frontDistance').textContent = s.front_distance == null ? '--' : `${s.front_distance.toFixed(2)} m`;
  $('guardState').textContent = s.obstacle_guard ? `ON (${s.stop_distance.toFixed(2)}m)` : 'OFF';
  $('mapState').textContent = s.map ? `${s.map.width}x${s.map.height}` : '未接收';
  $('planLen').textContent = s.plan_len ?? '--';
  $('poseState').textContent = s.pose ? `${fmt(s.pose.x)}, ${fmt(s.pose.y)}, ${fmt(s.pose.theta)}` : '--';
  $('cameraState').textContent = s.camera?.available ? (s.camera.open ? '已连接' : '待打开') : '不可用';
  $('warning').textContent = s.obstacle ? '前方障碍，前进命令会被拦截' : '';
  updateProcesses(s.processes || {});
  checkNavigationArrival(s.pose);
}

async function refreshViz() {
  if (activePage !== 'slam') return;
  latestViz = await getJson('/api/viz');
  drawMap(latestViz);
}

async function refreshMaps() {
  const data = await getJson('/api/maps');
  const list = $('mapList');
  list.innerHTML = '';
  if (!data.maps || data.maps.length === 0) {
    list.innerHTML = '<div class="empty-state">暂无已保存地图</div>';
    return;
  }
  data.maps.forEach((item, index) => {
    const button = document.createElement('button');
    button.className = 'map-item';
    button.type = 'button';
    button.innerHTML = `<strong>${item.name}</strong><span>${item.updated_text}</span><small>${item.yaml}</small>`;
    button.onclick = async () => {
      document.querySelectorAll('.map-item').forEach((el) => el.classList.remove('selected'));
      button.classList.add('selected');
      await api('/api/maps/select', { yaml: item.yaml });
      $('selectedMap').textContent = `导航地图：${item.name}`;
      notify(`已选择地图：${item.name}`);
    };
    if ((data.selected && data.selected === item.yaml) || (!data.selected && index === 0)) {
      document.querySelectorAll('.map-item').forEach((el) => el.classList.remove('selected'));
      button.classList.add('selected');
      api('/api/maps/select', { yaml: item.yaml });
      $('selectedMap').textContent = `导航地图：${item.name}`;
    }
    list.appendChild(button);
  });
}

function updateProcesses(processes) {
  const list = $('processList');
  if (!list) return;
  list.innerHTML = '';
  const labels = {
    bringup: '底盘/雷达',
    camera: '相机',
    slam: '建图',
    save_map: '保存地图',
    nav_dwa: 'DWA 导航',
    nav_teb: 'TEB 导航',
    mapping_keyboard: '键盘控制',
  };
  Object.keys(labels).forEach((name) => {
    const item = processes[name] || { running: false };
    const row = document.createElement('div');
    row.className = 'process-row';
    row.innerHTML = `<span>${labels[name]}</span><strong class="${item.running ? 'running' : ''}">${item.running ? '运行中' : '未启动'}</strong>`;
    list.appendChild(row);
  });
}

function switchPage(name) {
  activePage = name;
  document.querySelectorAll('.page').forEach((page) => {
    page.classList.toggle('active', page.id === `page-${name}`);
  });
  document.querySelectorAll('[data-page-target]').forEach((button) => {
    button.classList.toggle('active', button.dataset.pageTarget === name);
  });
  if (name === 'slam') {
    refreshViz();
    refreshMaps();
  }
}

function computeMapBounds(map) {
  let minX = map.width;
  let minY = map.height;
  let maxX = -1;
  let maxY = -1;
  for (let y = 0; y < map.height; y += 1) {
    for (let x = 0; x < map.width; x += 1) {
      const v = map.data[y * map.width + x];
      if (v >= 0) {
        minX = Math.min(minX, x);
        minY = Math.min(minY, y);
        maxX = Math.max(maxX, x);
        maxY = Math.max(maxY, y);
      }
    }
  }
  if (maxX < minX || maxY < minY) {
    return { x: 0, y: 0, width: map.width, height: map.height };
  }
  const pad = 24;
  minX = Math.max(0, minX - pad);
  minY = Math.max(0, minY - pad);
  maxX = Math.min(map.width - 1, maxX + pad);
  maxY = Math.min(map.height - 1, maxY + pad);
  return { x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1 };
}

function setPixel(image, width, height, x, y, r, g, b, a = 255) {
  if (x < 0 || y < 0 || x >= width || y >= height) return;
  const dst = (y * width + x) * 4;
  image.data[dst] = r;
  image.data[dst + 1] = g;
  image.data[dst + 2] = b;
  image.data[dst + 3] = a;
}

function makeMapImage(map, bounds) {
  const canvas = document.createElement('canvas');
  canvas.width = bounds.width;
  canvas.height = bounds.height;
  const ctx = canvas.getContext('2d');
  const image = ctx.createImageData(bounds.width, bounds.height);
  for (let y = 0; y < bounds.height; y += 1) {
    for (let x = 0; x < bounds.width; x += 1) {
      const mapX = bounds.x + x;
      const mapY = bounds.y + bounds.height - 1 - y;
      const src = mapY * map.width + mapX;
      const dst = (y * bounds.width + x) * 4;
      const v = map.data[src];
      let r = 238;
      let g = 241;
      let b = 245;
      if (v < 0) {
        r = 210; g = 216; b = 224;
      } else if (v > 50) {
        r = 18; g = 24; b = 33;
      } else if (v > 0) {
        const shade = Math.max(55, 230 - v * 2);
        r = shade; g = shade; b = shade;
      }
      image.data[dst] = r;
      image.data[dst + 1] = g;
      image.data[dst + 2] = b;
      image.data[dst + 3] = 255;
    }
  }

  for (let y = 0; y < bounds.height; y += 1) {
    for (let x = 0; x < bounds.width; x += 1) {
      const mapX = bounds.x + x;
      const mapY = bounds.y + bounds.height - 1 - y;
      const v = map.data[mapY * map.width + mapX];
      if (v > 50) {
        for (let oy = -1; oy <= 1; oy += 1) {
          for (let ox = -1; ox <= 1; ox += 1) {
            setPixel(image, bounds.width, bounds.height, x + ox, y + oy, 10, 15, 23);
          }
        }
      }
    }
  }

  ctx.putImageData(image, 0, 0);
  return canvas;
}

function updateMapView(canvas, map) {
  const bounds = computeMapBounds(map);
  const pad = 26;
  const scale = Math.min(
    (canvas.width - pad * 2) / bounds.width,
    (canvas.height - pad * 2) / bounds.height
  );
  const drawWidth = bounds.width * scale;
  const drawHeight = bounds.height * scale;
  mapView = {
    x: (canvas.width - drawWidth) / 2,
    y: (canvas.height - drawHeight) / 2,
    width: drawWidth,
    height: drawHeight,
    scale,
    map,
    bounds,
    image: makeMapImage(map, bounds),
  };
}

function drawMetricGrid(ctx) {
  if (!mapView) return;
  const { map, bounds } = mapView;
  const meters = 1;
  const minX = map.origin.x + bounds.x * map.resolution;
  const maxX = map.origin.x + (bounds.x + bounds.width) * map.resolution;
  const minY = map.origin.y + bounds.y * map.resolution;
  const maxY = map.origin.y + (bounds.y + bounds.height) * map.resolution;
  ctx.save();
  ctx.strokeStyle = 'rgba(59, 130, 246, 0.10)';
  ctx.lineWidth = 1;
  ctx.fillStyle = 'rgba(30, 41, 59, 0.72)';
  ctx.font = '11px system-ui';
  for (let x = Math.ceil(minX); x <= maxX; x += meters) {
    const a = worldToCanvas(x, minY);
    const b = worldToCanvas(x, maxY);
    if (!a || !b) continue;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
    if (x % 2 === 0) ctx.fillText(`${x}m`, a.x + 3, mapView.y + mapView.height - 6);
  }
  for (let y = Math.ceil(minY); y <= maxY; y += meters) {
    const a = worldToCanvas(minX, y);
    const b = worldToCanvas(maxX, y);
    if (!a || !b) continue;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
    if (y % 2 === 0) ctx.fillText(`${y}m`, mapView.x + 6, a.y - 3);
  }
  ctx.restore();
}

function worldToCanvas(x, y) {
  if (!mapView) return null;
  const { map, bounds } = mapView;
  const mx = (x - map.origin.x) / map.resolution;
  const my = (y - map.origin.y) / map.resolution;
  return {
    x: mapView.x + (mx - bounds.x) * mapView.scale,
    y: mapView.y + (bounds.y + bounds.height - my) * mapView.scale,
  };
}

function canvasToWorld(x, y) {
  if (!mapView) return null;
  const { map, bounds } = mapView;
  const mx = bounds.x + (x - mapView.x) / mapView.scale;
  const my = bounds.y + bounds.height - (y - mapView.y) / mapView.scale;
  return {
    x: map.origin.x + mx * map.resolution,
    y: map.origin.y + my * map.resolution,
  };
}

function drawRobot(ctx, pose) {
  const p = worldToCanvas(pose.x, pose.y);
  if (!p) return;
  drawPoseMarker(ctx, p.x, p.y, pose.theta, '#fb7185');
}

function drawPoseMarker(ctx, x, y, theta, color) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(-theta);
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(14, 0);
  ctx.lineTo(-10, -8);
  ctx.lineTo(-7, 0);
  ctx.lineTo(-10, 8);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

function drawStoredPose(ctx, pose, color) {
  if (!pose) return;
  const p = worldToCanvas(pose.x, pose.y);
  if (!p) return;
  drawPoseMarker(ctx, p.x, p.y, pose.theta, color);
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
  drawMetricGrid(ctx);
  ctx.strokeStyle = '#64748b';
  ctx.strokeRect(mapView.x, mapView.y, mapView.width, mapView.height);

  drawScan(ctx, viz.scan, viz.pose);
  drawPolyline(ctx, viz.plan, '#fbbf24', 3);
  if (viz.pose) drawRobot(ctx, viz.pose);
  drawStoredPose(ctx, initialPose, '#a7f3d0');
  drawStoredPose(ctx, goalPose, '#f97316');

  const age = viz.ages?.map == null ? '--' : `${viz.ages.map.toFixed(1)}s`;
  $('vizState').textContent = `地图 ${viz.map.width}x${viz.map.height} / ${age} / ${viz.map.resolution.toFixed(2)}m`;
}

function bindMapClick() {
  $('mapCanvas').addEventListener('click', (event) => {
    if (!mapView) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const scaleX = event.currentTarget.width / rect.width;
    const scaleY = event.currentTarget.height / rect.height;
    const world = canvasToWorld((event.clientX - rect.left) * scaleX, (event.clientY - rect.top) * scaleY);
    if (!world) return;
    if (poseMode === 'initial') {
      $('initialX').value = world.x.toFixed(2);
      $('initialY').value = world.y.toFixed(2);
      initialPose = {
        x: Number($('initialX').value),
        y: Number($('initialY').value),
        theta: Number($('initialTheta').value),
      };
    } else {
      $('goalX').value = world.x.toFixed(2);
      $('goalY').value = world.y.toFixed(2);
      goalPose = {
        x: Number($('goalX').value),
        y: Number($('goalY').value),
        theta: Number($('goalTheta').value),
      };
    }
    if (latestViz) drawMap(latestViz);
  });
}

function setPoseMode(mode) {
  poseMode = mode;
  $('poseModeState').textContent = mode === 'initial'
    ? '当前点击地图：设置初始位姿'
    : '当前点击地图：设置目标位姿';
  document.querySelectorAll('[data-pose-mode]').forEach((button) => {
    button.classList.toggle('active', button.dataset.poseMode === mode);
  });
}

function readInitialPose() {
  initialPose = {
    x: Number($('initialX').value),
    y: Number($('initialY').value),
    theta: Number($('initialTheta').value),
  };
  return initialPose;
}

function readGoalPose() {
  goalPose = {
    x: Number($('goalX').value),
    y: Number($('goalY').value),
    theta: Number($('goalTheta').value),
  };
  return goalPose;
}

function checkNavigationArrival(pose) {
  if (!navigationActive || arrivalNotified || !pose || !goalPose) return;
  const dx = pose.x - goalPose.x;
  const dy = pose.y - goalPose.y;
  const distance = Math.hypot(dx, dy);
  const headingError = Math.abs(Math.atan2(Math.sin(pose.theta - goalPose.theta), Math.cos(pose.theta - goalPose.theta)));
  if (distance <= 0.25 && headingError <= 0.7) {
    arrivalNotified = true;
    navigationActive = false;
    notify(`已到达目标点（误差 ${distance.toFixed(2)}m）`);
  }
}

function init() {
  document.querySelectorAll('[data-page-target]').forEach((button) => {
    button.onclick = () => switchPage(button.dataset.pageTarget);
  });
  document.querySelectorAll('[data-pose-mode]').forEach((button) => {
    button.onclick = () => setPoseMode(button.dataset.poseMode);
  });
  document.querySelectorAll('[data-move]').forEach(bindHold);
  $('linearSpeed').oninput = () => $('linearVal').textContent = $('linearSpeed').value;
  $('angularSpeed').oninput = () => $('angularVal').textContent = $('angularSpeed').value;
  $('applyGuard').onclick = () => runAction('应用避障设置', () => api('/api/guard', {
    enabled: $('guardEnabled').checked,
    stop_distance: Number($('stopDistance').value)
  }));
  document.querySelectorAll('[data-start]').forEach((btn) => {
    btn.onclick = () => runAction(btn.textContent.trim(), () => api('/api/process/start', { name: btn.dataset.start }));
  });
  document.querySelectorAll('[data-stop]').forEach((btn) => {
    btn.onclick = () => runAction(btn.textContent.trim(), () => api('/api/process/stop', {
      name: btn.dataset.stop,
      force_external: btn.dataset.forceExternal === 'true'
    }));
  });
  $('startMapping').onclick = async () => {
    const res = await runAction('启动建图', () => api('/api/mapping/start'));
    if (res && res.ok !== false) {
      latestViz = null;
      mapView = null;
      refreshViz();
    }
  };
  $('sendInitialPose').onclick = async () => {
    const pose = readInitialPose();
    await runAction('设置初始位姿', () => api('/api/nav/initial_pose', pose));
    if (latestViz) drawMap(latestViz);
  };
  $('setGoalPose').onclick = () => {
    readGoalPose();
    if (latestViz) drawMap(latestViz);
    notify('目标位姿已设置');
  };
  $('startNavStack').onclick = async () => {
    const algorithm = $('navAlgorithm').value;
    const other = algorithm === 'nav_dwa' ? 'nav_teb' : 'nav_dwa';
    await api('/api/process/stop', { name: other });
    await runAction(`启动${algorithm === 'nav_dwa' ? 'DWA' : 'TEB'}导航`, () => api('/api/process/start', { name: algorithm }));
  };
  $('startRobotNav').onclick = async () => {
    const pose = readGoalPose();
    const res = await runAction('启动小车前往目标', () => api('/api/nav/goal', pose));
    if (res && res.ok !== false) {
      navigationActive = true;
      arrivalNotified = false;
    }
  };
  $('stopNavStack').onclick = async () => {
    await runAction('停止DWA导航', () => api('/api/process/stop', { name: 'nav_dwa' }));
    await runAction('停止TEB导航', () => api('/api/process/stop', { name: 'nav_teb' }));
    await runAction('停止小车', stopMotion);
    navigationActive = false;
  };
  $('saveMap').onclick = async () => {
    await runAction('保存地图', () => api('/api/process/start', { name: 'save_map' }));
    setTimeout(refreshMaps, 2500);
  };
  $('clearMapping').onclick = async () => {
    const res = await runAction('清空本次建图', () => api('/api/mapping/clear'));
    if (res && res.ok !== false) {
      latestViz = null;
      mapView = null;
      drawFallback($('mapCanvas').getContext('2d'), $('mapCanvas'));
      $('vizState').textContent = '无地图';
    }
  };
  $('refreshMaps').onclick = () => runAction('刷新地图列表', refreshMaps);
  bindMapClick();
  setPoseMode('goal');
  setInterval(refreshStatus, 700);
  setInterval(refreshViz, 1200);
  setInterval(refreshMaps, 8000);
  refreshStatus();
  refreshMaps();
}

init();
