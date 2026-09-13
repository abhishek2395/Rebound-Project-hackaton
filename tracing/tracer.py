"""
Rebound Tracer
Logs structured JSONL execution traces capturing step, tool, input, output,
latency, and cost per graph superstep. Feeds the visualizer frontend.
"""

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional

from agent.models import TraceEntry, TraceStatus
from clients.base import measure_latency


class Tracer:
    """Manages appending and retrieving structured execution traces."""
    def __init__(self, run_id: str, traces_dir: str = "traces"):
        self.run_id = run_id
        self.traces_dir = traces_dir
        self.entries: List[TraceEntry] = []
        os.makedirs(self.traces_dir, exist_ok=True)
        self.file_path = os.path.join(self.traces_dir, f"{self.run_id}.jsonl")

    def log_entry(
        self,
        step: str,
        tool: str,
        input_data: Optional[Dict[str, Any]] = None,
        output_summary: str = "",
        latency_ms: int = 0,
        status: TraceStatus = TraceStatus.OK,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
    ) -> TraceEntry:
        """Appends a trace entry in-memory and writes it to the JSONL log."""
        entry = TraceEntry(
            run_id=self.run_id,
            step=step,
            tool=tool,
            input=input_data or {},
            output_summary=output_summary,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            status=status,
            ts=datetime.now(timezone.utc),
        )
        self.entries.append(entry)

        try:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(entry.model_dump_json() + "\n")
        except Exception:
            pass

        return entry

    @contextmanager
    def span(
        self,
        step: str,
        tool: str,
        input_data: Optional[Dict[str, Any]] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Context manager to measure latency and record status automatically:
          with tracer.span("search", "duffel.offer_requests.create", {"origin": "LHR"}) as s:
              res = do_search()
              s["summary"] = f"{len(res)} offers found"
        """
        span_ctx: Dict[str, Any] = {
            "summary": "",
            "status": TraceStatus.OK,
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_usd": 0.0,
        }
        with measure_latency() as lat:
            try:
                yield span_ctx
            except Exception as e:
                span_ctx["status"] = TraceStatus.ERROR
                span_ctx["summary"] = f"Error: {str(e)}"
                raise
            finally:
                self.log_entry(
                    step=step,
                    tool=tool,
                    input_data=input_data or {},
                    output_summary=span_ctx["summary"],
                    latency_ms=lat["ms"],
                    status=span_ctx["status"],
                    tokens_in=span_ctx["tokens_in"],
                    tokens_out=span_ctx["tokens_out"],
                    cost_usd=span_ctx["cost_usd"],
                )

    def to_dict_list(self) -> List[Dict[str, Any]]:
        """Returns all in-memory entries as plain dicts."""
        return [e.model_dump() for e in self.entries]

