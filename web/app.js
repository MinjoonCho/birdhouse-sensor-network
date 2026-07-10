const SOURCE_LABELS = {
  fireHistory: "산림청 산불통계",
  asosWind: "기상청 ASOS",
  airQuality: "에어코리아 대기질",
  vworldWfs: "VWorld WFS",
  dem: "국토정보 DEM",
};

const DIRECTIONS_16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];

const MOUNTAIN_LIST_LIMIT = 40; // 산이 수백~천 단위라 목록은 노드 수 상위 N개만 보여준다

// VWorld 주소 지오코딩(api.vworld.kr/req/address)으로 확인한 좌표 - "1리" 자연부락
// 단위까지는 구조화 주소 체계가 못 내려가서, 구계리(행정리) 대표 좌표를 쓴다.
const BOOKMARKED_LOCATIONS = {
  guge1ri: { label: "구계1리(의성군 단촌면 구계리)", region: "uiseong", lon: 128.766575, lat: 36.475073 },
};

let appState = {
  region: "uiseong",
  data: null,
  map: null,
  layerGroups: {},
  visibility: { ignition: true, smoke: true, mountain: true },
  pickMode: null, // null | "sim" | "area"
  simSeason: "봄",
  simLoading: false,
  selectedMountainId: null,
  areaMode: "mountain", // "mountain" | "custom"
  customArea: null, // { lon, lat, radiusM }
  objective: "worst", // "worst" | "average"
  queryMode: "target", // "target" | "count"
  queryLoading: false,
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
    mountain: L.layerGroup().addTo(appState.map),
    mountainOverview: L.layerGroup().addTo(appState.map),
    sim: L.layerGroup().addTo(appState.map),
    bookmark: L.layerGroup().addTo(appState.map),
  };

  appState.map.on("click", (e) => {
    if (appState.pickMode === "sim") {
      if (appState.simLoading) return;
      runSimulation(e.latlng.lng, e.latlng.lat);
    } else if (appState.pickMode === "area") {
      const radius = Number(document.getElementById("area-radius-input").value) || 1500;
      appState.customArea = { lon: e.latlng.lng, lat: e.latlng.lat, radiusM: radius };
      document.getElementById("area-pick-status").textContent =
        `위치 지정됨 (${e.latlng.lat.toFixed(4)}, ${e.latlng.lng.toFixed(4)}) · 반경 ${radius}m`;
      runPlacementQuery();
    }
  });
}

function bindControls() {
  document.getElementById("region-control").addEventListener("click", (e) => {
    const btn = e.target.closest(".segment");
    if (!btn) return;
    setActiveSegment("region-control", btn);
    loadRegion(btn.dataset.region);
  });

  document.getElementById("bookmark-list").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-bookmark]");
    if (!btn) return;
    flyToBookmark(btn.dataset.bookmark);
  });

  document.querySelectorAll("#layer-toggles input").forEach((input) => {
    input.addEventListener("change", () => {
      appState.visibility[input.dataset.layer] = input.checked;
      applyLayerVisibility();
    });
  });

  document.getElementById("mountain-select").addEventListener("change", (e) => {
    selectMountain(e.target.value);
  });

  document.getElementById("sim-season-control").addEventListener("click", (e) => {
    const btn = e.target.closest(".segment");
    if (!btn) return;
    setActiveSegment("sim-season-control", btn);
    appState.simSeason = btn.dataset.season;
  });

  const simToggle = document.getElementById("sim-toggle-button");
  simToggle.addEventListener("click", () => {
    const turningOn = appState.pickMode !== "sim";
    setPickMode(turningOn ? "sim" : null);
  });

  const areaToggle = document.getElementById("area-pick-button");
  areaToggle.addEventListener("click", () => {
    const turningOn = appState.pickMode !== "area";
    setPickMode(turningOn ? "area" : null);
  });

  document.getElementById("area-mode-control").addEventListener("click", (e) => {
    const btn = e.target.closest(".segment");
    if (!btn) return;
    setActiveSegment("area-mode-control", btn);
    appState.areaMode = btn.dataset.areaMode;
    const isCustom = appState.areaMode === "custom";
    document.getElementById("mountain-select").style.display = isCustom ? "none" : "";
    document.getElementById("custom-area-controls").style.display = isCustom ? "" : "none";
    if (!isCustom) setPickMode(null);
  });

  document.getElementById("objective-control").addEventListener("click", (e) => {
    const btn = e.target.closest(".segment");
    if (!btn) return;
    setActiveSegment("objective-control", btn);
    appState.objective = btn.dataset.objective;
  });

  document.getElementById("query-mode-control").addEventListener("click", (e) => {
    const btn = e.target.closest(".segment");
    if (!btn) return;
    setActiveSegment("query-mode-control", btn);
    appState.queryMode = btn.dataset.queryMode;
    const input = document.getElementById("query-value-input");
    input.value = appState.queryMode === "target" ? 20 : 4;
    input.max = appState.queryMode === "target" ? 999 : 20;
  });

  document.getElementById("query-run-button").addEventListener("click", () => runPlacementQuery());
}

function setPickMode(mode) {
  appState.pickMode = mode;
  const simToggle = document.getElementById("sim-toggle-button");
  const areaToggle = document.getElementById("area-pick-button");
  simToggle.classList.toggle("is-active", mode === "sim");
  simToggle.textContent = mode === "sim"
    ? "시뮬레이션 모드 켜짐 · 지도를 클릭하세요"
    : "지도 클릭으로 발화 지점 지정";
  document.getElementById("sim-status").textContent = mode === "sim"
    ? "지도 위 임의의 지점을 클릭하면 그 자리에서 시뮬레이션을 실행합니다."
    : "";
  areaToggle.classList.toggle("is-active", mode === "area");
  areaToggle.textContent = mode === "area"
    ? "위치 지정 모드 켜짐 · 지도를 클릭하세요"
    : "지도 클릭으로 위치 지정";
}

function setActiveSegment(containerId, activeBtn) {
  document.querySelectorAll(`#${containerId} .segment`).forEach((b) => b.classList.remove("is-active"));
  activeBtn.classList.add("is-active");
}

async function loadRegion(regionKey) {
  appState.region = regionKey;
  document.getElementById("sim-result-wrap").style.display = "none";
  appState.layerGroups.sim.clearLayers();
  const res = await fetch(`./data/${regionKey}.json`);
  const data = await res.json();
  appState.data = data;

  document.getElementById("map-title").textContent = `${data.regionNameKo} · 센서 배치 후보`;
  document.getElementById("region-note").textContent = data.note || "";

  renderHeroStats(data);
  renderSourceStatus(data);
  renderWindRose(data);
  renderIgnitionLayer();
  renderSmokeLayer();
  renderMountainOverviewLayer(data);
  populateMountainSelect(data);
  applyLayerVisibility();
  fitMapToBounds(data.bbox);
}

function fitMapToBounds(bbox) {
  const [west, south, east, north] = bbox;
  appState.map.fitBounds([[south, west], [north, east]], { padding: [20, 20] });
}

function renderHeroStats(data) {
  const mountains = data.mountainCoverage || [];
  const totalCameras = mountains.reduce((sum, m) => sum + m.recommendedCameras.length, 0);
  const avgWorstCase = mountains.length
    ? mountains.reduce((sum, m) => sum + (m.recommendedCameras.at(-1)?.worstCaseMin ?? 0), 0) / mountains.length
    : 0;
  const cards = [
    { value: data.ignitionCandidates.length, label: "발화 후보지" },
    { value: mountains.length, label: "식별된 산 개수" },
    { value: totalCameras, label: "그래프 추천 카메라 총합" },
    { value: `${avgWorstCase.toFixed(1)}분`, label: "산 평균 최악 탐지 시간" },
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

function applyLayerVisibility() {
  // "sim"/"mountainOverview"에는 토글 체크박스가 없다 - 항상 지도에 붙어 있어야
  // 하므로 이 루프 대상에서 제외한다.
  const alwaysOn = new Set(["sim", "mountainOverview", "bookmark"]);
  for (const [key, group] of Object.entries(appState.layerGroups)) {
    if (alwaysOn.has(key)) continue;
    if (appState.visibility[key]) {
      if (!appState.map.hasLayer(group)) group.addTo(appState.map);
    } else if (appState.map.hasLayer(group)) {
      appState.map.removeLayer(group);
    }
  }
}

async function runSimulation(lon, lat) {
  appState.simLoading = true;
  const statusEl = document.getElementById("sim-status");
  statusEl.textContent = "시뮬레이션 계산 중...";

  const group = appState.layerGroups.sim;
  group.clearLayers();
  const pulseMarker = L.marker([lat, lon], {
    icon: L.divIcon({ className: "", html: '<div class="sim-pulse-icon"></div>', iconSize: [22, 22], iconAnchor: [11, 11] }),
    interactive: false,
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
      group.clearLayers();
      return;
    }
    renderSimResult(result);
    statusEl.textContent = "다른 지점을 클릭하면 다시 계산합니다.";
  } catch (err) {
    statusEl.textContent = "시뮬레이션 요청에 실패했습니다.";
  } finally {
    appState.simLoading = false;
    group.removeLayer(pulseMarker);
  }
}

function renderSimResult(result) {
  const group = appState.layerGroups.sim;
  const originLatLng = [result.point.lat, result.point.lon];
  const boundsPoints = [originLatLng];

  L.circleMarker(originLatLng, {
    radius: 8, color: "#ffe14d", fillColor: "#ffe14d", fillOpacity: 0.9, weight: 2,
  }).bindTooltip("시뮬레이션 발화점", { permanent: false }).addTo(group);

  for (const dir of result.directions) {
    const latlngs = [originLatLng, ...dir.points.map(([lo, la]) => [la, lo])];
    boundsPoints.push(...latlngs);
    const color = dir.detected ? "#4de2b1" : "#ff5d5d";
    const weight = 1.5 + dir.probabilityPct / 5;
    L.polyline(latlngs, {
      color, weight, opacity: 0.55 + Math.min(0.4, dir.probabilityPct / 30),
      dashArray: dir.detected ? null : "6,5",
    })
      .bindTooltip(
        `${dir.direction} (${dir.probabilityPct}%) · ${dir.detected ? `${dir.detectionTimeMin}분 후 ${dir.detectingCameraId} 탐지` : "탐지 실패"}`,
        { sticky: true }
      )
      .addTo(group);

    // 경로 끝점에 경과 시간을 표시해 "얼마나 퍼졌는지"를 지도에서 바로 보이게 한다.
    const lastPoint = dir.points[dir.points.length - 1];
    if (lastPoint) {
      const [endLon, endLat, elapsedMin] = lastPoint;
      L.marker([endLat, endLon], {
        icon: L.divIcon({
          className: "",
          html: `<div class="sim-time-label">${dir.direction} · ${elapsedMin}분 시점</div>`,
          iconSize: null,
        }),
        interactive: false,
      }).addTo(group);
    }

    if (dir.detected && dir.detectingPoint) {
      const [dLon, dLat] = dir.detectingPoint;
      L.marker([dLat, dLon], {
        icon: L.divIcon({
          className: "",
          html: `<div class="sim-time-label detect">${dir.detectionTimeMin}분 · ${dir.detectingCameraId} 탐지</div>`,
          iconSize: null,
        }),
        interactive: false,
      }).addTo(group);
      L.circleMarker([dLat, dLon], { radius: 5, color: "#4de2b1", fillColor: "#4de2b1", fillOpacity: 0.9, weight: 2 }).addTo(group);
    }
  }

  if (boundsPoints.length > 1) {
    appState.map.flyToBounds(L.latLngBounds(boundsPoints), { padding: [80, 80], maxZoom: 14, duration: 0.6 });
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

function renderMountainOverviewLayer(data) {
  // 산마다 첫 번째(가장 중요한) 추천 카메라 하나씩 - 전체 지역에 배치가
  // 실제로 퍼져 있는지 한눈에 보여준다("한 산에만 몰려있나?" 질문에 대한 답).
  const group = appState.layerGroups.mountainOverview;
  group.clearLayers();
  for (const mountain of data.mountainCoverage || []) {
    const first = mountain.recommendedCameras[0];
    if (!first) continue;
    L.circleMarker([first.lat, first.lon], {
      radius: 4, color: "#c792ea", fillColor: "#c792ea", fillOpacity: 0.55, weight: 1,
    })
      .bindTooltip(`${mountain.mountainId} · 노드 ${mountain.nodeCount}개`, { direction: "top" })
      .on("click", () => {
        const select = document.getElementById("mountain-select");
        if (![...select.options].some((o) => o.value === mountain.mountainId)) {
          const opt = document.createElement("option");
          opt.value = mountain.mountainId;
          opt.textContent = `${mountain.mountainId} · 노드 ${mountain.nodeCount}개 · 표고 ${Math.round(mountain.seed.elevation)}m`;
          select.appendChild(opt);
        }
        select.value = mountain.mountainId;
        selectMountain(mountain.mountainId);
      })
      .addTo(group);
  }
}

function populateMountainSelect(data) {
  const select = document.getElementById("mountain-select");
  const mountains = (data.mountainCoverage || []);
  const top = mountains.slice(0, MOUNTAIN_LIST_LIMIT);
  select.innerHTML = top.map((m) =>
    `<option value="${m.mountainId}">${m.mountainId} · 노드 ${m.nodeCount}개 · 표고 ${Math.round(m.seed.elevation)}m</option>`
  ).join("");
  if (top.length) {
    select.value = top[0].mountainId;
    selectMountain(top[0].mountainId);
  } else {
    document.getElementById("mountain-chart").innerHTML = "";
    document.getElementById("mountain-summary").textContent = "이 지역에서 식별된 산이 없습니다.";
    appState.layerGroups.mountain.clearLayers();
  }
}

function selectMountain(mountainId) {
  appState.selectedMountainId = mountainId;
  const mountain = (appState.data.mountainCoverage || []).find((m) => m.mountainId === mountainId);
  if (!mountain) return;

  renderMountainLayer(mountain);
  renderMountainChart(mountain);

  const [west, south, east, north] = mountain.bbox;
  appState.map.flyToBounds([[south, west], [north, east]], { padding: [60, 60], maxZoom: 15, duration: 0.6 });
}

function renderMountainLayer(mountain) {
  const group = appState.layerGroups.mountain;
  group.clearLayers();

  if (mountain.hull && mountain.hull.length >= 3) {
    L.polygon(mountain.hull.map(([lo, la]) => [la, lo]), {
      color: "#7fa6bd", weight: 1.5, fillColor: "#7fa6bd", fillOpacity: 0.08, dashArray: "4,3",
    }).addTo(group);
  }

  mountain.recommendedCameras.forEach((cam) => {
    L.circleMarker([cam.lat, cam.lon], {
      radius: 7, color: "#c792ea", fillColor: "#c792ea", fillOpacity: 0.85, weight: 2,
    })
      .bindTooltip(`#${cam.order} · 이 카메라까지 놓으면 최악 ${cam.worstCaseMin ?? "-"}분`, { direction: "top" })
      .addTo(group);
  });
}

function renderMountainChart(mountain, objective = "worst") {
  const cams = mountain.recommendedCameras;
  const metricKey = objective === "average" ? "avgCaseMin" : "worstCaseMin";
  const metricLabel = objective === "average" ? "평균" : "최악";
  const maxTime = Math.max(1, ...cams.map((c) => c[metricKey] ?? 0));
  const rows = cams.map((c) => {
    const pct = maxTime ? ((c[metricKey] ?? 0) / maxTime) * 100 : 0;
    return `
      <div class="mountain-chart-row">
        <span>#${c.order}</span>
        <div class="mountain-chart-bar-track"><div class="mountain-chart-bar-fill" style="width:${pct}%"></div></div>
        <span>${c[metricKey] ?? "-"}분</span>
      </div>`;
  }).join("");
  document.getElementById("mountain-chart").innerHTML = rows;

  const last = cams[cams.length - 1];
  document.getElementById("mountain-summary").textContent =
    `${mountain.mountainId} (노드 ${mountain.nodeCount}개, 표고 ${Math.round(mountain.seed.elevation)}m): ` +
    `카메라 ${cams.length}개로 ${metricLabel} 탐지 시간을 ${last[metricKey] ?? 0}분까지 줄일 수 있습니다 ` +
    `(최장 ${last.worstCaseMin ?? "-"}분 · 평균 ${last.avgCaseMin ?? "-"}분).`;
}

async function runPlacementQuery() {
  if (appState.queryLoading) return;
  const summaryEl = document.getElementById("mountain-summary");
  const params = new URLSearchParams({ region: appState.region, objective: appState.objective });

  if (appState.areaMode === "mountain") {
    if (!appState.selectedMountainId) {
      summaryEl.textContent = "먼저 산을 선택하세요.";
      return;
    }
    params.set("mountainId", appState.selectedMountainId);
  } else {
    if (!appState.customArea) {
      summaryEl.textContent = "먼저 지도에서 위치를 지정하세요.";
      return;
    }
    params.set("lon", appState.customArea.lon);
    params.set("lat", appState.customArea.lat);
    params.set("radiusM", appState.customArea.radiusM);
  }

  const value = Number(document.getElementById("query-value-input").value);
  if (appState.queryMode === "target") params.set("targetMinutes", value);
  else params.set("cameraCount", value);

  appState.queryLoading = true;
  summaryEl.textContent = "계산 중...";
  document.getElementById("mountain-chart").innerHTML = "";

  try {
    const res = await fetch(`/api/plan_cameras?${params.toString()}`);
    const result = await res.json();
    if (!res.ok) {
      summaryEl.textContent = result.message || "계산에 실패했습니다.";
      return;
    }
    renderPlacementResult(result);
  } catch (err) {
    summaryEl.textContent = "질의 요청에 실패했습니다.";
  } finally {
    appState.queryLoading = false;
  }
}

function renderPlacementResult(result) {
  renderMountainLayer({ hull: result.hull, recommendedCameras: result.recommendedCameras });
  renderMountainChart({
    mountainId: result.areaId, nodeCount: result.nodeCount, seed: result.seed,
    recommendedCameras: result.recommendedCameras,
  }, result.objective);

  const [west, south, east, north] = result.bbox;
  appState.map.flyToBounds([[south, west], [north, east]], { padding: [60, 60], maxZoom: 15, duration: 0.6 });
}

async function flyToBookmark(bookmarkId) {
  const bookmark = BOOKMARKED_LOCATIONS[bookmarkId];
  if (!bookmark) return;

  if (appState.region !== bookmark.region) {
    const btn = document.querySelector(`#region-control [data-region="${bookmark.region}"]`);
    if (btn) setActiveSegment("region-control", btn);
    await loadRegion(bookmark.region);
  }

  const group = appState.layerGroups.bookmark;
  group.clearLayers();
  L.circleMarker([bookmark.lat, bookmark.lon], {
    radius: 8, color: "#4de2b1", fillColor: "#4de2b1", fillOpacity: 0.35, weight: 2,
  }).addTo(group);
  L.marker([bookmark.lat, bookmark.lon], {
    icon: L.divIcon({ className: "", html: `<div class="sim-time-label detect">${bookmark.label}</div>`, iconSize: null, iconAnchor: [-10, 10] }),
    interactive: false,
  }).addTo(group);

  appState.map.flyTo([bookmark.lat, bookmark.lon], 15, { duration: 0.8 });
}

init();
