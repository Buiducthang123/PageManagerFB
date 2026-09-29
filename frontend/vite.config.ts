import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5175,
    // Cho phép truy cập qua ngrok (`ngrok http 5175`) — Facebook gọi lại
    // /api/facebook/callback qua tên miền này khi đăng nhập Facebook.
    allowedHosts: ['.ngrok-free.app', '.ngrok-free.dev', '.ngrok.app', '.ngrok.io'],
    proxy: {
      '/api': 'http://127.0.0.1:8001',
    },
  },
})
