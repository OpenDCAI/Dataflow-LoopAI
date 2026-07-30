import { createPinia } from 'pinia'
import {
  createStdinDriver,
  createStdoutRenderer,
  createTerminalApp,
  installTerminalCleanup
} from '@simon_he/vue-tui/cli'
import App from './App.vue'

function terminalSize() {
  const cols = Number.isFinite(process.stdout.columns) ? Math.max(96, process.stdout.columns) : 120
  const rows = Number.isFinite(process.stdout.rows) ? Math.max(30, process.stdout.rows) : 36
  return { cols, rows }
}

const smoke = process.env.VT_SMOKE === '1'
const { cols, rows } = terminalSize()
let exiting = false

const app = createTerminalApp({
  cols,
  rows,
  component: App,
  props: {
    onQuit: () => exit(0)
  },
  defaultStyle: {
    fg: 'whiteBright',
    bg: '#08111f'
  },
  selection: {
    style: { fg: 'black', bg: 'cyanBright', inverse: false }
  }
})
app.app.use(createPinia())
app.mount()

const out = createStdoutRenderer(
  app.terminal,
  smoke
    ? {
        output: { write: () => {}, isTTY: false },
        clear: false,
        hideCursor: false,
        altScreen: false,
        trackResize: false,
        defaultBg: '#08111f'
      }
    : {
        output: process.stdout,
        hideCursor: true,
        altScreen: true,
        colorMode: 'auto',
        allowFileUrls: true,
        defaultBg: '#08111f',
        getImeAnchor: () => app.getImeAnchor(),
        trackResize: true
      }
)

const offCommitCursor = app.terminal.on('commit', () => {
  if (smoke) return
  const anchor = app.getImeAnchor()
  if (!anchor) return
  out.setCursor(anchor.cellX, anchor.cellY)
  out.showCursor(false)
})

let driver = null
let cleanupHandle = null

function cleanup() {
  if (exiting) return
  exiting = true
  cleanupHandle?.uninstall()
  cleanupHandle = null
  driver?.dispose()
  offCommitCursor()
  out.dispose()
  app.dispose()
}

function exit(code = 0) {
  cleanup()
  process.exit(code)
}

app.scheduler.flushNow()

if (smoke) {
  exit(0)
} else {
  cleanupHandle = installTerminalCleanup(cleanup, { signalPolicy: 'exit' })
  driver = createStdinDriver({
    dispatch: (event) => {
      const prevented = app.events.dispatch(event)
      app.scheduler.flush()
      return prevented
    },
    enableMouse: true,
    enableMouseMotion: true,
    onExit: () => exit(0)
  })
}
