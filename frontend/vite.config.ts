import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const api = (env.VITE_API_BASE_URL || '').trim();

  if (mode === 'production') {
    if (!api || !api.startsWith('https://') || /localhost|127\.0\.0\.1/i.test(api)) {
      throw new Error(
        'Production build requires VITE_API_BASE_URL as a public HTTPS URL (e.g. https://api.blackcreator.ir).'
      );
    }
  }

  return {
    plugins: [react()],
    base: './',
    build: {
      outDir: 'dist',
      sourcemap: false,
      assetsInlineLimit: 4096,
    },
    server: {
      host: true,
      port: 5173,
    },
  };
});
