/*
 * CatChap Guard 위젯 로더
 * 사용 예 (인강 페이지):
 *   <div id="catchap"></div>
 *   <script>
 *     window.catchapOnVerified = function (token, lectureId) {
 *       // 이 token 을 인강 서버로 보내 /api/verify-token 으로 검증하세요.
 *     };
 *   </script>
 *   <script src="https://<캡챠호스트>/widget.js"
 *           data-target="#catchap" data-lecture-id="LEC-2026-01"></script>
 */
(function () {
  var script = document.currentScript;
  if (!script) return;
  var origin = new URL(script.src).origin;
  var lecture = script.getAttribute("data-lecture-id") || "";

  var target = script.getAttribute("data-target");
  var container = target ? document.querySelector(target) : null;
  if (!container) {
    container = document.createElement("div");
    script.parentNode.insertBefore(container, script);
  }

  var params = new URLSearchParams({ embed: "1", lecture: lecture });
  var iframe = document.createElement("iframe");
  iframe.src = origin + "/?" + params.toString();
  iframe.title = "CatChap 인증";
  iframe.style.cssText =
    "width:100%;max-width:620px;height:760px;border:0;display:block;margin:0 auto;";
  container.appendChild(iframe);

  window.addEventListener("message", function (event) {
    if (event.origin !== origin || !event.data) return;
    if (event.data.type === "catchap-verified") {
      if (typeof window.catchapOnVerified === "function") {
        window.catchapOnVerified(event.data.token, event.data.lecture_id);
      }
    }
  });
})();
