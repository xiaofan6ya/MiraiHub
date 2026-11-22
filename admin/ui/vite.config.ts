import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

const API_TARGET = 'http://127.0.0.1:8790'

export default defineConfig({
  base: '/admin/v2/',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': '/src',
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        index: 'index.html',
        login: 'login.html',
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: false },
      '/admin/assets': { target: API_TARGET, changeOrigin: false },
      '/static': { target: API_TARGET, changeOrigin: false },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.spec.ts', 'src/**/*.spec.ts'],
    css: false,
  },
})
