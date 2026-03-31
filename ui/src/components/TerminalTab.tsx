import { useEffect, useRef } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

interface Props {
  sessionId: string
  onRegister: (writeFn: (data: Uint8Array) => void) => void
  onUnregister: () => void
  onInput: (data: string) => void  // base64-encoded keystroke bytes
  onResize: (cols: number, rows: number) => void
}

export function TerminalTab({ sessionId, onRegister, onUnregister, onInput, onResize }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const term = new Terminal({
      theme: { background: '#030712', foreground: '#e2e8f0' },
      cursorBlink: true,
      fontSize: 13,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
    })
    const fitAddon = new FitAddon()
    term.loadAddon(fitAddon)
    term.open(containerRef.current)
    fitAddon.fit()

    // Register write function with App so WS output can reach this terminal
    onRegister((data: Uint8Array) => term.write(data))

    // Send keystrokes to server as base64
    const dataDispose = term.onData((str: string) => {
      const bytes = new TextEncoder().encode(str)
      let binary = ''
      bytes.forEach(b => { binary += String.fromCharCode(b) })
      onInput(btoa(binary))
    })

    // Resize terminal when container changes size
    const ro = new ResizeObserver(() => {
      fitAddon.fit()
      onResize(term.cols, term.rows)
    })
    ro.observe(containerRef.current)

    // Send initial size
    onResize(term.cols, term.rows)

    return () => {
      onUnregister()
      dataDispose.dispose()
      ro.disconnect()
      term.dispose()
    }
  }, [sessionId])  // remount when session changes

  return (
    <div
      ref={containerRef}
      className="flex-1 min-h-0"
      style={{ padding: '8px', backgroundColor: '#030712' }}
    />
  )
}
