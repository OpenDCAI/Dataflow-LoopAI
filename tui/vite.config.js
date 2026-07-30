import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

const vueTuiSrc = fileURLToPath(new URL('../../vue-tui/src/index.ts', import.meta.url))
const vueTuiVueSrc = fileURLToPath(new URL('../../vue-tui/src/vue.ts', import.meta.url))
const vueTuiCliSrc = fileURLToPath(new URL('../../vue-tui/src/cli.ts', import.meta.url))
const vueTuiMarkdownSrc = fileURLToPath(new URL('../../vue-tui/src/markdown.ts', import.meta.url))
const vueTuiExperimentalSrc = fileURLToPath(new URL('../../vue-tui/src/experimental.ts', import.meta.url))

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
    plugins: [vue()],
    resolve: {
      alias: [
        { find: /^@simon_he\/vue-tui$/, replacement: vueTuiSrc },
        { find: /^@simon_he\/vue-tui\/vue$/, replacement: vueTuiVueSrc },
        { find: /^@simon_he\/vue-tui\/cli$/, replacement: vueTuiCliSrc },
        { find: /^@simon_he\/vue-tui\/markdown$/, replacement: vueTuiMarkdownSrc },
        { find: /^@simon_he\/vue-tui\/experimental$/, replacement: vueTuiExperimentalSrc }
      ]
    }
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
