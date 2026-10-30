from __future__ import annotations

from typing import Any

from services.store import Store, identifier, now


class TraceService:
    def __init__(self, store: Store):
        self.store = store

    def start(self, action: str, inputs: dict[str, Any]) -> dict[str, Any]:
        trace = {
            "id": identifier("trace"),
            "trace_id": None,
            "action": action,
            "status": "running",
            "inputs": inputs,
            "steps": [],
            "started_at": now(),
            "finished_at": None,
        }
        trace["trace_id"] = trace["id"]
        return self.store.put("trace", trace)

    def step(self, trace_id: str, name: str, output: Any) -> dict[str, Any]:
        trace = self.store.get("trace", trace_id)
        trace["steps"].append({"name": name, "output": output, "at": now()})
        return self.store.put("trace", trace)

    def finish(self, trace_id: str, output: Any = None, error: str | None = None) -> dict[str, Any]:
        trace = self.store.get("trace", trace_id)
        trace["status"] = "failed" if error else "succeeded"
        trace["output"] = output
        trace["error"] = error
        trace["finished_at"] = now()
        return self.store.put("trace", trace)

    def get(self, trace_id: str) -> dict[str, Any]:
        return self.store.get("trace", trace_id)

    def list(self) -> list[dict[str, Any]]:
        return self.store.all("trace")
