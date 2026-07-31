import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const api = async (url, options = {}) => {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || "요청을 처리하지 못했습니다.");
  return body;
};

const sessionId = () => {
  let value = sessionStorage.getItem("captcha-session");
  if (!value) { value = crypto.randomUUID(); sessionStorage.setItem("captcha-session", value); }
  return value;
};

const reviewerId = () => {
  let value = sessionStorage.getItem("captcha-reviewer");
  if (!value) { value = `reviewer-${crypto.randomUUID()}`; sessionStorage.setItem("captcha-reviewer", value); }
  return value;
};

// 자동화 브라우저(헤드리스/Selenium/Playwright 등) 탐지용 신호. 위조 가능하나 순정 자동화를 잡는다.
const clientSignals = () => {
  try {
    const n = navigator || {};
    return {
      webdriver: n.webdriver === true,
      headlessUA: /headless/i.test(n.userAgent || ""),
      languages: (n.languages || []).length,
      cores: n.hardwareConcurrency || 0,
    };
  } catch (e) { return {}; }
};

// ── Proof-of-Work ─────────────────────────────────────────────
// 서버가 준 seed에 대해 sha256(seed:nonce)의 선행 0비트가 요구치 이상인 nonce를 찾는다.
// 사람이 문제를 푸는 수 초 동안 워커가 백그라운드로 계산 → 체감 지연 0. 봇은 매 요청마다 이 비용을 치른다.
function _rotr(x, n) { return (x >>> n) | (x << (32 - n)); }
function _sha256(bytes) {
  const K = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  let h0=0x6a09e667,h1=0xbb67ae85,h2=0x3c6ef372,h3=0xa54ff53a,h4=0x510e527f,h5=0x9b05688c,h6=0x1f83d9ab,h7=0x5be0cd19;
  const l = bytes.length, bitLen = l * 8, klen = (((l + 1 + 8) + 63) & ~63);
  const m = new Uint8Array(klen); m.set(bytes); m[l] = 0x80;
  const dv = new DataView(m.buffer);
  dv.setUint32(klen - 4, bitLen >>> 0, false); dv.setUint32(klen - 8, Math.floor(bitLen / 0x100000000), false);
  const w = new Uint32Array(64);
  for (let off = 0; off < klen; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4, false);
    for (let i = 16; i < 64; i++) {
      const s0 = _rotr(w[i-15],7) ^ _rotr(w[i-15],18) ^ (w[i-15] >>> 3);
      const s1 = _rotr(w[i-2],17) ^ _rotr(w[i-2],19) ^ (w[i-2] >>> 10);
      w[i] = (w[i-16] + s0 + w[i-7] + s1) | 0;
    }
    let a=h0,b=h1,c=h2,d=h3,e=h4,f=h5,g=h6,hh=h7;
    for (let i = 0; i < 64; i++) {
      const S1 = _rotr(e,6) ^ _rotr(e,11) ^ _rotr(e,25), ch = (e & f) ^ (~e & g);
      const t1 = (hh + S1 + ch + K[i] + w[i]) | 0;
      const S0 = _rotr(a,2) ^ _rotr(a,13) ^ _rotr(a,22), maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) | 0;
      hh=g; g=f; f=e; e=(d+t1)|0; d=c; c=b; b=a; a=(t1+t2)|0;
    }
    h0=(h0+a)|0; h1=(h1+b)|0; h2=(h2+c)|0; h3=(h3+d)|0; h4=(h4+e)|0; h5=(h5+f)|0; h6=(h6+g)|0; h7=(h7+hh)|0;
  }
  return [h0,h1,h2,h3,h4,h5,h6,h7];
}
function _lzbits(words) { let n = 0; for (let i = 0; i < words.length; i++) { const x = words[i] >>> 0; if (x === 0) { n += 32; continue; } n += Math.clz32(x); break; } return n; }
function _powSolve(seed, bits, cap) { const enc = new TextEncoder(); const p = seed + ":"; for (let nonce = 0; nonce < cap; nonce++) { if (_lzbits(_sha256(enc.encode(p + nonce))) >= bits) return String(nonce); } return null; }
// 워커 본문의 _powSolve 호출은 "문자열"이라 미니파이 때 함수명과 어긋난다(ReferenceError→워커 throw
// →메인스레드 fallback으로 떨어져 느려짐+콘솔에러). 대입으로 이름을 워커 스코프에 고정한다(named fn expr).
const _powWorkerSrc = `${_rotr.toString()}\n${_sha256.toString()}\n${_lzbits.toString()}\nconst _powSolve = ${_powSolve.toString()};\nself.onmessage=function(e){self.postMessage(_powSolve(e.data.seed,e.data.bits,20000000));};`;
const solvePow = (pow) => new Promise((resolve) => {
  if (!pow || !pow.seed) { resolve(null); return; }
  const { seed, bits } = pow;
  try {
    const url = URL.createObjectURL(new Blob([_powWorkerSrc], { type: "application/javascript" }));
    const worker = new Worker(url); let settled = false;
    worker.onmessage = (e) => { if (settled) return; settled = true; resolve(e.data || null); worker.terminate(); URL.revokeObjectURL(url); };
    worker.onerror = () => { if (settled) return; settled = true; URL.revokeObjectURL(url); try { resolve(_powSolve(seed, bits, 20000000)); } catch (_) { resolve(null); } };
    worker.postMessage({ seed, bits });
  } catch (err) { try { resolve(_powSolve(seed, bits, 20000000)); } catch (_) { resolve(null); } }
});

function CaptchaApp() {
  const [challenge, setChallenge] = useState(null);
  const [siteKey, setSiteKey] = useState("");
  const [selected, setSelected] = useState([]);
  const [dragging, setDragging] = useState(null);
  const [dragPoint, setDragPoint] = useState(null);
  const [message, setMessage] = useState("보안 문제를 준비하고 있습니다.");
  const [token, setToken] = useState("");
  const [behaviorDebug, setBehaviorDebug] = useState(null);
  const [startedAt, setStartedAt] = useState(0);
  const [remaining, setRemaining] = useState(60);
  const deadlineRef = useRef(0);
  const stageRef = useRef(null);
  const dropRef = useRef(null);
  const lastMove = useRef(0);
  const powRef = useRef(null);
  const embedOriginsRef = useRef([]);  // ③ 서버가 내려준 허용 임베드 출처(postMessage 대상 검증용)
  const challengeRef = useRef(null);
  const siteKeyRef = useRef("");
  const behaviorNonceRef = useRef("");
  const pendingEventsRef = useRef([]);
  const nextEventSeqRef = useRef(0);
  const nextBatchSeqRef = useRef(0);
  const previousReceiptRef = useRef(null);
  const flushTimerRef = useRef(null);
  const flushPromiseRef = useRef(null);
  const collectorGenerationRef = useRef(0);
  const behaviorTransportFailedRef = useRef(false);

  const flushBehavior = async () => {
    if (flushPromiseRef.current) return flushPromiseRef.current;
    if (behaviorTransportFailedRef.current) return false;
    const activeChallenge = challengeRef.current;
    const nonce = behaviorNonceRef.current;
    const generation = collectorGenerationRef.current;
    if (activeChallenge?.behavior_event_transport === "off") return true;
    if (!activeChallenge || !nonce || !pendingEventsRef.current.length) return true;

    const run = async () => {
      try {
        while (pendingEventsRef.current.length) {
          if (generation !== collectorGenerationRef.current) return false;
          const batch = pendingEventsRef.current.slice(0, activeChallenge.behavior_batch_max_events || 32);
          const result = await api(`/api/captcha/challenges/${activeChallenge.challenge_id}/behavior-batches`, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-Captcha-Site-Key": siteKeyRef.current },
            body: JSON.stringify({
              session_id: sessionId(),
              nonce,
              batch_seq: nextBatchSeqRef.current,
              previous_receipt: previousReceiptRef.current,
              events: batch,
            }),
          });
          if (!result.accepted || !result.receipt) throw new Error("behavior_batch_rejected");
          if (generation !== collectorGenerationRef.current) return false;
          pendingEventsRef.current.splice(0, batch.length);
          nextBatchSeqRef.current += 1;
          previousReceiptRef.current = result.receipt;
        }
        return true;
      } catch {
        behaviorTransportFailedRef.current = true;
        return false;
      }
    };

    const promise = run();
    flushPromiseRef.current = promise;
    try {
      return await promise;
    } finally {
      if (flushPromiseRef.current === promise) flushPromiseRef.current = null;
      if (pendingEventsRef.current.length && !behaviorTransportFailedRef.current) scheduleBehaviorFlush();
    }
  };

  const scheduleBehaviorFlush = () => {
    if (flushTimerRef.current || !challengeRef.current || behaviorTransportFailedRef.current) return;
    const interval = challengeRef.current.behavior_batch_interval_ms || 200;
    flushTimerRef.current = window.setTimeout(() => {
      flushTimerRef.current = null;
      void flushBehavior();
    }, interval);
  };
  const embedParams = new URLSearchParams(location.search);
  const embed = embedParams.get("embed") === "1";
  const lectureId = embedParams.get("lecture") || embedParams.get("lecture_id") || null;
  const purpose = embedParams.get("purpose") || (embed ? "lecture" : "signup");
  // 수집 세션 참여자 코드(?participant=). 세션 내 유지 → 참여자 단위 FRR 산출용.
  // 없으면 서버가 session_id 로 폴백하므로 일반 사용자는 영향이 없다.
  const participantId = (() => {
    const p = embedParams.get("participant");
    try {
      if (p) { sessionStorage.setItem("catchap-participant", p); return p; }
      return sessionStorage.getItem("catchap-participant") || null;
    } catch (e) { return p || null; }
  })();

  const record = (type, objectId, event) => {
    if (challengeRef.current?.behavior_event_transport === "off") return;
    const now = Date.now();
    if (type === "pointer_move" && now - lastMove.current < 40) return;
    if (type === "pointer_move") lastMove.current = now;
    const rect = stageRef.current?.getBoundingClientRect();
    pendingEventsRef.current.push({ seq: nextEventSeqRef.current++, type, object_id: objectId || null,
      x: rect && event ? Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) : null,
      y: rect && event ? Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) : null,
      timestamp_ms: now,
      // PointerEvent 원천 신호(궤적 밖 특징). 미지원/이벤트 없으면 null — 실패가 아니다.
      // isTrusted=합성 이벤트 판별, coalesced_count=실브라우저 없이 못 만드는 값.
      is_trusted: event?.isTrusted ?? null,
      pointer_type: event?.pointerType ?? null,
      pressure: event?.pressure ?? null,
      pointer_width: event?.width ?? null,
      pointer_height: event?.height ?? null,
      buttons: event?.buttons ?? null,
      is_primary: event?.isPrimary ?? null,
      event_timestamp: event?.timeStamp ?? null,
      // React SyntheticEvent는 속성만 복사하고 메서드는 안 넘김 → nativeEvent 경유. 미지원 브라우저는 null.
      coalesced_count: event?.nativeEvent?.getCoalescedEvents?.().length ?? null });
    scheduleBehaviorFlush();
  };

  const load = async () => {
    try {
      setMessage("새 문제를 불러오는 중입니다."); setToken(""); setSelected([]); setBehaviorDebug(null);
      collectorGenerationRef.current += 1;
      if (flushTimerRef.current) window.clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
      challengeRef.current = null;
      behaviorNonceRef.current = "";
      pendingEventsRef.current = [];
      nextEventSeqRef.current = 0;
      nextBatchSeqRef.current = 0;
      previousReceiptRef.current = null;
      lastMove.current = 0;
      behaviorTransportFailedRef.current = false;
      const config = siteKey ? { siteKey } : await api("/api/config");
      setSiteKey(config.siteKey);
      if (config.embedOrigins) embedOriginsRef.current = config.embedOrigins;  // ③ 최초 1회 수신
      setSiteKey(config.siteKey); siteKeyRef.current = config.siteKey;
      const row = await api("/api/captcha/challenges", { method: "POST", headers: { "Content-Type": "application/json", "X-Captcha-Site-Key": config.siteKey },
        body: JSON.stringify({ purpose, risk_level: "high", session_id: sessionId(), lecture_id: lectureId }) });
      powRef.current = solvePow(row.pow);  // 사람이 문제 푸는 동안 백그라운드로 연산 퍼즐 해결
      setChallenge(row); challengeRef.current = row; behaviorNonceRef.current = row.behavior_nonce;
      setStartedAt(performance.now()); setMessage(row.instruction);
      deadlineRef.current = Date.now() + 60000; setRemaining(60);
      record("challenge_loaded", null, null);
    } catch (error) { setMessage(error.message); }
  };
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (!challenge || token) return;
    const id = setInterval(() => {
      const rem = Math.max(0, Math.ceil((deadlineRef.current - Date.now()) / 1000));
      setRemaining(rem);
      if (rem <= 0) { clearInterval(id); setMessage("시간이 초과되었습니다. 새 문제를 불러옵니다."); window.setTimeout(load, 1200); }
    }, 250);
    return () => clearInterval(id);
  }, [challenge, token]);

  const moveDrag = (event) => {
    if (!dragging) return;
    setDragPoint({ x: event.clientX, y: event.clientY });
    record("pointer_move", dragging.object_id, event);
  };
  const drop = (event) => {
    if (!dragging) return;
    const zone = dropRef.current?.getBoundingClientRect();
    if (!zone) return;
    const inside = event.clientX >= zone.left && event.clientX <= zone.right && event.clientY >= zone.top && event.clientY <= zone.bottom;
    record("drop", dragging.object_id, event);
    if (inside) {
      record("selection_add",dragging.object_id,event);
      setSelected((rows) => rows.includes(dragging.object_id) ? rows : [...rows, dragging.object_id]);
    }
    setDragging(null); setDragPoint(null);
  };
  const cancelDrag = (event) => {
    if (dragging) record("pointer_cancel", dragging.object_id, event);
    setDragging(null); setDragPoint(null);
  };
  const remove = (id) => { setSelected((rows) => rows.filter((value) => value !== id)); record("object_removed", id); };
  const clearAll = () => { selected.forEach((id) => record("object_removed", id)); setSelected([]); };
  // ③ postMessage 대상 출처를 "*"로 두면 악성 페이지가 캡차를 iframe으로 끼워 사람이 풀게 한 뒤
  // 토큰을 가로챌 수 있다. 부모 출처(referrer)를 서버 허용목록으로 검증해 그 출처로만 보낸다.
  const safeTargetOrigin = () => {
    let parentOrigin = "";
    try { parentOrigin = document.referrer ? new URL(document.referrer).origin : ""; } catch (e) { parentOrigin = ""; }
    const allow = embedOriginsRef.current || [];
    if (allow.includes("*")) return parentOrigin || "*";            // 명시적 전체허용(비권장)
    if (allow.length) return allow.includes(parentOrigin) ? parentOrigin : null;  // 허용목록: 일치할 때만
    return parentOrigin || null;                                    // 미설정: 부모 출처로만, "*" 금지
  };
  const verify = async () => {
    if (!challenge || !selected.length) { setMessage("옮길 객체를 먼저 선택해주세요."); return; }
    try {
      record("submit", null, null);
      if (!(await flushBehavior())) {
        setMessage("행동 데이터를 서버에 전송하지 못했습니다. 다시 시도해주세요.");
        return;
      }
      if (powRef.current) setMessage("확인 중입니다…");
      const powNonce = powRef.current ? await powRef.current : null;  // 워커가 아직이면 잠깐 대기
      const result = await api(`/api/captcha/challenges/${challenge.challenge_id}/verify`, { method: "POST",
        headers: { "Content-Type": "application/json", "X-Captcha-Site-Key": siteKey },
        body: JSON.stringify({ selected_object_ids: selected, session_id: sessionId(),
          duration_ms: Math.max(100, Math.round(performance.now() - startedAt)), client_signals: clientSignals(), pow_nonce: powNonce, participant_id: participantId }) });
      setBehaviorDebug(result.behavior_debug || null);
      if (result.success) {
        setToken(result.captcha_token);
        setMessage("인증되었습니다.");
        if (embed && window.parent !== window) {
          const target = safeTargetOrigin();  // 허용목록 검증 실패 시 토큰 미전송(탈취 차단)
          if (target) window.parent.postMessage({ type: "catchap-verified", token: result.captcha_token, lecture_id: lectureId }, target);
        }
        return;
      }
      setMessage(result.blocked?"자동화 의심 행동이 감지되었습니다.":result.step_up?"추가 인증이 필요합니다.":result.pow_failed?"확인에 실패했습니다. 다시 시도합니다.":"인증에 실패하였습니다.");
      window.setTimeout(load, 1200);
    } catch (error) {
      setMessage("인증에 실패하였습니다.");
      window.setTimeout(load, 1200);
    }
  };

  return <div className="cc-page">
    <div className="cc-card" aria-live="polite">
      <div className="cc-top">
        <div className="cc-head">
          <div className="cc-brand"><span className="cc-logo">C</span><span className="cc-brandname">CatChap Guard</span></div>
          <span className="cc-verif">Verification</span>
        </div>
        <h1 className="cc-title">계속하기 전에, 확인이 필요해요</h1>
        <p className="cc-sub">{challenge ? message : "강의에 집중하고 있는지 간단한 문제로 확인합니다."}</p>
      </div>

      <div className="cc-main">
        <div className="cc-rowhead"><span className="cc-tag">문제</span><span className="cc-rowright"><span className={`cc-timer ${remaining<=10?"warn":""}`}>⏱ {Math.floor(remaining/60)}:{String(remaining%60).padStart(2,"0")}</span><button className="cc-link" onClick={load}>문제 바꾸기</button></span></div>
        <div className={`cc-stage ${challenge ? "loaded" : ""}`} ref={stageRef} onPointerMove={moveDrag} onPointerUp={drop} onPointerCancel={cancelDrag}>
          {challenge ? <>
            <img src={challenge.image_url} alt="CAPTCHA 원본 장면" draggable="false" />
            {challenge.objects.filter((obj) => !selected.includes(obj.object_id)).map((obj) => <button key={obj.object_id} className="hit-object"
              style={{ left:`${obj.hit_region[0]*100}%`,top:`${obj.hit_region[1]*100}%`,width:`${obj.hit_region[2]*100}%`,height:`${obj.hit_region[3]*100}%` }}
              onPointerEnter={(e)=>record("object_enter",obj.object_id,e)} onPointerLeave={(e)=>record("object_leave",obj.object_id,e)}
              onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); setDragging(obj); setDragPoint({x:e.clientX,y:e.clientY}); record("pointer_down", obj.object_id, e); record("drag_start", obj.object_id, e); }}
              aria-label="사진 속 객체를 정답존으로 드래그" />)}
          </> : <div className="cc-stage-empty">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.6"/><path d="M21 15l-5-5L5 21"/></svg>
            <span>문제 이미지</span>
          </div>}
        </div>
        <div className="cc-rowhead"><span className="cc-tag">정답존</span>{selected.length > 0 && <button className="cc-link muted" onClick={clearAll}>비우기</button>}</div>
        <div ref={dropRef} className={`cc-zone ${dragging ? "armed" : ""} ${selected.length ? "filled" : ""}`} onPointerUp={drop}
          style={challenge?.drop_zone ? { marginLeft:`${(challenge.drop_zone.x||0)*100}%`, width:`${(challenge.drop_zone.width||1)*100}%` } : undefined}>
          {selected.length
            ? <div className="cc-chips">{selected.map((id) => { const obj=challenge.objects.find((item)=>item.object_id===id); return <button key={id} className="cc-chip" onClick={()=>remove(id)} title="선택 취소"><img src={obj.preview_url} alt="선택한 객체"/><span className="cc-chip-x">×</span></button>; })}</div>
            : <span className="cc-zone-empty">이미지에서 정답 객체를 이곳으로 드래그하세요</span>}
        </div>
      </div>

      {dragging && dragPoint && <img className="drag-ghost" style={{left:dragPoint.x,top:dragPoint.y}} src={dragging.preview_url} alt="드래그 중인 객체" />}

      <div className="cc-bottom">
        {token
          ? <div className="cc-done" role="status">확인되었습니다 · 잠시 후 이어집니다</div>
          : <button className="cc-verify" onClick={verify} disabled={!challenge || !selected.length || remaining<=0}>확인</button>}
        <div className="cc-guard"><span>이 확인은 <strong>CatChap Guard</strong>로 보호됩니다</span><a className="cc-admin-link" href="/admin">라벨링 콘솔</a></div>
        {behaviorDebug && <section className="cc-debug" aria-label="로컬 행동 모델 점수">
          <strong>로컬 행동 점수</strong>
          <span>{behaviorDebug.model_name || "모델 미연결"} · {behaviorDebug.model_version || behaviorDebug.status}</span>
          <dl>
            <div><dt>사람 점수</dt><dd>{behaviorDebug.human_score ?? "-"}</dd></div>
            <div><dt>봇 위험</dt><dd>{behaviorDebug.bot_risk_score ?? "-"}</dd></div>
            <div><dt>위험 등급</dt><dd>{behaviorDebug.risk_level || "-"}</dd></div>
            <div><dt>권고</dt><dd>{behaviorDebug.recommended_action || "-"}</dd></div>
          </dl>
          {behaviorDebug.detail && <small>{behaviorDebug.detail}</small>}
        </section>}
      </div>
    </div>
  </div>;
}

function AdminApp() {
  const [key,setKey]=useState(""); const [items,setItems]=useState([]); const [index,setIndex]=useState(0);
  const [view,setView]=useState("pending");
  const [draft,setDraft]=useState(null); const [message,setMessage]=useState("관리자 키를 입력하세요.");
  const [selectedObjectKey,setSelectedObjectKey]=useState(null);
  const [reviewerName,setReviewerName]=useState(""); const [counts,setCounts]=useState({approved:0,rejected:0});
  const canvasRef=useRef(null); const editRef=useRef(null); const advanceRef=useRef(null);
  const item=items[index];
  const load=async(nextView)=>{ const requested=typeof nextView==="string"?nextView:view; try { const data=await api(`/api/admin/queue?view=${requested}`,{headers:{"X-Captcha-Admin-Key":key}}); setView(requested); setItems(data.items); setIndex(0); setDraft(data.items[0]||null); if(data.reviewer) setReviewerName(data.reviewer); if(data.counts) setCounts(data.counts); const label=requested==="approved"?"승인 완료":requested==="rejected"?"제외":"승인 대기"; setMessage(`${data.items.length}개 ${label} 문항을 불러왔습니다.`); } catch(error){setMessage(error.message);} };
  const advance=()=>{ const remaining=items.filter((row)=>row.queue_id!==draft.queue_id); if(!remaining.length){load(view);return;} setItems(remaining); setIndex(Math.min(index,remaining.length-1)); };
  advanceRef.current=advance;
  const refreshCounts=async()=>{ try{ const c=await api("/api/admin/counts",{headers:{"X-Captcha-Admin-Key":key}}); setCounts(c);}catch(e){} };
  useEffect(()=>{ if(item) setDraft(structuredClone(item)); },[index,items]);
  const currentQid=item?.queue_id;
  useEffect(()=>{ if(!currentQid||!key||view!=="pending") return; const ping=async()=>{ try{ const r=await fetch(`/api/admin/claim/${currentQid}`,{method:"POST",headers:{"X-Captcha-Admin-Key":key}}); const d=await r.json(); if(d&&d.blocked&&advanceRef.current){ setMessage(d.decided?"이미 처리된 문항이라 건너뜁니다.":"다른 검수자가 보는 중이라 건너뜁니다."); advanceRef.current(); } }catch(e){} }; ping(); const t=setInterval(ping,60000); return ()=>clearInterval(t); },[currentQid,key,view]);
  useEffect(()=>{ if(!key||!reviewerName) return; const t=setInterval(refreshCounts,5000); return ()=>clearInterval(t); },[key,reviewerName]);
  const updateObject=(objectKey,patch)=>setDraft((current)=>({...current,objects:current.objects.map((obj)=>obj.object_key===objectKey?{...obj,...patch}:obj)}));
  const clamp=(value,min,max)=>Math.min(max,Math.max(min,value));
  const beginBoxEdit=(event,obj,mode)=>{ event.preventDefault(); event.stopPropagation(); event.currentTarget.setPointerCapture(event.pointerId); setSelectedObjectKey(obj.object_key); editRef.current={objectKey:obj.object_key,mode,startX:event.clientX,startY:event.clientY,box:{x:obj.x,y:obj.y,width:obj.width,height:obj.height}}; };
  const moveBox=(event)=>{ const edit=editRef.current; const canvas=canvasRef.current; if(!edit||!canvas)return; const rect=canvas.getBoundingClientRect(); const dx=(event.clientX-edit.startX)/rect.width; const dy=(event.clientY-edit.startY)/rect.height; let {x,y,width,height}=edit.box; const min=.015;
    if(edit.mode==="move"){x=clamp(x+dx,0,1-width);y=clamp(y+dy,0,1-height);}
    if(edit.mode.includes("e"))width=clamp(width+dx,min,1-x);
    if(edit.mode.includes("s"))height=clamp(height+dy,min,1-y);
    if(edit.mode.includes("w")){const right=x+width;x=clamp(x+dx,0,right-min);width=right-x;}
    if(edit.mode.includes("n")){const bottom=y+height;y=clamp(y+dy,0,bottom-min);height=bottom-y;}
    updateObject(edit.objectKey,{x:+x.toFixed(6),y:+y.toFixed(6),width:+width.toFixed(6),height:+height.toFixed(6)});
  };
  const endBoxEdit=()=>{editRef.current=null;};
  const save=async(status)=>{ try { await api(`/api/admin/reviews/${draft.queue_id}`,{method:"PUT",headers:{"Content-Type":"application/json","X-Captcha-Admin-Key":key},body:JSON.stringify({queue_id:draft.queue_id,reviewer:"web",review_status:status,instruction_ko:draft.instruction_ko,difficulty:draft.difficulty||2,expected_target_count:Number(draft.expected_target_count),objects:draft.objects})}); setMessage(`${status} 상태로 저장했습니다.`); refreshCounts(); if(status==="approved"||status==="rejected"){advance();}else if(index<items.length-1)setIndex(index+1); } catch(error){ setMessage(error.message); if(/이미|처리 중/.test(error.message)) advance(); } };
  const addBox=()=>{const object_key=`manual_${crypto.randomUUID().slice(0,8)}`;setDraft({...draft,objects:[...draft.objects,{object_key,label:"새 객체",x:.35,y:.25,width:.2,height:.35,role:"ambiguous"}]});setSelectedObjectKey(object_key);};
  if(!items.length)return <main className="admin-login"><div className="brand-mark">C</div><h1>라벨링 콘솔</h1><p>{message}</p><input type="password" value={key} onChange={(e)=>setKey(e.target.value)} placeholder="관리자 키"/><button className="primary" onClick={load}>후보 불러오기</button><a href="/">사용자 화면으로</a></main>;
  const targetCount=draft.objects.filter((obj)=>obj.role==="target").length;
  return <main className="admin-shell"><header className="admin-head"><div><p className="kicker">LABELING CONSOLE</p><h1>객체 관계 검수</h1></div><div><button className="outline" onClick={()=>load("pending")}>승인 대기</button><button className="outline" onClick={()=>load("approved")}>승인 완료</button><button className="outline" onClick={()=>load("rejected")}>제외</button><span>{reviewerName?`${reviewerName} · `:""}승인 {counts.approved} · 제외 {counts.rejected} · {index+1}/{items.length}</span><a href="/">사용자 화면</a></div></header>
    <section className="review-grid"><div className="review-canvas" ref={canvasRef} onPointerMove={moveBox} onPointerUp={endBoxEdit} onPointerCancel={endBoxEdit}><AdminImage path={draft.image_path} adminKey={key}/>
      {draft.objects.map((obj)=><div key={obj.object_key} className={`bbox ${obj.role} ${selectedObjectKey===obj.object_key?"selected":""}`} style={{left:`${obj.x*100}%`,top:`${obj.y*100}%`,width:`${obj.width*100}%`,height:`${obj.height*100}%`}} onPointerDown={(e)=>beginBoxEdit(e,obj,"move")}><span>{obj.label} · {obj.role}</span>{["nw","ne","sw","se"].map((corner)=><i key={corner} className={`resize-handle ${corner}`} onPointerDown={(e)=>beginBoxEdit(e,obj,corner)} />)}</div>)}</div>
      <aside><p className="question-en">{draft.question_en}</p><textarea value={draft.instruction_ko} onChange={(e)=>setDraft({...draft,instruction_ko:e.target.value})}/><div className="target-count"><span>target 수</span><strong className={targetCount===Number(draft.expected_target_count)?"match":""}>{targetCount} / <input type="number" min="0" max="50" className="tc-input" value={draft.expected_target_count} onChange={(e)=>setDraft({...draft,expected_target_count:Number(e.target.value)})}/></strong></div><p className="hint">관계 힌트: {draft.relationship_hints?.map((r)=>r.predicate).join(", ")||"없음 — 육안 검수 필요"}</p><p className="edit-help">박스를 드래그해 이동하고, 네 모서리를 잡아 크기를 조절하세요.</p><button className="outline" onClick={addBox}>+ 새 bbox</button></aside></section>
    <section className="object-table"><div className="table-head"><span>객체 라벨</span><span>역할</span><span>정규화 bbox (x, y, w, h)</span><span></span></div>{draft.objects.map((obj)=><div className={`object-row ${selectedObjectKey===obj.object_key?"selected":""}`} key={obj.object_key} onClick={()=>setSelectedObjectKey(obj.object_key)}><label className="label-editor"><input value={obj.label} onChange={(e)=>updateObject(obj.object_key,{label:e.target.value})} aria-label="객체 라벨"/><small>{obj.object_key}</small></label><select value={obj.role} onChange={(e)=>updateObject(obj.object_key,{role:e.target.value})}>{["target","decoy","ambiguous","invalid"].map((role)=><option key={role}>{role}</option>)}</select><div className="coords">{["x","y","width","height"].map((name)=><input key={name} type="number" min="0" max="1" step="0.001" value={obj[name]} onChange={(e)=>updateObject(obj.object_key,{[name]:Number(e.target.value)})}/>)}</div><button className="delete" onClick={()=>setDraft({...draft,objects:draft.objects.filter((row)=>row.object_key!==obj.object_key)})}>삭제</button></div>)}</section>
    <footer className="review-actions"><p>{message}</p><div><button className="outline" onClick={()=>setIndex(Math.max(0,index-1))}>이전</button><button className="danger" onClick={()=>save("rejected")}>제외</button><button className="outline" onClick={()=>save("labeled")}>임시 저장</button><button className="primary" onClick={()=>save("approved")}>승인 및 다음</button></div></footer></main>;
}

function AdminImage({path,adminKey}) {
  const [src,setSrc]=useState("");
  useEffect(()=>{ let current=""; fetch(`/api/admin/assets/${path}`,{headers:{"X-Captcha-Admin-Key":adminKey}}).then((response)=>{if(!response.ok)throw new Error("이미지를 불러오지 못했습니다.");return response.blob();}).then((blob)=>{current=URL.createObjectURL(blob);setSrc(current);}); return()=>{if(current)URL.revokeObjectURL(current);}; },[path,adminKey]);
  return src ? <img src={src} alt="라벨링 대상"/> : <div className="image-loading">이미지 불러오는 중</div>;
}

createRoot(document.getElementById("root")).render(location.pathname.startsWith("/admin") ? <AdminApp/> : <CaptchaApp/>);
