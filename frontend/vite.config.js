import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  base: './', // required so the packaged Electron build can load via file://
  plugins: [react()],
})
