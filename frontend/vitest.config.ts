import { defineConfig } from "vitest/config";
import path from "node:path";

/**
 * Vitest 설정 — Playwright e2e 스펙(e2e/**)을 제외하고 src/ 하위 unit test만 수집.
 * - include: src/**의 .test.ts 만 채집 — Playwright spec 파일은 e2e/에만 존재하므로 충돌 없음.
 * - alias: "@/*" → src/* (tsconfig paths와 동일, Next.js alias 호환).
 */
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.{test,spec}.ts", "src/**/*.{test,spec}.tsx"],
    exclude: [
      "e2e/**",
      "node_modules/**",
      "dist/**",
      ".next/**",
      "test-results/**",
      "playwright-report/**",
    ],
    globals: false,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
});
