"""Hilfen für `jarvis login`: `claude setup-token` interaktiv ausführen und den
angezeigten Token direkt aus der Ausgabe lesen – kein Abtippen/Kopieren nötig.

Auf dem Handy (Termux) bricht das schmale Terminal den ~100 Zeichen langen Token
auf mehrere Zeilen um; beim Kopieren gehen dann leicht Zeichen verloren. Deshalb
läuft setup-token hier in einem Pseudo-Terminal mit sehr breiter Zeile, und wir
lesen die Ausgabe mit.
"""

from __future__ import annotations

import os
import re
import select
import struct
import sys

TOKEN_RE = re.compile(r"sk-ant-oat\d{2}-[A-Za-z0-9_\-]{20,}")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(\x07|\x1b\\)|\x1b[()][A-Za-z0-9]")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def extract_tokens(output: str) -> list[str]:
    """Alle Token-Kandidaten aus der (ANSI-bereinigten) Ausgabe, neueste zuerst.
    Zusätzlich Varianten, bei denen ein Zeilenumbruch mitten im Token entfernt wurde."""
    text = strip_ansi(output).replace("\r", "")
    found: list[str] = []
    for m in TOKEN_RE.finditer(text):
        found.append(m.group(0))
        # Falls doch umgebrochen: nächste Zeile(n) anhängen, solange sie nur Token-Zeichen enthalten
        rest = text[m.end():]
        joined = m.group(0)
        for line in rest.split("\n")[1:3]:
            piece = line.strip()
            if piece and re.fullmatch(r"[A-Za-z0-9_\-]+", piece):
                joined += piece
                found.append(joined)
            else:
                break
    # neueste zuerst, ohne Duplikate
    out: list[str] = []
    for t in reversed(found):
        if t not in out:
            out.append(t)
    return out


def run_captured(argv: list[str], env: dict[str, str], columns: int = 400) -> str:
    """Startet argv interaktiv in einem Pseudo-Terminal (breite Zeile) und gibt die
    gesamte Ausgabe zurück. Ein-/Ausgabe werden wie gewohnt an den Nutzer durchgereicht."""
    import fcntl
    import pty
    import termios
    import tty

    pid, fd = pty.fork()
    if pid == 0:  # Kind: breites Terminal, dann claude starten
        try:
            rows = os.get_terminal_size(sys.__stdout__.fileno()).lines if sys.__stdout__.isatty() else 40
        except OSError:
            rows = 40
        try:
            fcntl.ioctl(sys.stdout.fileno(), termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        except OSError:
            pass
        os.execvpe(argv[0], argv, env)

    buf = bytearray()
    stdin_fd = sys.stdin.fileno()
    old = None
    if os.isatty(stdin_fd):
        old = termios.tcgetattr(stdin_fd)
        tty.setraw(stdin_fd)
    try:
        inputs = [fd, stdin_fd]
        while True:
            try:
                ready, _, _ = select.select(inputs, [], [])
            except InterruptedError:
                continue
            if fd in ready:
                try:
                    data = os.read(fd, 4096)
                except OSError:  # Kind beendet
                    break
                if not data:
                    break
                buf += data
                os.write(sys.stdout.fileno(), data)
            if stdin_fd in ready:
                data = os.read(stdin_fd, 1024)
                if not data:
                    inputs.remove(stdin_fd)  # Eingabe zu Ende, Ausgabe weiter lesen
                else:
                    os.write(fd, data)
    finally:
        if old is not None:
            termios.tcsetattr(stdin_fd, termios.TCSAFLUSH, old)
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    return buf.decode("utf-8", errors="ignore")
