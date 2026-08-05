/*
 * CatChap Guard 위젯 로더
 * 사용 예 (인강 페이지):
 *   <div id="catchap"></div>
 *   <script>
 *     window.catchapOnVerified = function (token, ctx) {
 *       // ctx = { lectureId, sessionId, purpose }
 *       // token·ctx.sessionId·ctx.purpose 를 인강 서버로 보내 POST /api/verify-token 으로 검증.
 *       // (session_id·purpose 가 없으면 검증이 실패한다 — 토큰이 세션에 묶여 있음)
 *     };
 *   </script>
 *   <script src="https://<캡챠호스트>/widget.js"
 *           data-target="#catchap" data-lecture-id="LEC-2026-01"></script>
 */
(function () {
  var script = document.currentScript;
  if (!script) return;
  var origin = new URL(script.src).origin;
  // 경로 프록시 지원: widget.js가 /captcha-api/widget.js 처럼 하위경로로 서빙돼도
  // iframe을 같은 베이스경로에서 로드한다(https 플랫폼의 mixed-content 회피용 동일도메인 프록시).
  var base = script.src.replace(/\/widget\.js(\?.*)?$/, "");
  var lecture = script.getAttribute("data-lecture-id") || "";

  var target = script.getAttribute("data-target");
  var container = target ? document.querySelector(target) : null;
  if (!container) {
    container = document.createElement("div");
    script.parentNode.insertBefore(container, script);
  }

  var params = new URLSearchParams({ embed: "1", lecture: lecture });
  var iframe = document.createElement("iframe");
  iframe.src = base + "/?" + params.toString();
  iframe.title = "CatChap 인증";
  iframe.style.cssText =
    "width:100%;max-width:620px;height:760px;border:0;display:block;margin:0 auto;";
  container.appendChild(iframe);

  window.addEventListener("message", function (event) {
    if (event.origin !== origin || !event.data) return;
    if (event.data.type === "catchap-verified") {
      if (typeof window.catchapOnVerified === "function") {
        // token 검증(POST /api/verify-token)에 session_id·purpose가 반드시 필요하다.
        window.catchapOnVerified(event.data.token, {
          lectureId: event.data.lecture_id,
          sessionId: event.data.session_id,
          purpose: event.data.purpose,
        });
      }
    }
  });
})();
