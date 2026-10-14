import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, process.cwd(), 'VITE_'), ...process.env };
  const target = env.VITE_API_PROXY || 'http://localhost:8000';
  const proxy = { '/api': { target, changeOrigin: true } };
  return {
    define: { __APP_VERSION__: JSON.stringify(process.env.APP_VERSION || process.env.npm_package_version || 'dev') },
    server: { proxy },
    preview: { proxy },
    build: { chunkSizeWarningLimit: 1500 },
  };
});
