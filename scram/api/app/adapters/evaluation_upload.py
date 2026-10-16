"""Attach native AX evaluations to existing spans, outside the control path."""
from __future__ import annotations

from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic, sleep


def evaluation_row(span_id: str, results: list[dict]) -> dict:
    row = {"context.span_id": span_id}
    for result in results:
        prefix = "eval." + result["name"].lower().replace(" ", "_")
        row[f"{prefix}.label"] = result["label"]
        row[f"{prefix}.explanation"] = result["explanation"]
        if result["score"] is not None:
            row[f"{prefix}.score"] = result["score"]
    return row


class EvaluationUploader:
    def __init__(self, provider, client, *, space_id: str, project_name: str, capacity: int = 128):
        self.provider = provider
        self.client = client
        self.space_id = space_id
        self.project_name = project_name
        self.queue = Queue(maxsize=capacity)
        self.statuses: dict[str, str] = {}
        self.stopping = Event()
        self.worker = Thread(target=self._work, name="scram-ax-evaluations", daemon=True)
        self.worker.start()

    def submit(self, span_id: str, results: list[dict]) -> None:
        if self.stopping.is_set():
            self.statuses[span_id] = "error:closed"
            return
        row = evaluation_row(span_id, results)
        self.statuses[span_id] = "queued"
        try:
            self.queue.put_nowait((span_id, row))
        except Full:
            self.statuses[span_id] = "error:queue_full"

    def _work(self) -> None:
        while not self.stopping.is_set() or not self.queue.empty():
            try:
                span_id, row = self.queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                import pandas as pd

                if not self.provider.force_flush(timeout_millis=2000):
                    raise TimeoutError("Span flush timed out")
                self.client.spans.update_evaluations(
                    space_id=self.space_id, project_name=self.project_name,
                    dataframe=pd.DataFrame([row]), force_http=True, timeout=5,
                )
                # Submission is not confirmation that AX has indexed the trace.
                self.statuses[span_id] = "evaluations_submitted"
            except Exception as error:
                # SDK exceptions may contain credentials. Retain only the type.
                self.statuses[span_id] = f"error:{type(error).__name__}"
            finally:
                self.queue.task_done()

    def status(self, span_id: str) -> str:
        return self.statuses.get(span_id, "recording")

    def flush(self, timeout: float = 2) -> bool:
        deadline = monotonic() + timeout
        while self.queue.unfinished_tasks and monotonic() < deadline:
            sleep(0.01)
        return self.queue.unfinished_tasks == 0

    def close(self, timeout: float = 2) -> bool:
        self.stopping.set()
        self.worker.join(timeout=timeout)
        return not self.worker.is_alive()
