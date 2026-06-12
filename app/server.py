"""HTTP adapter.

Exposes the agent (built by app.bootstrap) as the AgentBase entrypoint plus a
health check. The layer wiring lives in app/bootstrap.py, shared with the CLI.
"""
from __future__ import annotations

from datetime import datetime

from greennode_agentbase import GreenNodeAgentBaseApp, PingStatus, RequestContext

from app.bootstrap import bootstrap


def create_app() -> GreenNodeAgentBaseApp:
    _config, router = bootstrap()

    app = GreenNodeAgentBaseApp()

    @app.entrypoint
    def handler(payload: dict, context: RequestContext) -> dict:
        """POST /invocations — {"message": "...", "history": [{role, content}, ...]}.

        history is optional (stateless build). Role is derived from
        context.user_id; see AgentRouter.
        """
        message = payload.get("message", "")
        if not message:
            return {"status": "error", "error": "Missing 'message' in payload."}

        result = router.chat(
            user_id=context.user_id,
            session_id=context.session_id,
            message=message,
        )
        return {
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            **result,
        }

    @app.ping
    def health_check() -> PingStatus:
        return PingStatus.HEALTHY

    return app


app = create_app()
