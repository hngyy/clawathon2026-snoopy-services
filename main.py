"""Entrypoint for the VNG Campus Tour Bot.

Thin shim: the AgentBase app (handler, health check, and all wiring) lives in
`app.server`. Run with `python main.py`; the Dockerfile uses the same command.
"""
from app.server import app

if __name__ == "__main__":
    app.run(port=8080, host="0.0.0.0")
