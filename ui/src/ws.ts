export type MessageHandler = (msg: unknown) => void

export class WsClient {
  private ws: WebSocket | null = null
  private handler: MessageHandler
  private reconnectDelay = 1000
  private destroyed = false

  constructor(handler: MessageHandler) {
    this.handler = handler
    this.connect()
  }

  private connect(): void {
    const url = `ws://${window.location.host}/ws`
    this.ws = new WebSocket(url)

    this.ws.onmessage = (event) => {
      try {
        this.handler(JSON.parse(event.data as string))
      } catch {
        // ignore malformed messages
      }
    }

    this.ws.onclose = () => {
      if (!this.destroyed) {
        setTimeout(() => this.connect(), this.reconnectDelay)
        this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30_000)
      }
    }

    this.ws.onopen = () => {
      this.reconnectDelay = 1000
    }
  }

  send(msg: object): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg))
    }
  }

  destroy(): void {
    this.destroyed = true
    this.ws?.close()
  }
}
