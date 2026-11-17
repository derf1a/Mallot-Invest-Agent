import { execSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8')) as { version: string }
let gitSha = 'dev'
try {
  gitSha = execSync('git rev-parse --short HEAD', { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim()
} catch {
  /* git 이 없으면 dev */
}

// 경로 접두사 — MIA_BASE_PATH=/invest 면 <도메인>/invest/ 아래에서 연다 (백엔드 app/core/base_path 가 같은 값으로 뗀다). 없으면 루트
const rawBase = (process.env.MIA_BASE_PATH ?? '').trim().replace(/^\/+|\/+$/g, '')
const base = rawBase ? `/${rawBase}/` : '/'

// https://vite.dev/config/
export default defineConfig({
  base,
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
    __GIT_SHA__: JSON.stringify(gitSha),
  },
  server: {
    host: true,
    proxy: {
      [`${base}api`]: `http://localhost:${process.env.BACKEND_PORT ?? 8100}`,
    },
  },
})
