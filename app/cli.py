"""Command-line tester for the chatbot — drives the agent directly, no HTTP.

Reuses the same composition as the server (app.bootstrap), then runs a REPL that
calls router.chat() per line. Use it to exercise services and roles locally.

Run:
    python -m app.cli                 # default user (→ default role)
    python -m app.cli --user owner    # test as an owner identity
    python -m app.cli --user alice --session demo-1

In-REPL commands:
    /help            show commands
    /whoami          show current user id, resolved role, and session
    /user <id>       switch the caller's user id (changes role; starts a new session)
    /new [id]        start a fresh conversation (new session id)
    /quit            exit
"""
from __future__ import annotations

import argparse
import uuid

from app.bootstrap import bootstrap


class ChatbotCLI:
    def __init__(self, user_id: str = "cli-user", session_id: str | None = None):
        self._config, self._router = bootstrap()
        self.user_id = user_id
        self.session_id = session_id or self._new_session_id()

    @staticmethod
    def _new_session_id() -> str:
        return f"cli-{uuid.uuid4().hex[:8]}"

    @property
    def role(self) -> str:
        return self._config.role_for(self.user_id)

    # ── REPL ───────────────────────────────────────────────────────────
    def run(self) -> None:
        self._banner()
        while True:
            try:
                line = input("\nyou › ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nbye.")
                return
            if not line:
                continue
            if line.startswith("/"):
                if not self._command(line):
                    return
                continue
            self._send(line)

    def _send(self, message: str) -> None:
        import sys
        try:
            result = self._router.chat(self.user_id, self.session_id, message)
        except Exception as e:  # surface errors without killing the REPL
            print(f"[error] {type(e).__name__}: {e}")
            return
        text = f"\nbot ({result['role']}) › {result['response']}"
        # Encode with replace so surrogate chars from non-UTF8 terminals don't crash.
        sys.stdout.buffer.write(text.encode(sys.stdout.encoding or "utf-8", errors="replace") + b"\n")
        sys.stdout.buffer.flush()

    # ── commands ───────────────────────────────────────────────────────
    def _command(self, line: str) -> bool:
        """Handle a /command. Return False to exit the REPL."""
        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/quit", "/exit", "/q"):
            print("bye.")
            return False
        if cmd == "/help":
            self._help()
        elif cmd == "/whoami":
            print(f"user_id={self.user_id!r}  role={self.role!r}  session={self.session_id!r}")
        elif cmd == "/user":
            if not arg:
                print("usage: /user <id>")
            else:
                self.user_id = arg
                self.session_id = self._new_session_id()  # avoid mixing memory across users
                print(f"switched to user_id={arg!r} (role={self.role!r}), new session={self.session_id!r}")
        elif cmd == "/new":
            self.session_id = arg or self._new_session_id()
            print(f"new session={self.session_id!r}")
        else:
            print(f"unknown command {cmd!r}. type /help")
        return True

    # ── display ──────────────────────────────────────────────────────────
    def _banner(self) -> None:
        print("=" * 60)
        print("Chatbot CLI — talking to the agent directly (no HTTP)")
        print(f"routing : {self._config.routing}")
        print(f"roles   : {', '.join(self._config.roles())}  (default: {self._config.default_role})")
        print(f"user    : {self.user_id!r}  →  role {self.role!r}")
        print(f"session : {self.session_id!r}")
        print("type /help for commands, /quit to exit")
        print("=" * 60)

    @staticmethod
    def _help() -> None:
        print(
            "commands:\n"
            "  /help          show this\n"
            "  /whoami        show user id, role, session\n"
            "  /user <id>     switch user id (changes role; new session)\n"
            "  /new [id]      start a fresh conversation\n"
            "  /quit          exit"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Chatbot CLI tester (no HTTP).")
    parser.add_argument("--user", default="cli-user", help="caller user id (determines role)")
    parser.add_argument("--session", default=None, help="session id (default: random)")
    args = parser.parse_args()
    ChatbotCLI(user_id=args.user, session_id=args.session).run()


if __name__ == "__main__":
    main()
