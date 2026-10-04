#!/usr/bin/env python3
"""Run a command in a pseudo-terminal and relay it over stdin/stdout, for the web app's terminal.

Usage: pty_relay.py COLS ROWS -- COMMAND...

stdin goes to the terminal as typed keys, the terminal's output comes out on stdout, and a line
"COLS ROWS" written to fd 3 resizes it (the command gets SIGWINCH, as in a real terminal). When the
command exits, so does the relay; when stdin closes, the command is stopped. Standard library only, so
no native module has to be built.
"""

import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios


def main():
    cols, rows = int(sys.argv[1]), int(sys.argv[2])
    command = sys.argv[sys.argv.index("--") + 1 :]
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(command[0], command)

    def resize(c, r):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", r, c, 0, 0))

    resize(cols, rows)
    watched, control = [fd, 0, 3], b""
    while fd in watched:
        ready, _, _ = select.select(watched, [], [])
        if fd in ready:
            try:
                data = os.read(fd, 65536)
            except OSError:  # the command exited
                data = b""
            if not data:
                break
            os.write(1, data)
        if 0 in ready:
            data = os.read(0, 65536)
            if not data:  # the router went away: stop the command
                break
            os.write(fd, data)
        if 3 in ready:
            data = os.read(3, 1024)
            if not data:
                watched.remove(3)
                continue
            control += data
            while b"\n" in control:
                line, control = control.split(b"\n", 1)
                parts = line.split()
                if len(parts) == 2:
                    resize(int(parts[0]), int(parts[1]))  # the kernel sends SIGWINCH
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)


if __name__ == "__main__":
    main()
