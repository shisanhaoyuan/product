import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  base: '/assets/dist/',
  build: { outDir: '../web/dist', emptyOutDir: true },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/openapi.json': 'http://127.0.0.1:8765',
      '/docs': 'http://127.0.0.1:8765',
      '^/assets/(api.js|docs.css)$': 'http://127.0.0.1:8765',
    },
  },
});
