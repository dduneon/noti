"use strict";

const TRADE_TYPES = [
  ["A1", "매매"],
  ["B1", "전세"],
  ["B2", "월세"],
  ["B3", "단기임대"],
];
const REAL_ESTATE_TYPES = [
  ["APT", "아파트"],
  ["OPST", "오피스텔"],
  ["VL", "빌라"],
  ["DDDGG", "단독/다가구"],
  ["JGC", "재건축"],
  ["ABYG", "분양권"],
];
const DIRECTIONS = ["남향", "남동향", "남서향", "동향", "서향", "북향", "북동향", "북서향"];

const NUMBER_FIELDS = ["max_pages", "max_subregions"];
const CRITERIA_NUMBERS = [
  "min_deposit", "max_deposit", "min_monthly", "max_monthly",
  "min_area_m2", "max_area_m2", "min_floor", "max_floor",
];

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 3200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && body.detail ? JSON.stringify(body.detail) : response.statusText;
    throw new Error(detail);
  }
  return body;
}

/** 만원 단위를 "9억 5,000만원" 처럼 읽어준다. */
function moneyHint(man) {
  if (!man && man !== 0) return "";
  const eok = Math.floor(man / 10000);
  const rest = man % 10000;
  if (eok && rest) return `${eok}억 ${rest.toLocaleString()}만원`;
  if (eok) return `${eok}억`;
  return `${man.toLocaleString()}만원`;
}

const pyeong = (m2) => (m2 ? `약 ${(m2 / 3.3058).toFixed(1)}평` : "");

function renderChips(container, options, selected, onChange) {
  container.innerHTML = "";
  for (const option of options) {
    const [value, label] = Array.isArray(option) ? option : [option, option];
    const chip = document.createElement("label");
    chip.className = "chip" + (selected.includes(value) ? " on" : "");
    chip.innerHTML = `<input type="checkbox"><span>${label}</span>`;
    const input = chip.querySelector("input");
    input.checked = selected.includes(value);
    input.addEventListener("change", () => {
      chip.classList.toggle("on", input.checked);
      const next = input.checked
        ? [...onChange.get(), value]
        : onChange.get().filter((v) => v !== value);
      onChange.set(next);
    });
    container.appendChild(chip);
  }
}

function emptyTarget() {
  return {
    name: "",
    kind: "region",
    complex_no: null,
    cortar_no: null,
    expand_subregions: true,
    max_subregions: 30,
    trade_types: ["B1"],
    real_estate_types: ["APT"],
    max_pages: 2,
    criteria: {
      min_deposit: null, max_deposit: null, min_monthly: null, max_monthly: null,
      min_area_m2: null, max_area_m2: null, min_floor: null, max_floor: null,
      exclude_first_floor: false, directions: [], include_keywords: [], exclude_keywords: [],
    },
  };
}

function buildCard(target, onRemove) {
  const node = $("#targetTpl").content.firstElementChild.cloneNode(true);

  const applyKind = () => {
    $$("[data-only]", node).forEach((el) => {
      el.hidden = el.dataset.only !== target.kind;
    });
  };

  // 일반 필드
  $$("[data-field]", node).forEach((el) => {
    const key = el.dataset.field;
    if (el.classList.contains("chips")) return;
    if (el.type === "checkbox") {
      el.checked = Boolean(target[key]);
      el.addEventListener("change", () => (target[key] = el.checked));
    } else {
      el.value = target[key] ?? "";
      el.addEventListener("input", () => {
        const raw = el.value.trim();
        target[key] = NUMBER_FIELDS.includes(key)
          ? (raw === "" ? null : Number(raw))
          : (raw === "" ? null : raw);
        if (key === "kind") applyKind();
      });
    }
  });

  renderChips($('[data-field="trade_types"]', node), TRADE_TYPES, target.trade_types, {
    get: () => target.trade_types,
    set: (v) => (target.trade_types = v),
  });
  renderChips(
    $('[data-field="real_estate_types"]', node),
    REAL_ESTATE_TYPES,
    target.real_estate_types,
    { get: () => target.real_estate_types, set: (v) => (target.real_estate_types = v) },
  );
  renderChips($('[data-criteria="directions"]', node), DIRECTIONS, target.criteria.directions, {
    get: () => target.criteria.directions,
    set: (v) => (target.criteria.directions = v),
  });

  // 조건 필드
  $$("[data-criteria]", node).forEach((el) => {
    const key = el.dataset.criteria;
    if (el.classList.contains("chips")) return;
    if (el.type === "checkbox") {
      el.checked = Boolean(target.criteria[key]);
      el.addEventListener("change", () => (target.criteria[key] = el.checked));
      return;
    }
    el.value = target.criteria[key] ?? "";
    const hint = el.parentElement.querySelector("[data-hint]");
    const updateHint = () => {
      if (!hint) return;
      const value = target.criteria[key];
      hint.textContent = key.includes("deposit") || key.includes("monthly")
        ? moneyHint(value)
        : key.includes("area") ? pyeong(value) : "";
    };
    el.addEventListener("input", () => {
      const raw = el.value.trim();
      target.criteria[key] = raw === "" ? null : Number(raw);
      updateHint();
    });
    updateHint();
  });

  $$("[data-criteria-list]", node).forEach((el) => {
    const key = el.dataset.criteriaList;
    el.value = (target.criteria[key] || []).join(", ");
    el.addEventListener("input", () => {
      target.criteria[key] = el.value.split(",").map((s) => s.trim()).filter(Boolean);
    });
  });

  // 지역 검색
  const queryInput = $("[data-region-query]", node);
  const resultList = $("[data-region-results]", node);
  const chosen = $("[data-region-chosen]", node);
  const cortarInput = $('[data-field="cortar_no"]', node);

  const searchRegion = async () => {
    const q = queryInput.value.trim();
    if (!q) return;
    resultList.hidden = true;
    try {
      const results = await api(`/api/regions/search?q=${encodeURIComponent(q)}`);
      resultList.innerHTML = "";
      if (!results.length) {
        toast("일치하는 지역이 없습니다. '서울 강남구'처럼 상위 지역부터 넣어보세요.", true);
        return;
      }
      for (const item of results) {
        const li = document.createElement("li");
        li.innerHTML = `${item.path} <code>${item.cortar_no}</code>`;
        li.addEventListener("click", () => {
          target.cortar_no = item.cortar_no;
          cortarInput.value = item.cortar_no;
          chosen.textContent = `선택됨: ${item.path}`;
          resultList.hidden = true;
        });
        resultList.appendChild(li);
      }
      resultList.hidden = false;
    } catch (err) {
      toast(`지역 검색 실패: ${err.message}`, true);
    }
  };

  $('[data-action="search-region"]', node).addEventListener("click", searchRegion);
  queryInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); searchRegion(); }
  });

  // 미리보기
  $('[data-action="preview"]', node).addEventListener("click", async (e) => {
    const button = e.currentTarget;  // await 이후엔 null 이 된다
    const status = $("[data-preview-status]", node);
    const list = $("[data-preview-results]", node);
    button.disabled = true;
    status.textContent = "조회 중…";
    try {
      const result = await api("/api/preview", {
        method: "POST",
        body: JSON.stringify({ target, limit: 20 }),
      });
      status.textContent =
        `${result.scopes}곳 조회 · 매물 ${result.fetched}건 중 조건 일치 ${result.matched}건`;
      list.innerHTML = result.listings
        .map(
          (x) => `<li>
            <div class="name">${x.name}</div>
            <div class="meta">${x.summary}${x.realtor ? ` · ${x.realtor}` : ""}</div>
            <a href="${x.url}" target="_blank" rel="noreferrer">네이버에서 보기</a>
          </li>`,
        )
        .join("");
      list.hidden = result.listings.length === 0;
    } catch (err) {
      status.textContent = "";
      toast(`미리보기 실패: ${err.message}`, true);
    } finally {
      button.disabled = false;
    }
  });

  $('[data-action="remove"]', node).addEventListener("click", () => onRemove(node));
  applyKind();
  return node;
}

const state = { notify_on_first_run: false, targets: [] };

function render() {
  const container = $("#targets");
  container.innerHTML = "";
  state.targets.forEach((target) => {
    const card = buildCard(target, (node) => {
      state.targets = state.targets.filter((t) => t !== target);
      node.remove();
      $("#empty").hidden = state.targets.length > 0;
    });
    container.appendChild(card);
  });
  $("#empty").hidden = state.targets.length > 0;
}

async function load() {
  const runtime = await api("/api/settings");
  $("#runtime").innerHTML =
    `설정 파일 <code>${runtime.config_path}</code> · 폴링 ${runtime.poll_interval_seconds}초 · ` +
    (runtime.telegram_enabled
      ? '텔레그램 <span class="on">연결됨</span>'
      : '텔레그램 <span class="off">미설정(.env 확인)</span>');

  const config = await api("/api/config");
  state.notify_on_first_run = config.notify_on_first_run;
  state.targets = config.targets;
  $("#notifyFirstRun").checked = state.notify_on_first_run;
  render();
}

$("#notifyFirstRun").addEventListener("change", (e) => {
  state.notify_on_first_run = e.target.checked;
});

$("#addTarget").addEventListener("click", () => {
  state.targets.push(emptyTarget());
  render();
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
});

$("#save").addEventListener("click", async (e) => {
  // await 이후엔 e.currentTarget 이 null 이라 미리 잡아둔다.
  const button = e.currentTarget;
  button.disabled = true;
  try {
    const result = await api("/api/config", { method: "PUT", body: JSON.stringify(state) });
    toast(`저장했습니다. 감시 대상 ${result.targets}개 (다음 사이클부터 적용)`);
  } catch (err) {
    toast(`저장 실패: ${err.message}`, true);
  } finally {
    button.disabled = false;
  }
});

$("#testNotify").addEventListener("click", async (e) => {
  const button = e.currentTarget;
  button.disabled = true;
  try {
    await api("/api/test-notify", { method: "POST" });
    toast("텔레그램으로 테스트 메시지를 보냈습니다.");
  } catch (err) {
    toast(`전송 실패: ${err.message}`, true);
  } finally {
    button.disabled = false;
  }
});

load().catch((err) => toast(`설정을 불러오지 못했습니다: ${err.message}`, true));
