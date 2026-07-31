# 행동 이벤트 스키마 (BehaviorEvent) — 프론트가 보내는 필드

프론트 `src/main.jsx` `record()`가 만들고, 서버 `app/main.py` `BehaviorEvent`가 검증한다.
배치채널(`behavior-batches`)과 verify 둘 다 이 이벤트 배열을 쓴다.

## 기존 필드
| wire 필드 | 타입 | 의미 |
|---|---|---|
| `type` | enum | challenge_loaded/pointer_down/drag_start/pointer_move/drop/selection_add/object_removed/submit/… |
| `object_id` | str\|null | 대상 임시 객체 ID(`tmp_…`) |
| `x`,`y` | float\|null (0..1) | 스테이지 정규화 좌표 |
| `timestamp_ms` | int | `Date.now()` epoch ms |

## 신규 필드 — PointerEvent 원천 신호 (2026-07-31, sw 요청 ④)
전부 **nullable**, JS는 `event?.X ?? null`로 감쌈. 미지원/이벤트 없으면 **null(실패 아님)**. 서버 검증 느슨(정상 풀이 거부 안 함).

| wire 필드 | JS 원천 | 타입 | 의미 / 봇 판별 가치 |
|---|---|---|---|
| `is_trusted` | `event.isTrusted` | bool\|null | 브라우저 실제 이벤트=true, **JS 합성=false** ← 스크립트 봇 직격 |
| `pointer_type` | `event.pointerType` | str\|null | "mouse"/"pen"/"touch" |
| `pressure` | `event.pressure` | float\|null | 필압 0..1(마우스 보통 0 또는 0.5) |
| `pointer_width` | `event.width` | float\|null | 접촉 폭 px(터치/펜) |
| `pointer_height` | `event.height` | float\|null | 접촉 높이 px |
| `buttons` | `event.buttons` | int\|null | 눌린 버튼 비트마스크 |
| `is_primary` | `event.isPrimary` | bool\|null | 주 포인터 여부 |
| `event_timestamp` | `event.timeStamp` | float\|null | 페이지 기준 고해상 ms(단조·서브ms) — 합성 타이밍과 대비 |
| `coalesced_count` | `event.getCoalescedEvents?.().length` | int\|null | 합쳐진 원시 move 수 ← **실브라우저 없이 생성 불가** |

## ⚠️ AI/배치 측 맞출 것 (sw)
1. **배치 canonical/payload_hash**: 새 필드가 해시 대상에 들어가면 `_canonical_events()`가 이들을 포함·정규화해야 함. `pressure`·`event_timestamp`는 float이니 **b04c315(float 정밀도) 재발 주의** — 반올림 자릿수 통일. `null` 필드는 canonical 표현을 프론트/서버 동일하게(생략 or 고정).
2. **sw-captcha `BehaviorBatchEvent`**: 위 신규 필드를 동일 이름·타입으로 추가(안 그러면 드롭됨).
3. **특징 추출**: `is_trusted==false` 비율, `coalesced_count` 분포, `pointer_type`, `pressure` 변화량 등을 특징으로. 궤적 밖 신호라 "사람속도·다중드래그 봇"에도 유효할 것으로 기대.

배포: 프론트 재빌드 필요(PoW 워커처럼). sw 병합 후 함께 배포.
