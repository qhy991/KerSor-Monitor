import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/tasks': 'http://localhost:8000', '/projects': 'http://localhost:8000' } },
})
