"""Entry shim for the chatbot CLI tester.

Run `python chatbot_cli.py [--user <id>] [--session <id>]`.
The class and logic live in app/cli.py (ChatbotCLI).
"""
from app.cli import main

if __name__ == "__main__":
    main()
