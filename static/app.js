const state = {
  siteKey: null,
  challenge: null,
  dragging: false,
  dragMode: null,
  startClientX: 0,
  offset: 0,
  startedAt: 0,
  movements: [],
  locked: false,
  markerCanvasX: 0,
  markerCanvasY: 0,
  markerPointerDx: 0,
  markerPointerDy: 0,
  challengeStartedAt: 0,
};

const elements = {
  serviceState: document.querySelector("#service-state"),
  instruction: document.querySelector("#instruction"),
  stage: document.querySelector("#captcha-stage"),
  background: document.querySelector("#background"),
  piece: document.querySelector("#piece"),
  marker: document.querySelector("#target-marker"),
  loading: document.querySelector("#loading"),
  qaForm: document.querySelector("#qa-form"),
  questionText: document.querySelector("#question-text"),
  answerInput: document.querySelector("#answer-input"),
  answerButton: document.querySelector(".answer-button"),
  slider: document.querySelector("#slider"),
  progress: document.querySelector("#slider-progress"),
  handle: document.querySelector("#slider-handle"),
  label: document.querySelector("#slider-label"),
  refresh: document.querySelector("#refresh"),
  result: document.querySelector("#result"),
  token: document.querySelector("#token"),
};

async function getPublicConfig() {
  const response = await fetch("/config.json", { cache: "no-store" });
  if (!response.ok) throw new Error("public_config_unavailable");
  return response.json();
}

function resetView() {
  state.locked = true;
  state.challenge = null;
  state.dragging = false;
  state.offset = 0;
  state.movements = [];
  elements.stage.classList.remove("success", "error");
  elements.slider.classList.remove("success", "error");
  elements.result.hidden = true;
  elements.loading.hidden = false;
  elements.loading.textContent = "이미지를 준비하고 있습니다";
  elements.piece.hidden = true;
  elements.marker.hidden = true;
  elements.qaForm.hidden = true;
  elements.answerInput.value = "";
  elements.answerInput.disabled = false;
  elements.answerButton.disabled = false;
  elements.slider.hidden = false;
  elements.label.textContent = "오른쪽으로 밀어 맞추기";
  moveSliderTo(0);
}

async function loadChallenge() {
  resetView();
  try {
    if (!state.siteKey) {
      const config = await getPublicConfig();
      state.siteKey = config.siteKey;
    }
    const response = await fetch("/v1/challenges", {
      method: "POST",
      headers: { "X-Captcha-Site-Key": state.siteKey },
    });
    if (!response.ok) throw new Error(`challenge_${response.status}`);

    const challenge = await response.json();
    state.challenge = challenge;
    state.challengeStartedAt = performance.now();
    elements.background.src = challenge.background_url;
    await elements.background.decode();

    if (challenge.mode === "question_answer") {
      elements.slider.hidden = true;
      elements.qaForm.hidden = false;
      elements.questionText.textContent = challenge.question;
    } else if (challenge.mode === "object_drag") {
      elements.slider.hidden = true;
      elements.marker.hidden = false;
      state.markerCanvasX = 38;
      state.markerCanvasY = challenge.height - 38;
      positionMarkerFromCanvas();
    } else {
      elements.piece.src = challenge.piece_url;
      elements.piece.style.top = `${(challenge.piece_y / challenge.height) * 100}%`;
      await elements.piece.decode();
      elements.piece.hidden = false;
      elements.slider.hidden = false;
    }

    elements.loading.hidden = true;
    elements.serviceState.textContent = "서비스 정상";
    elements.serviceState.classList.add("ready");
    elements.instruction.textContent = challenge.instruction;
    state.locked = false;
  } catch (error) {
    elements.loading.textContent = "문제를 불러오지 못했습니다";
    elements.serviceState.textContent = "연결 오류";
    elements.serviceState.classList.remove("ready");
    elements.instruction.textContent = "잠시 후 새 문제를 눌러주세요.";
  }
}

function maxHandleOffset() {
  return elements.slider.clientWidth - elements.handle.clientWidth;
}

function moveSliderTo(offset) {
  const maximum = Math.max(0, maxHandleOffset());
  const clamped = Math.max(0, Math.min(offset, maximum));
  state.offset = clamped;
  elements.handle.style.transform = `translateX(${clamped}px)`;
  elements.progress.style.width = `${clamped + elements.handle.clientWidth}px`;
  if (state.challenge && state.challenge.mode === "jigsaw" && maximum > 0) {
    const pieceX = (clamped / maximum) * state.challenge.travel_width;
    const displayX = (pieceX / state.challenge.width) * elements.stage.clientWidth;
    elements.piece.style.transform = `translateX(${displayX}px)`;
  }
}

function currentPuzzleX() {
  const maximum = maxHandleOffset();
  if (!state.challenge || maximum <= 0) return 0;
  return (state.offset / maximum) * state.challenge.travel_width;
}

function moveMarkerDisplay(x, y) {
  const radius = elements.marker.offsetWidth / 2;
  const edgeInset = 2;
  const clampedX = Math.max(
    radius + edgeInset,
    Math.min(x, elements.stage.clientWidth - radius - edgeInset),
  );
  const clampedY = Math.max(
    radius + edgeInset,
    Math.min(y, elements.stage.clientHeight - radius - edgeInset),
  );
  elements.marker.style.transform = `translate(${clampedX - radius}px, ${clampedY - radius}px)`;
  state.markerCanvasX = (clampedX / elements.stage.clientWidth) * state.challenge.width;
  state.markerCanvasY = (clampedY / elements.stage.clientHeight) * state.challenge.height;
}

function positionMarkerFromCanvas() {
  if (!state.challenge || state.challenge.mode !== "object_drag") return;
  const x = (state.markerCanvasX / state.challenge.width) * elements.stage.clientWidth;
  const y = (state.markerCanvasY / state.challenge.height) * elements.stage.clientHeight;
  moveMarkerDisplay(x, y);
}

function movementPoint(elapsed) {
  if (state.dragMode === "object_drag") {
    return {
      x: Math.round(state.markerCanvasX),
      y: Math.round(state.markerCanvasY),
      t: elapsed,
    };
  }
  return { x: Math.round(currentPuzzleX()), t: elapsed };
}

function recordMovement(elapsed) {
  const point = movementPoint(elapsed);
  const last = state.movements[state.movements.length - 1];
  if (!last || elapsed - last.t >= 16) {
    state.movements.push(point);
  } else {
    state.movements[state.movements.length - 1] = point;
  }
  if (state.movements.length > 78) state.movements.splice(1, 1);
}

function ensureMovementSamples() {
  if (state.movements.length >= 3) return;
  const first = state.movements[0];
  const last = state.movements[state.movements.length - 1];
  const midpoint = {
    x: Math.round((first.x + last.x) / 2),
    t: Math.round((first.t + last.t) / 2),
  };
  if (first.y !== undefined && last.y !== undefined) {
    midpoint.y = Math.round((first.y + last.y) / 2);
  }
  state.movements.splice(1, 0, midpoint);
}

function sliderPointerDown(event) {
  if (state.locked || !state.challenge || state.challenge.mode !== "jigsaw") return;
  state.dragging = true;
  state.dragMode = "jigsaw";
  state.startClientX = event.clientX - state.offset;
  state.startedAt = performance.now();
  state.movements = [movementPoint(0)];
  elements.handle.setPointerCapture(event.pointerId);
}

function sliderPointerMove(event) {
  if (!state.dragging || state.dragMode !== "jigsaw") return;
  moveSliderTo(event.clientX - state.startClientX);
  recordMovement(Math.round(performance.now() - state.startedAt));
}

async function sliderPointerUp(event) {
  if (!state.dragging || state.dragMode !== "jigsaw") return;
  state.dragging = false;
  elements.handle.releasePointerCapture(event.pointerId);
  const duration = Math.max(100, Math.round(performance.now() - state.startedAt));
  recordMovement(duration);
  ensureMovementSamples();
  await verify(Math.round(currentPuzzleX()), null, duration);
}

function markerPointerDown(event) {
  if (state.locked || !state.challenge || state.challenge.mode !== "object_drag") return;
  state.dragging = true;
  state.dragMode = "object_drag";
  state.startedAt = performance.now();
  const markerRect = elements.marker.getBoundingClientRect();
  state.markerPointerDx = event.clientX - (markerRect.left + markerRect.width / 2);
  state.markerPointerDy = event.clientY - (markerRect.top + markerRect.height / 2);
  state.movements = [movementPoint(0)];
  elements.marker.setPointerCapture(event.pointerId);
}

function markerPointerMove(event) {
  if (!state.dragging || state.dragMode !== "object_drag") return;
  const stageRect = elements.stage.getBoundingClientRect();
  moveMarkerDisplay(
    event.clientX - stageRect.left - state.markerPointerDx,
    event.clientY - stageRect.top - state.markerPointerDy,
  );
  recordMovement(Math.round(performance.now() - state.startedAt));
}

async function markerPointerUp(event) {
  if (!state.dragging || state.dragMode !== "object_drag") return;
  state.dragging = false;
  elements.marker.releasePointerCapture(event.pointerId);
  const duration = Math.max(100, Math.round(performance.now() - state.startedAt));
  recordMovement(duration);
  ensureMovementSamples();
  await verify(
    Math.round(state.markerCanvasX),
    Math.round(state.markerCanvasY),
    duration,
  );
}

function pointerCancel() {
  state.dragging = false;
  state.dragMode = null;
}

async function verify(x, y, durationMs) {
  state.locked = true;
  elements.label.textContent = "확인 중";
  const payload = {
    x,
    duration_ms: durationMs,
    movements: state.movements,
  };
  if (y !== null) payload.y = y;

  try {
    const response = await fetch(`/v1/challenges/${state.challenge.challenge_id}/verify`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Captcha-Site-Key": state.siteKey,
      },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (result.success) {
      showSuccess(result);
      return;
    }
    elements.slider.classList.add("error");
    elements.stage.classList.add("error");
    elements.label.textContent = "위치가 맞지 않습니다";
    elements.instruction.textContent = "새 문제로 다시 시도해주세요.";
    window.setTimeout(loadChallenge, 900);
  } catch (error) {
    elements.slider.classList.add("error");
    elements.stage.classList.add("error");
    elements.instruction.textContent = "검증 요청에 실패했습니다.";
    window.setTimeout(loadChallenge, 1200);
  }
}

function showSuccess(result) {
  elements.slider.classList.add("success");
  elements.stage.classList.add("success");
  elements.label.textContent = "인증 완료";
  elements.instruction.textContent = "인증이 완료되었습니다.";
  elements.token.textContent = result.verification_token;
  elements.result.hidden = false;
  elements.answerInput.disabled = true;
  elements.answerButton.disabled = true;
}

async function submitAnswer(event) {
  event.preventDefault();
  if (state.locked || state.challenge?.mode !== "question_answer") return;
  state.locked = true;
  elements.answerButton.disabled = true;
  const duration = Math.max(
    100,
    Math.min(120000, Math.round(performance.now() - state.challengeStartedAt)),
  );
  try {
    const response = await fetch(`/v1/challenges/${state.challenge.challenge_id}/verify`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Captcha-Site-Key": state.siteKey,
      },
      body: JSON.stringify({
        answer: elements.answerInput.value,
        duration_ms: duration,
      }),
    });
    const result = await response.json();
    if (result.success) {
      showSuccess(result);
      return;
    }
    elements.stage.classList.add("error");
    elements.instruction.textContent = "정답이 아닙니다. 새 문제로 다시 시도해주세요.";
    window.setTimeout(loadChallenge, 900);
  } catch (error) {
    elements.instruction.textContent = "검증 요청에 실패했습니다.";
    window.setTimeout(loadChallenge, 1200);
  }
}

elements.handle.addEventListener("pointerdown", sliderPointerDown);
elements.handle.addEventListener("pointermove", sliderPointerMove);
elements.handle.addEventListener("pointerup", sliderPointerUp);
elements.handle.addEventListener("pointercancel", pointerCancel);
elements.marker.addEventListener("pointerdown", markerPointerDown);
elements.marker.addEventListener("pointermove", markerPointerMove);
elements.marker.addEventListener("pointerup", markerPointerUp);
elements.marker.addEventListener("pointercancel", pointerCancel);
elements.qaForm.addEventListener("submit", submitAnswer);
elements.refresh.addEventListener("click", loadChallenge);
window.addEventListener("resize", () => {
  if (state.challenge?.mode === "object_drag") positionMarkerFromCanvas();
  else moveSliderTo(state.offset);
});

loadChallenge();
