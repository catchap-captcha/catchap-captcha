import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiTarget = process.env.VITE_API_TARGET || "http://127.0.0.1:18000";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "static/dist", emptyOutDir: true },
  server: {
    proxy: {
      // 프로덕션 캡차는 ALLOWED_ORIGINS 로 브라우저가 아닌 직접 호출을 막는다
      // (main.py check_origin). 127.0.0.1 은 당연히 목록에 없고, 그건 옳은 동작이라
      // 서버 설정을 바꾸는 대신 프록시가 실제 서비스 오리진을 붙여 보낸다.
      // 조준 구간 실험은 프론트만 바꾸는 것이 목적이므로 서버는 손대지 않는다.
      "/api": {
        target: apiTarget,
        changeOrigin: true,
        configure(proxy) {
          proxy.on("proxyReq", (proxyReq) => {
            proxyReq.setHeader("origin", "https://www.catchap5.com");
            proxyReq.setHeader("referer", "https://www.catchap5.com/");
          });
        },
      },
      "/health": { target: apiTarget, changeOrigin: true },
      // 조준 구간 수집 실험(2026-08-08). 캡차 서버가 아니라 로컬 수집기로 간다 —
      // 서버의 배치 검증은 정해진 타입만 받으므로 새 타입을 섞으면 배치가 거부된다.
      "/collect-aim": { target: "http://127.0.0.1:18100", changeOrigin: true },
    },
  },
});
