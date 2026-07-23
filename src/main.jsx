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

function CaptchaApp() {
  const [challenge, setChallenge] = useState(null);
  const [siteKey, setSiteKey] = useState("");
  const [selected, setSelected] = useState([]);
  const [dragging, setDragging] = useState(null);
  const [dragPoint, setDragPoint] = useState(null);
  const [message, setMessage] = useState("보안 문제를 준비하고 있습니다.");
  const [token, setToken] = useState("");
  const [events, setEvents] = useState([]);
  const [startedAt, setStartedAt] = useState(0);
  const stageRef = useRef(null);
  const dropRef = useRef(null);
  const lastMove = useRef(0);

  const record = (type, objectId, event) => {
    const now = Date.now();
    if (type === "pointer_move" && now - lastMove.current < 40) return;
    if (type === "pointer_move") lastMove.current = now;
    const rect = stageRef.current?.getBoundingClientRect();
    setEvents((rows) => [...rows.slice(-550), { type, object_id: objectId || null,
      x: rect && event ? Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) : null,
      y: rect && event ? Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) : null,
      timestamp_ms: now }]);
  };

  const load = async () => {
    try {
      setMessage("새 문제를 불러오는 중입니다."); setToken(""); setSelected([]); setEvents([]);
      const config = siteKey ? { siteKey } : await api("/api/config");
      setSiteKey(config.siteKey);
      const row = await api("/api/captcha/challenges", { method: "POST", headers: { "Content-Type": "application/json", "X-Captcha-Site-Key": config.siteKey },
        body: JSON.stringify({ purpose: "signup", risk_level: "high", session_id: sessionId() }) });
      setChallenge(row); setStartedAt(performance.now()); setMessage(row.instruction);
      setEvents([{ type: "challenge_loaded", object_id: null, x: null, y: null, timestamp_ms: Date.now() }]);
    } catch (error) { setMessage(error.message); }
  };
  useEffect(() => { load(); }, []);

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
  const verify = async () => {
    if (!challenge || !selected.length) { setMessage("옮길 객체를 먼저 선택해주세요."); return; }
    try {
      const submitEvent={type:"submit",object_id:null,x:null,y:null,timestamp_ms:Date.now()};
      const payloadEvents=[...events.slice(-598),submitEvent]; setEvents(payloadEvents);
      const result = await api(`/api/captcha/challenges/${challenge.challenge_id}/verify`, { method: "POST",
        headers: { "Content-Type": "application/json", "X-Captcha-Site-Key": siteKey },
        body: JSON.stringify({ selected_object_ids: selected, session_id: sessionId(),
          duration_ms: Math.max(100, Math.round(performance.now() - startedAt)), events:payloadEvents }) });
      if (result.success) {
        setToken(result.captcha_token);
        setMessage("인증되었습니다.");
        return;
      }
      setMessage(result.blocked?"자동화 의심 행동이 감지되었습니다.":result.step_up?"추가 인증이 필요합니다.":"인증에 실패하였습니다.");
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
        <p className="cc-sub">{message}</p>
      </div>

      {challenge && <div className="cc-main">
        <div className="cc-rowhead"><span className="cc-tag">문제</span><button className="cc-link" onClick={load}>문제 바꾸기</button></div>
        <div className="cc-stage" ref={stageRef} onPointerMove={moveDrag} onPointerUp={drop} onPointerCancel={cancelDrag}>
          <img src={challenge.image_url} alt="CAPTCHA 원본 장면" draggable="false" />
          {challenge.objects.filter((obj) => !selected.includes(obj.object_id)).map((obj) => <button key={obj.object_id} className="hit-object"
            style={{ left:`${obj.hit_region[0]*100}%`,top:`${obj.hit_region[1]*100}%`,width:`${obj.hit_region[2]*100}%`,height:`${obj.hit_region[3]*100}%` }}
            onPointerEnter={(e)=>record("object_enter",obj.object_id,e)} onPointerLeave={(e)=>record("object_leave",obj.object_id,e)}
            onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); setDragging(obj); setDragPoint({x:e.clientX,y:e.clientY}); record("pointer_down", obj.object_id, e); record("drag_start", obj.object_id, e); }}
            aria-label="사진 속 객체를 정답존으로 드래그" />)}
        </div>
        <div className="cc-rowhead"><span className="cc-tag">정답존</span>{selected.length > 0 && <button className="cc-link muted" onClick={clearAll}>비우기</button>}</div>
        <div ref={dropRef} className={`cc-zone ${dragging ? "armed" : ""} ${selected.length ? "filled" : ""}`} onPointerUp={drop}>
          {selected.length
            ? <div className="cc-chips">{selected.map((id) => { const obj=challenge.objects.find((item)=>item.object_id===id); return <button key={id} className="cc-chip" onClick={()=>remove(id)} title="선택 취소"><img src={obj.preview_url} alt="선택한 객체"/><span className="cc-chip-x">×</span></button>; })}</div>
            : <span className="cc-zone-empty">이미지에서 정답 객체를 이곳으로 드래그하세요</span>}
        </div>
      </div>}

      {dragging && dragPoint && <img className="drag-ghost" style={{left:dragPoint.x,top:dragPoint.y}} src={dragging.preview_url} alt="드래그 중인 객체" />}

      <div className="cc-bottom">
        {token
          ? <div className="cc-done" role="status">확인되었습니다 · 잠시 후 이어집니다</div>
          : <button className="cc-verify" onClick={verify} disabled={!challenge || !selected.length}>확인</button>}
        <div className="cc-guard"><span>이 확인은 <strong>CatChap Guard</strong>로 보호됩니다</span><a className="cc-admin-link" href="/admin">라벨링 콘솔</a></div>
      </div>
    </div>
  </div>;
}

function AdminApp() {
  const [key,setKey]=useState(""); const [items,setItems]=useState([]); const [index,setIndex]=useState(0);
  const [view,setView]=useState("pending");
  const [draft,setDraft]=useState(null); const [message,setMessage]=useState("관리자 키를 입력하세요.");
  const [selectedObjectKey,setSelectedObjectKey]=useState(null);
  const canvasRef=useRef(null); const editRef=useRef(null);
  const item=items[index];
  const load=async(nextView)=>{ const requested=typeof nextView==="string"?nextView:view; try { const data=await api(`/api/admin/queue?view=${requested}`,{headers:{"X-Captcha-Admin-Key":key}}); setView(requested); setItems(data.items); setIndex(0); setDraft(data.items[0]||null); setMessage(`${data.items.length}개 ${requested==="approved"?"승인 완료":"승인 대기"} 문항을 불러왔습니다.`); } catch(error){setMessage(error.message);} };
  useEffect(()=>{ if(item) setDraft(structuredClone(item)); },[index,items]);
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
  const save=async(status)=>{ try { await api(`/api/admin/reviews/${draft.queue_id}`,{method:"PUT",headers:{"Content-Type":"application/json","X-Captcha-Admin-Key":key},body:JSON.stringify({queue_id:draft.queue_id,reviewer:"admin",review_status:status,instruction_ko:draft.instruction_ko,difficulty:draft.difficulty||2,objects:draft.objects})}); setMessage(`${status} 상태로 저장했습니다.`); if(view==="pending"&&(status==="approved"||status==="rejected")){const remaining=items.filter((row)=>row.queue_id!==draft.queue_id);setItems(remaining);setIndex(Math.min(index,Math.max(0,remaining.length-1)));if(!remaining.length)setDraft(null);}else if(index<items.length-1)setIndex(index+1); } catch(error){setMessage(error.message);} };
  const addBox=()=>{const object_key=`manual_${crypto.randomUUID().slice(0,8)}`;setDraft({...draft,objects:[...draft.objects,{object_key,label:"새 객체",x:.35,y:.25,width:.2,height:.35,role:"ambiguous"}]});setSelectedObjectKey(object_key);};
  if(!items.length)return <main className="admin-login"><div className="brand-mark">C</div><h1>라벨링 콘솔</h1><p>{message}</p><input type="password" value={key} onChange={(e)=>setKey(e.target.value)} placeholder="관리자 키"/><button className="primary" onClick={load}>후보 불러오기</button><a href="/">사용자 화면으로</a></main>;
  const targetCount=draft.objects.filter((obj)=>obj.role==="target").length;
  return <main className="admin-shell"><header className="admin-head"><div><p className="kicker">LABELING CONSOLE</p><h1>객체 관계 검수</h1></div><div><button className="outline" onClick={()=>load("pending")}>승인 대기</button><button className="outline" onClick={()=>load("approved")}>승인 완료</button><span>{index+1} / {items.length}</span><a href="/">사용자 화면</a></div></header>
    <section className="review-grid"><div className="review-canvas" ref={canvasRef} onPointerMove={moveBox} onPointerUp={endBoxEdit} onPointerCancel={endBoxEdit}><AdminImage path={draft.image_path} adminKey={key}/>
      {draft.objects.map((obj)=><div key={obj.object_key} className={`bbox ${obj.role} ${selectedObjectKey===obj.object_key?"selected":""}`} style={{left:`${obj.x*100}%`,top:`${obj.y*100}%`,width:`${obj.width*100}%`,height:`${obj.height*100}%`}} onPointerDown={(e)=>beginBoxEdit(e,obj,"move")}><span>{obj.label} · {obj.role}</span>{["nw","ne","sw","se"].map((corner)=><i key={corner} className={`resize-handle ${corner}`} onPointerDown={(e)=>beginBoxEdit(e,obj,corner)} />)}</div>)}</div>
      <aside><p className="question-en">{draft.question_en}</p><textarea value={draft.instruction_ko} onChange={(e)=>setDraft({...draft,instruction_ko:e.target.value})}/><div className="target-count"><span>target 수</span><strong className={targetCount===draft.expected_target_count?"match":""}>{targetCount} / {draft.expected_target_count}</strong></div><p className="hint">관계 힌트: {draft.relationship_hints?.map((r)=>r.predicate).join(", ")||"없음 — 육안 검수 필요"}</p><p className="edit-help">박스를 드래그해 이동하고, 네 모서리를 잡아 크기를 조절하세요.</p><button className="outline" onClick={addBox}>+ 새 bbox</button></aside></section>
    <section className="object-table"><div className="table-head"><span>객체 라벨</span><span>역할</span><span>정규화 bbox (x, y, w, h)</span><span></span></div>{draft.objects.map((obj)=><div className={`object-row ${selectedObjectKey===obj.object_key?"selected":""}`} key={obj.object_key} onClick={()=>setSelectedObjectKey(obj.object_key)}><label className="label-editor"><input value={obj.label} onChange={(e)=>updateObject(obj.object_key,{label:e.target.value})} aria-label="객체 라벨"/><small>{obj.object_key}</small></label><select value={obj.role} onChange={(e)=>updateObject(obj.object_key,{role:e.target.value})}>{["target","decoy","ambiguous","invalid"].map((role)=><option key={role}>{role}</option>)}</select><div className="coords">{["x","y","width","height"].map((name)=><input key={name} type="number" min="0" max="1" step="0.001" value={obj[name]} onChange={(e)=>updateObject(obj.object_key,{[name]:Number(e.target.value)})}/>)}</div><button className="delete" onClick={()=>setDraft({...draft,objects:draft.objects.filter((row)=>row.object_key!==obj.object_key)})}>삭제</button></div>)}</section>
    <footer className="review-actions"><p>{message}</p><div><button className="outline" onClick={()=>setIndex(Math.max(0,index-1))}>이전</button><button className="danger" onClick={()=>save("rejected")}>제외</button><button className="outline" onClick={()=>save("labeled")}>임시 저장</button><button className="primary" onClick={()=>save("approved")}>승인 및 다음</button></div></footer></main>;
}

function AdminImage({path,adminKey}) {
  const [src,setSrc]=useState("");
  useEffect(()=>{ let current=""; fetch(`/api/admin/assets/${path}`,{headers:{"X-Captcha-Admin-Key":adminKey}}).then((response)=>{if(!response.ok)throw new Error("이미지를 불러오지 못했습니다.");return response.blob();}).then((blob)=>{current=URL.createObjectURL(blob);setSrc(current);}); return()=>{if(current)URL.revokeObjectURL(current);}; },[path,adminKey]);
  return src ? <img src={src} alt="라벨링 대상"/> : <div className="image-loading">이미지 불러오는 중</div>;
}

createRoot(document.getElementById("root")).render(location.pathname.startsWith("/admin") ? <AdminApp/> : <CaptchaApp/>);
