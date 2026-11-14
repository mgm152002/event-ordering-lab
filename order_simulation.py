"""Order-processing simulation: versioned event ordering.

An order moves through versioned states: v1 CREATED, v2 PAID, v3 SHIPPED.
Events may arrive late, out of order, or more than once. Each event
carries order_id, version, and status. Ordering is decided by version,
never by arrival time or wall-clock timestamps.

Rules:
- An event is applied only when its version is the next expected one.
- A future version is buffered until the missing versions arrive.
- A version at or below the applied version is a duplicate: ignored.
- Each worker carries an increasing fencing token. An update from a
  worker whose token is below the highest token seen for that order
  is a stale worker and is rejected before any version check.

Usage:
    python3 order_simulation.py ordering
    python3 order_simulation.py missing
    python3 order_simulation.py stale
    python3 order_simulation.py all

Standard library only. No dependencies, no helper files.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass


@dataclass(frozen=True)
class OrderEvent:
    """One order update. Version defines the order, not arrival time."""

    order_id: str
    version: int
    status: str


class OrderState:
    """Versioned state for one order, with a buffer for early events."""

    def __init__(self, order_id: str) -> None:
        self.order_id = order_id
        self.version = 0
        self.status = "NONE"
        self.applied: list[int] = []
        self.pending: dict[int, OrderEvent] = {}
        self.duplicates_ignored = 0

    @property
    def expected_version(self) -> int:
        return self.version + 1

    def pending_versions(self) -> list[int]:
        return sorted(self.pending)

    def missing_versions(self) -> list[int]:
        """Versions below the highest buffered one that never arrived."""
        if not self.pending:
            return []
        top = max(self.pending)
        return [v for v in range(self.expected_version, top + 1)
                if v not in self.pending]

    def _apply(self, event: OrderEvent) -> None:
        self.version = event.version
        self.status = event.status
        self.applied.append(event.version)

    def receive(self, event: OrderEvent) -> str:
        """Handle one delivery. Prints only the outcome lines."""
        if event.version < 1:
            print("    REJECTED: version must be 1 or higher.")
            return "invalid"
        if event.version <= self.version:
            self.duplicates_ignored += 1
            print(f"    IGNORED duplicate: version {event.version} "
                  f"already applied (current version={self.version}).")
            return "duplicate"
        if event.version in self.pending:
            self.duplicates_ignored += 1
            print(f"    IGNORED duplicate: version {event.version} "
                  f"already buffered, waiting for version "
                  f"{self.expected_version}.")
            return "duplicate"
        if event.version > self.expected_version:
            self.pending[event.version] = event
            print(f"    BUFFERED: waiting for version "
                  f"{self.expected_version}. "
                  f"Missing: {self.missing_versions()}. "
                  f"Pending: {self.pending_versions()}.")
            print(f"    State unchanged: version={self.version} "
                  f"status={self.status}")
            return "buffered"
        self._apply(event)
        print(f"    APPLIED version={event.version} "
              f"status={event.status}")
        while self.expected_version in self.pending:
            buffered = self.pending.pop(self.expected_version)
            self._apply(buffered)
            print(f"    APPLIED buffered version={buffered.version} "
                  f"status={buffered.status}")
        return "applied"


@dataclass(frozen=True)
class Worker:
    """A worker identity. Tokens only increase; old holders go stale."""

    name: str
    token: int


class OrderStore:
    """Holds order states and the fencing token seen per order."""

    def __init__(self) -> None:
        self.orders: dict[str, OrderState] = {}
        self.fencing: dict[str, int] = {}
        self.rejected_stale = 0

    def state_for(self, order_id: str) -> OrderState:
        if order_id not in self.orders:
            self.orders[order_id] = OrderState(order_id)
        return self.orders[order_id]

    def fencing_token(self, order_id: str) -> int:
        return self.fencing.get(order_id, 0)

    def update(self, worker: Worker, event: OrderEvent) -> str:
        """Fencing check first, then versioned apply. Outcome lines only."""
        current = self.fencing_token(event.order_id)
        if worker.token < current:
            self.rejected_stale += 1
            print(f"    REJECTED stale worker: token {worker.token} "
                  f"< current token {current}. Update dropped.")
            return "stale"
        if worker.token > current:
            self.fencing[event.order_id] = worker.token
            print(f"    Fencing token advanced: {current} -> "
                  f"{worker.token} ({worker.name}).")
        return self.state_for(event.order_id).receive(event)


def demo_ordering() -> None:
    """Delivery order v2, v1, v2 again. Apply order must be v1, v2."""
    print("=== Test 1: versioned event ordering ===")
    print("Order ORD-1001. Delivery order: v2, v1, v2 again.")
    print("Expected apply order: v1 CREATED, then v2 PAID.")
    state = OrderState("ORD-1001")
    deliveries = [
        OrderEvent("ORD-1001", 2, "PAID"),
        OrderEvent("ORD-1001", 1, "CREATED"),
        OrderEvent("ORD-1001", 2, "PAID"),
    ]
    for step, event in enumerate(deliveries, 1):
        print(f"[{step}] RECEIVED order_id={event.order_id} "
              f"version={event.version} status={event.status}")
        state.receive(event)
    print(f"FINAL order_id={state.order_id} version={state.version} "
          f"status={state.status} applied={state.applied} "
          f"pending={state.pending_versions()}")
    applied = " -> ".join(str(v) for v in state.applied)
    print(f"Applied in order: {applied}. "
          f"Duplicates ignored: {state.duplicates_ignored}.")


def demo_missing() -> None:
    """Only later versions arrive. Nothing may jump ahead."""
    print("=== Test 2: missing event ===")
    print("Order ORD-2001. Only v2 and v3 arrive; v1 never arrives.")
    state = OrderState("ORD-2001")
    deliveries = [
        OrderEvent("ORD-2001", 2, "PAID"),
        OrderEvent("ORD-2001", 3, "SHIPPED"),
    ]
    for step, event in enumerate(deliveries, 1):
        print(f"[{step}] RECEIVED order_id={event.order_id} "
              f"version={event.version} status={event.status}")
        state.receive(event)
    print(f"FINAL order_id={state.order_id} version={state.version} "
          f"status={state.status} applied={state.applied} "
          f"pending={state.pending_versions()}")
    print(f"Missing versions: {state.missing_versions()}. "
          f"Pending: {state.pending_versions()}.")
    print("Order did NOT jump ahead: version stayed 0, "
          "status stayed NONE.")


def demo_stale() -> None:
    """Two workers, one order. The older fencing token loses."""
    print("=== Test 3: stale worker (fencing tokens) ===")
    print("Order ORD-3001. worker-A token=1, worker-B token=2.")
    print("Higher token wins; the older token is rejected.")
    store = OrderStore()
    worker_a = Worker("worker-A", 1)
    worker_b = Worker("worker-B", 2)
    steps = [
        (worker_a, OrderEvent("ORD-3001", 1, "CREATED")),
        (worker_b, OrderEvent("ORD-3001", 2, "PAID")),
        (worker_a, OrderEvent("ORD-3001", 3, "SHIPPED")),
        (worker_b, OrderEvent("ORD-3001", 3, "SHIPPED")),
    ]
    for step, (worker, event) in enumerate(steps, 1):
        print(f"[{step}] {worker.name} (token={worker.token}) sends "
              f"order_id={event.order_id} version={event.version} "
              f"status={event.status}")
        store.update(worker, event)
    state = store.state_for("ORD-3001")
    print(f"FINAL order_id={state.order_id} version={state.version} "
          f"status={state.status} applied={state.applied} "
          f"pending={state.pending_versions()}")
    print(f"Fencing token={store.fencing_token('ORD-3001')}. "
          f"Stale updates rejected: {store.rejected_stale}.")
    print("Stale worker-A update rejected; current worker-B "
          "update applied.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Order event-ordering simulation.")
    parser.add_argument("demo", nargs="?", default="all",
                        choices=["ordering", "missing", "stale", "all"],
                        help="which demo to run (default: all)")
    args = parser.parse_args()
    if args.demo == "ordering":
        demo_ordering()
    elif args.demo == "missing":
        demo_missing()
    elif args.demo == "stale":
        demo_stale()
    else:
        demo_ordering()
        print()
        demo_missing()
        print()
        demo_stale()


if __name__ == "__main__":
    main()
