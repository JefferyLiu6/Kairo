import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { apiWebSocketUrl } from "../api";

type Props = {
  path: string;
  onExit: () => void;
};

export function InteractiveTerminal({ path, onExit }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const term = new Terminal({
      cursorBlink: true,
      fontFamily: "'SF Mono', 'Fira Code', 'Consolas', monospace",
      fontSize: 13,
      lineHeight: 1.15,
      theme: {
        background: "#0f172a",
        foreground: "#e2e8f0",
        cursor: "#6366f1",
        cursorAccent: "#0f172a",
        selectionBackground: "rgba(99,102,241,0.3)",
        black:   "#1e293b", red:     "#f87171",
        green:   "#4ade80", yellow:  "#fbbf24",
        blue:    "#60a5fa", magenta: "#c084fc",
        cyan:    "#22d3ee", white:   "#e2e8f0",
        brightBlack:   "#475569", brightRed:     "#fca5a5",
        brightGreen:   "#86efac", brightYellow:  "#fde68a",
        brightBlue:    "#93c5fd", brightMagenta: "#d8b4fe",
        brightCyan:    "#67e8f9", brightWhite:   "#f8fafc",
      },
    });

    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.open(containerRef.current);

    // Slight delay so the DOM is fully painted before measuring
    setTimeout(() => fitAddon.fit(), 10);

    const ws = new WebSocket(apiWebSocketUrl("/ws/terminal", { path }));

    ws.onopen = () => {
      term.write("\x1b[2m[Connected — type to interact, Ctrl+C to interrupt]\x1b[0m\r\n\r\n");
    };

    ws.onmessage = (e) => term.write(e.data as string);

    ws.onclose = () => {
      term.write("\x1b[2m\r\n[Session ended — click Close terminal to dismiss]\x1b[0m\r\n");
    };

    ws.onerror = () => {
      term.write("\x1b[31m[WebSocket error — is the backend running?]\x1b[0m\r\n");
    };

    // Keyboard → WebSocket + local echo (no PTY means Docker won't echo for us)
    term.onData((data) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      ws.send(data);
      // Echo each character back so the user can see what they type
      for (const ch of data) {
        if (ch === "\x7f") {          // Backspace
          term.write("\b \b");
        } else if (ch === "\r") {     // Enter
          term.write("\r\n");
        } else if (ch < "\x20") {    // Other control chars (Ctrl+C etc.)
          // don't echo
        } else {
          term.write(ch);
        }
      }
    });

    // Resize
    const ro = new ResizeObserver(() => {
      try { fitAddon.fit(); } catch { /* ignore during unmount */ }
    });
    ro.observe(containerRef.current);

    return () => {
      ws.close();
      term.dispose();
      ro.disconnect();
    };
  }, [path, onExit]);

  return <div ref={containerRef} className="ws-xterm-container" />;
}
