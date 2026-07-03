const SCORE_LABELS = {
  fireRiskCoverage: "산불 위험 커버리지",
  smokeVisibility: "연기 가시성",
  opticalReliability: "광학 신뢰도(역광 포함)",
  maintenanceAccess: "유지보수 접근성",
  protectedTarget: "보호 대상 중요도",
  communication: "통신 가능성",
  powerStability: "전력 안정성",
  falseAlarmRisk: "오탐 위험",
  ecologicalRisk: "생태 교란 위험",
};

const SOURCE_LABELS = {
  fireHistory: "산림청 산불통계",
  asosWind: "기상청 ASOS",
  airQuality: "에어코리아 대기질",
  vworldWfs: "VWorld WFS",
  dem: "국토정보 DEM",
};

const DIRECTIONS_16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];

let appState = {
  region: "uiseong",
  data: null,
  map: null,
  layerGroups: {},
  hour: "14",
  topN: 80,
  selectedCameraId: null,
  visibility: { ignition: true, smoke: true, camera: true, los: false },
  simMode: false,
  simSeason: "봄",
  simLoading: false,
};

async function init() {
  setupMap();
  bindControls();
  await loadRegion(appState.region);
}

function setupMap() {
  appState.map = L.map("map", { zoomControl: false, preferCanvas: true }).setView([36.4, 128.6], 10);
  L.control.zoom({ position: "bottomright" }).addTo(appState.map);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
  }).addTo(appState.map);

  appState.layerGroups = {
    ignition: L.layerGroup().addTo(appState.map),
    smoke: L.layerGroup().addTo(appState.map),
    camera: L.layerGroup().addTo(appState.map),
    los: L.layerGroup().addTo(appState.map),
    sim: L.layerGroup().addTo(appState.map),
  };

  appState.map.on("click", (e) => {
    if (!appState.simMode || appState.simLoading) return;
    runSimulation(e.latlng.lng, e.latlng.lat);
  });
}

function bindControls() {
  document.getElementById("region-control").addEventListener("click", (e) => {
    const btn = e.target.closest(".segment");
    if (!btn) return;
    setActiveSegment("region-control", btn);
    loadRegion(btn.dataset.region);
  });

  document.getElementById("hour-control").addEventListener("click", (e) => {
    const btn = e.target.closest(".segment");
    if (!btn) return;
    setActiveSegment("hour-control", btn);
    appState.hour = btn.dataset.hour;
    renderRankingList();
    if (appState.selectedCameraId) showDetail(appState.selectedCameraId);
  });

  document.querySelectorAll("#layer-toggles input").forEach((input) => {
    input.addEventListener("change", () => {
      appState.visibility[input.dataset.layer] = input.checked;
      applyLayerVisibility();
    });
  });

  const slider = document.getElementById("top-n-slider");
  slider.addEventListener("input", () => {
    appState.topN = Number(slider.value);
    document.getElementById("top-n-label").textContent = `상위 ${appState.topN}개 표시`;
    renderCameraLayer();
    renderRankingList();
  });

  document.getElementById("sim-season-control").addEventListener("click", (e) => {
    const btn = e.target.closest(".segment");
    if (!btn) return;
    setActiveSegment("sim-season-control", btn);
    appState.simSeason = btn.dataset.season;
  });

  const simToggle = document.getElementById("sim-toggle-button");
  simToggle.addEventListener("click", () => {
    appState.simMode = !appState.simMode;
    simToggle.classList.toggle("is-active", appState.simMode);
    simToggle.textContent = appState.simMode
      ? "시뮬레이션 모드 켜짐 · 지도를 클릭하세요"
      : "지도 클릭으로 발화 지점 지정";
    document.getElementById("sim-status").textContent = appState.simMode
      ? "지도 위 임의의 지점을 클릭하면 그 자리에서 시뮬레이션을 실행합니다."
      : "";
  });
}

function setActiveSegment(containerId, activeBtn) {
  document.querySelectorAll(`#${containerId} .segment`).forEach((b) => b.classList.remove("is-active"));
  activeBtn.classList.add("is-active");
}

function displayScore(camera, key) {
  if (key === "opticalReliability") {
    const v = camera.scores.opticalReliabilityByHour[appState.hour];
    return v === undefined ? camera.scores.opticalReliabilityBaseByHour[appState.hour] ?? 60 : v;
  }
  const raw = camera.scores[key];
  if (raw && typeof raw === "object") return raw.value;
  return raw;
}

function isApprox(camera, key) {
  const raw = camera.scores[key];
  return !!(raw && typeof raw === "object" && raw.approx);
}

async function loadRegion(regionKey) {
  appState.region = regionKey;
  appState.selectedCameraId = null;
  document.getElementById("detail-panel-wrap").style.display = "none";
  document.getElementById("sim-result-wrap").style.display = "none";
  appState.layerGroups.sim.clearLayers();
  const res = await fetch(`./data/${regionKey}.json`);
  const data = await res.json();
  appState.data = data;

  document.getElementById("map-title").textContent = `${data.regionNameKo} · 센서 배치 후보`;
  document.getElementById("region-note").textContent = data.note || "";
  document.getElementById("top-n-label").textContent = `상위 ${appState.topN}개 표시`;

  renderHeroStats(data);
  renderSourceStatus(data);
  renderWindRose(data);
  renderIgnitionLayer();
  renderSmokeLayer();
  renderCameraLayer();
  renderRankingList();
  applyLayerVisibility();
  fitMapToBounds(data.bbox);
}

function fitMapToBounds(bbox) {
  const [west, south, east, north] = bbox;
  appState.map.fitBounds([[south, west], [north, east]], { padding: [20, 20] });
}

function renderHeroStats(data) {
  const topScore = data.cameraCandidates[0]?.scores.final ?? 0;
  const cards = [
    { value: data.ignitionCandidates.length, label: "발화 후보지" },
    { value: data.cameraCandidates.length, label: "카메라 후보지" },
    { value: topScore.toFixed(1), label: "최고 배치 점수" },
    { value: `${data.wind.avgWindSpeedMs ?? "-"} m/s`, label: "봄철 평균 풍속" },
  ];
  document.getElementById("hero-stats").innerHTML = cards
    .map((c) => `<article class="stat-card"><strong>${c.value}</strong><span>${c.label}</span></article>`)
    .join("");

  document.getElementById("map-summary").textContent =
    `${data.fireHistorySummary.totalMatched}건의 과거 산불 통계(${data.fireHistorySummary.yearsCovered[0] || "-"}~${data.fireHistorySummary.yearsCovered.at(-1) || "-"})를 계절 가중치로 반영했습니다.`;
}

function renderSourceStatus(data) {
  const rows = Object.entries(data.sourceStatus).map(([key, status]) => {
    const label = SOURCE_LABELS[key] || key;
    const cls = status === "live" ? "live" : "fallback";
    const text = status === "live" ? "LIVE" : status.toUpperCase();
    return `<div class="source-status-row"><span>${label}</span><span class="source-badge ${cls}">${text}</span></div>`;
  });
  document.getElementById("source-status").innerHTML = rows.join("");
}

function renderWindRose(data) {
  const rose = data.wind.windRose || {};
  const max = Math.max(1, ...Object.values(rose));
  const cx = 90, cy = 90, maxR = 70;
  const points = DIRECTIONS_16.map((dir, i) => {
    const angle = (Math.PI * 2 * i) / 16 - Math.PI / 2;
    const r = (rose[dir] || 0) / max * maxR;
    return [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
  });
  const path = points.map((p) => p.join(",")).join(" ");
  const rings = [0.33, 0.66, 1].map((f) =>
    `<circle cx="${cx}" cy="${cy}" r="${maxR * f}" fill="none" stroke="rgba(126,145,158,0.2)" stroke-width="1"/>`
  ).join("");
  const labels = ["N", "E", "S", "W"].map((label, i) => {
    const angle = (Math.PI * 2 * i) / 4 - Math.PI / 2;
    const x = cx + (maxR + 12) * Math.cos(angle);
    const y = cy + (maxR + 12) * Math.sin(angle);
    return `<text x="${x}" y="${y}" fill="#97a3ad" font-size="10" text-anchor="middle" dominant-baseline="middle">${label}</text>`;
  }).join("");

  document.getElementById("wind-rose").innerHTML = `
    <svg viewBox="0 0 180 180">
      ${rings}
      <polygon points="${path}" fill="rgba(217,130,43,0.35)" stroke="#d9822b" stroke-width="1.5"/>
      ${labels}
    </svg>`;

  const top3 = Object.entries(rose).sort((a, b) => b[1] - a[1]).slice(0, 3)
    .map(([dir, pct]) => `${dir} ${pct}%`).join(" · ");
  document.getElementById("wind-summary").textContent =
    `주풍향: ${top3} (평균 풍속 ${data.wind.avgWindSpeedMs ?? "-"} m/s, 무풍 ${data.wind.calmPct ?? "-"}%)`;
}

function riskColor(score) {
  if (score >= 75) return "#ff5d5d";
  if (score >= 55) return "#f6a04d";
  if (score >= 35) return "#f6d34d";
  return "#8fd6c1";
}

function scoreColor(score) {
  if (score >= 70) return "#4de2b1";
  if (score >= 50) return "#7fa6bd";
  return "#97a3ad";
}

function renderIgnitionLayer() {
  const group = appState.layerGroups.ignition;
  group.clearLayers();
  for (const ig of appState.data.ignitionCandidates) {
    const marker = L.circleMarker([ig.lat, ig.lon], {
      radius: 5 + ig.riskScore / 20,
      color: riskColor(ig.riskScore),
      fillColor: riskColor(ig.riskScore),
      fillOpacity: 0.55,
      weight: 1,
    });
    marker.bindPopup(
      `<strong>발화 후보지 ${ig.id}</strong><br>위험 점수 ${ig.riskScore}<br>` +
      `${ig.riskType.join(", ")}<br>고도 ${ig.elevation}m · 경사 ${ig.slopeDeg}°`
    );
    marker.addTo(group);
  }
}

function renderSmokeLayer() {
  const group = appState.layerGroups.smoke;
  group.clearLayers();
  const topIgnitionIds = new Set(
    [...appState.data.ignitionCandidates]
      .sort((a, b) => b.riskScore - a.riskScore)
      .slice(0, 12)
      .map((ig) => ig.id)
  );
  for (const path of appState.data.smokePaths) {
    if (!topIgnitionIds.has(path.ignitionId)) continue;
    const latlngs = path.points.map(([lon, lat]) => [lat, lon]);
    const weight = 1 + path.probabilityPct / 6;
    const opacity = 0.25 + Math.min(0.6, path.probabilityPct / 30);
    L.polyline(latlngs, { color: "#d9822b", weight, opacity }).addTo(group);
  }
}

function renderCameraLayer() {
  const group = appState.layerGroups.camera;
  group.clearLayers();
  const cameras = appState.data.cameraCandidates.slice(0, appState.topN);
  for (const cam of cameras) {
    const color = scoreColor(cam.scores.final);
    const marker = L.circleMarker([cam.lat, cam.lon], {
      radius: cam.id === appState.selectedCameraId ? 9 : 6,
      color,
      fillColor: color,
      fillOpacity: 0.75,
      weight: cam.id === appState.selectedCameraId ? 3 : 1,
    });
    marker.bindTooltip(`#${cam.rank} · ${cam.scores.final}`, { direction: "top" });
    marker.on("click", () => selectCamera(cam.id));
    marker.addTo(group);
  }
}

function renderLosLayer() {
  const group = appState.layerGroups.los;
  group.clearLayers();
  if (!appState.selectedCameraId) return;
  const cam = appState.data.cameraCandidates.find((c) => c.id === appState.selectedCameraId);
  if (!cam) return;
  const ignitionsById = Object.fromEntries(appState.data.ignitionCandidates.map((ig) => [ig.id, ig]));
  for (const covered of cam.coveredIgnitions) {
    const ig = ignitionsById[covered.ignitionId];
    if (!ig) continue;
    const color = covered.visibilityScore >= 55 ? "#4de2b1" : covered.visibilityScore >= 30 ? "#f6a04d" : "#ff5d5d";
    L.polyline([[cam.lat, cam.lon], [ig.lat, ig.lon]], {
      color, weight: 1.5, opacity: 0.7, dashArray: covered.visibilityScore >= 55 ? null : "4,4",
    }).addTo(group);
  }
}

function applyLayerVisibility() {
  // "sim"에는 토글 체크박스가 없다 - 항상 지도에 붙어 있어야 하므로 이 루프 대상에서 제외한다.
  for (const [key, group] of Object.entries(appState.layerGroups)) {
    if (key === "sim") continue;
    if (appState.visibility[key]) {
      if (!appState.map.hasLayer(group)) group.addTo(appState.map);
    } else if (appState.map.hasLayer(group)) {
      appState.map.removeLayer(group);
    }
  }
}

function renderRankingList() {
  const cameras = appState.data.cameraCandidates.slice(0, appState.topN);
  const html = cameras.map((cam) => {
    const selected = cam.id === appState.selectedCameraId ? "is-selected" : "";
    return `
      <div class="ranking-item ${selected}" data-cam-id="${cam.id}">
        <span class="rank-num">#${cam.rank}</span>
        <span class="rank-id">${cam.id} · 고도 ${cam.elevation}m</span>
        <span class="rank-score" style="color:${scoreColor(cam.scores.final)}">${cam.scores.final}</span>
      </div>`;
  }).join("");
  const container = document.getElementById("ranking-list");
  container.innerHTML = html;
  container.querySelectorAll(".ranking-item").forEach((el) => {
    el.addEventListener("click", () => selectCamera(el.dataset.camId));
  });
}

function selectCamera(camId) {
  appState.selectedCameraId = camId;
  const cam = appState.data.cameraCandidates.find((c) => c.id === camId);
  if (cam) appState.map.panTo([cam.lat, cam.lon]);
  renderCameraLayer();
  renderRankingList();
  renderLosLayer();
  showDetail(camId);
}

function scoreBarRow(label, value, approx) {
  const pct = Math.max(0, Math.min(100, value));
  const isRisk = label.includes("위험");
  return `
    <div class="score-bar-row">
      <div class="score-bar-meta">
        <span>${label}${approx ? '<span class="approx-tag">근사</span>' : ""}</span>
        <span>${pct.toFixed(1)}</span>
      </div>
      <div class="score-track"><div class="score-fill ${isRisk ? "risk" : ""}" style="width:${pct}%"></div></div>
    </div>`;
}

function showDetail(camId) {
  const cam = appState.data.cameraCandidates.find((c) => c.id === camId);
  if (!cam) return;
  document.getElementById("detail-panel-wrap").style.display = "block";
  document.getElementById("detail-title").textContent = `${cam.id} (${cam.rank}위)`;
  document.getElementById("detail-final-score").textContent = cam.scores.final;

  const bars = Object.keys(SCORE_LABELS).map((key) =>
    scoreBarRow(SCORE_LABELS[key], displayScore(cam, key), isApprox(cam, key))
  ).join("");
  document.getElementById("detail-bars").innerHTML = bars;

  document.getElementById("detail-reasons").innerHTML =
    cam.reasons.map((r) => `<p>${r}</p>`).join("");
}

async function runSimulation(lon, lat) {
  appState.simLoading = true;
  const statusEl = document.getElementById("sim-status");
  statusEl.textContent = "시뮬레이션 계산 중...";

  const group = appState.layerGroups.sim;
  group.clearLayers();
  L.circleMarker([lat, lon], {
    radius: 8, color: "#ffe14d", fillColor: "#ffe14d", fillOpacity: 0.9, weight: 2,
  }).addTo(group);

  try {
    const params = new URLSearchParams({
      region: appState.region, lon, lat, season: appState.simSeason,
    });
    const res = await fetch(`/api/simulate?${params.toString()}`);
    const result = await res.json();
    if (!res.ok) {
      statusEl.textContent = result.message || "이 지점은 시뮬레이션할 수 없습니다(범위 밖일 수 있음).";
      document.getElementById("sim-result-wrap").style.display = "none";
      return;
    }
    renderSimResult(result);
    statusEl.textContent = "다른 지점을 클릭하면 다시 계산합니다.";
  } catch (err) {
    statusEl.textContent = "시뮬레이션 요청에 실패했습니다.";
  } finally {
    appState.simLoading = false;
  }
}

function renderSimResult(result) {
  const group = appState.layerGroups.sim;
  for (const dir of result.directions) {
    const latlngs = [[result.point.lat, result.point.lon], ...dir.points.map(([lo, la]) => [la, lo])];
    const color = dir.detected ? "#4de2b1" : "#ff5d5d";
    const weight = 1 + dir.probabilityPct / 6;
    L.polyline(latlngs, { color, weight, opacity: 0.4 + Math.min(0.5, dir.probabilityPct / 30), dashArray: dir.detected ? null : "5,5" })
      .bindTooltip(
        `${dir.direction} (${dir.probabilityPct}%) · ${dir.detected ? `${dir.detectionTimeMin}분 후 ${dir.detectingCameraId} 탐지` : "탐지 실패"}`,
        { sticky: true }
      )
      .addTo(group);
  }

  document.getElementById("sim-result-wrap").style.display = "block";
  const headline = result.headlineDirection;
  const expected = result.expectedDetectionTimeMin;
  const headlineHtml = headline
    ? `
      <p>표고 ${result.point.elevation ?? "-"}m 지점, <strong>${appState.simSeason}철</strong> 풍속 ${result.windSpeedMsUsed} m/s 기준.</p>
      <p>가장 유력한 풍향 <strong>${headline.direction}</strong>(${headline.probabilityPct}%) 확산 시 ${
        headline.detected
          ? `<strong>${headline.detectingCameraId}</strong>가 약 <strong>${headline.detectionTimeMin}분</strong> 만에 탐지 예상`
          : "현재 카메라망으로 탐지 실패 예상"
      }.</p>
      ${expected !== null ? `<p>전체 풍향 확률가중 평균 예상 탐지 시간: <strong>${expected}분</strong></p>` : ""}
      ${result.undetectedProbabilityPct > 0 ? `<p>풍향 확률 중 <strong>${result.undetectedProbabilityPct}%</strong>는 현재 후보 카메라망으로 탐지되지 않을 수 있습니다.</p>` : ""}
    `
    : "<p>바람 데이터가 부족해 경로를 만들 수 없습니다.</p>";
  document.getElementById("sim-headline").innerHTML = headlineHtml;

  const rows = result.directions.map((dir) => `
    <div class="sim-direction-row ${dir.detected ? "detected" : "undetected"}">
      <span>${dir.direction} · ${dir.probabilityPct}%</span>
      <span>${dir.detected ? `${dir.detectionTimeMin}분 (${dir.detectingCameraId})` : "미탐지"}</span>
    </div>`).join("");
  document.getElementById("sim-direction-list").innerHTML = rows;
}

init();
