import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  root: '.',
  publicDir: 'public',
  build: {
    outDir: 'dist',
    sourcemap: true,
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks: {
          three: ['three'],
          vendor: ['preact', 'preact/compat', 'zustand'],
        },
      },
    },
  },
  server: {
    port: 8091,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  resolve: {
    alias: {
      'react':              'preact/compat',
      'react-dom/test-utils': 'preact/test-utils',
      'react-dom':          'preact/compat',
      'react/jsx-runtime':  'preact/jsx-runtime',
    },
  },
});
