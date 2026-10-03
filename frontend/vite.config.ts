import { svelte } from '@sveltejs/vite-plugin-svelte';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

const backend = process.env.BDW_BACKEND ?? 'http://127.0.0.1:8000';

// 与后端 GuardMiddleware 一致：PDF 工具里的 LibreOffice WASM 需要跨源隔离（SharedArrayBuffer）
const isolation = {
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Embedder-Policy': 'require-corp',
  'Cross-Origin-Resource-Policy': 'same-origin',
};

export default defineConfig({
  plugins: [tailwindcss(), svelte()],
  server: {
    port: 5173,
    headers: isolation,
    proxy: {
      '/api': { target: backend, changeOrigin: false },
      '/pdf-assets/': { target: backend, changeOrigin: false },
    },
  },
  preview: { headers: isolation },
  // qpdf、OCR 等引擎放在模块 worker 里跑，worker 内部还会按需 import
  worker: { format: 'es' },
  // 开发时预先打包 PDF 工具的依赖，免得第一次打开某个工具时 Vite 重新优化依赖、整页刷新
  optimizeDeps: {
    include: [
      '@cantoo/pdf-lib',
      '@cantoo/fontkit',
      'pdfjs-dist/legacy/build/pdf.mjs',
      '@neslinesli93/qpdf-wasm',
      'tesseract.js',
      'fflate',
    ],
  },
  build: {
    target: 'es2022',
    chunkSizeWarningLimit: 800,
  },
});
