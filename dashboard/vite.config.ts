import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiTarget = 'http://localhost:8000'
const proxy = () => ({ target: apiTarget, changeOrigin: true })

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/projects': proxy(),
      '/tasks': proxy(),
      '/hosts': proxy(),
      '/templates': proxy(),
      '/summary': proxy(),
      '/internal': proxy(),
    },
  },
})
