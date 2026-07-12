const $ = (id) => document.getElementById(id);

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
  ctx.strokeStyle = '#64748b';
  ctx.strokeRect(mapView.x, mapView.y, mapView.width, mapView.height);
  drawScan(ctx, viz.scan, viz.pose);
  drawPlan(ctx, viz.plan);
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
  drawDetailMap(viz);
}

function initPower() {
  $('recoverBringup').onclick = async () => {
    await post('/api/process/start', { name: 'bringup' });
    await refreshPower();
  };
  refreshPower();
  setInterval(refreshPower, 1000);
}

function initSlam() {
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

const page = document.body.dataset.page;
if (page === 'power') initPower();
if (page === 'slam') initSlam();
