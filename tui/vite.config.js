import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

const terminalExternal = [
  'fs',
  'node:fs',
  'node:fs/promises',
  'child_process',
  'node:child_process',
  'events',
  'node:events',
  'os',
  'node:os',
  'path',
  'node:path',
  'buffer',
  'node:buffer',
  'process',
  'node:process',
  'url',
  'node:url',
  'util',
  'node:util'
]

export default defineConfig(({ mode }) => {
  const base = {
    plugins: [vue()]
  }

  if (mode === 'terminal') {
    return {
      ...base,
      build: {
        outDir: 'dist-terminal',
        emptyOutDir: true,
        lib: {
          entry: fileURLToPath(new URL('./src/terminal.js', import.meta.url)),
          formats: ['es'],
          fileName: () => 'terminal.js'
        },
        rollupOptions: {
          external: terminalExternal
        },
        target: 'node18',
        minify: false
      }
    }
  }

  return base
})
