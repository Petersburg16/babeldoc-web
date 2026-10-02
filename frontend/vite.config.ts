import { svelte } from '@sveltejs/vite-plugin-svelte';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

const backend = process.env.BDW_BACKEND ?? 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [tailwindcss(), svelte()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: backend, changeOrigin: false },
    },
  },
  build: {
    target: 'es2022',
    chunkSizeWarningLimit: 800,
  },
});
