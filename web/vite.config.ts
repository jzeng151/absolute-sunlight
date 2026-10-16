import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In development, /api requests go to the FastAPI back end on port 8000.
// With VITE_API_MODE=fixture (the default), the app never calls /api at all.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': 'http://localhost:8000' }
  }
});
