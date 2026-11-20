import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    host: '127.0.0.1',
    fs: { deny: ['.env', '.env.*', '*.{crt,pem}', '**/.git/**', '**/.echo/**', '**/.cache/**',
      '**/*.sqlite3*', '**/phone-password.txt'] },
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
});
