import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const clinicalTrialsProxy = {
  target: 'https://clinicaltrials.gov',
  changeOrigin: true,
  rewrite: (path: string) => path.replace(/^\/api\/clinicaltrials/, '/api/v2/studies'),
}

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api/clinicaltrials': clinicalTrialsProxy },
  },
  preview: {
    proxy: { '/api/clinicaltrials': clinicalTrialsProxy },
  },
})
