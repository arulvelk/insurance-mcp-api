"""In-memory data store seeded from ``data/seed.json``.

This keeps the demo self-contained. To go to production, replace this class with one
backed by Azure Cosmos DB, Azure SQL, or your policy admin system. The service layer only
depends on the methods below.
"""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any

SEED_PATH = Path(__file__).parent / "data" / "seed.json"


class InMemoryRepository:
    def __init__(self, seed_path: Path = SEED_PATH) -> None:
        with open(seed_path, encoding="utf-8") as f:
            seed = json.load(f)
        self._lock = threading.Lock()
        self.company: dict[str, Any] = seed["company"]
        self._customers = {c["customer_id"]: c for c in seed["customers"]}
        self._policies = {p["policy_number"]: p for p in seed["policies"]}
        self._claims = {c["claim_number"]: c for c in seed["claims"]}
        self._callbacks: dict[str, dict[str, Any]] = {}
        self._claim_seq = 900
        self._callback_seq = 1000

    # ---- customers -------------------------------------------------------
    def list_customers(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(c) for c in self._customers.values()]

    def get_customer(self, customer_id: str) -> dict[str, Any] | None:
        c = self._customers.get(customer_id)
        return copy.deepcopy(c) if c else None

    # ---- policies --------------------------------------------------------
    def get_policy(self, policy_number: str) -> dict[str, Any] | None:
        p = self._policies.get(policy_number)
        return copy.deepcopy(p) if p else None

    def list_policies_for_customer(self, customer_id: str) -> list[dict[str, Any]]:
        return [copy.deepcopy(p) for p in self._policies.values() if p["customer_id"] == customer_id]

    # ---- claims ----------------------------------------------------------
    def get_claim(self, claim_number: str) -> dict[str, Any] | None:
        c = self._claims.get(claim_number)
        return copy.deepcopy(c) if c else None

    def list_claims_for_policy(self, policy_number: str) -> list[dict[str, Any]]:
        return [copy.deepcopy(c) for c in self._claims.values() if c["policy_number"] == policy_number]

    def next_claim_number(self, year: int) -> str:
        with self._lock:
            self._claim_seq += 1
            return f"CLM-{year}-{self._claim_seq:06d}"

    def save_claim(self, claim: dict[str, Any]) -> None:
        with self._lock:
            self._claims[claim["claim_number"]] = copy.deepcopy(claim)

    # ---- callbacks -------------------------------------------------------
    def save_callback(self, callback: dict[str, Any]) -> str:
        with self._lock:
            self._callback_seq += 1
            callback_id = f"CB-{self._callback_seq}"
            self._callbacks[callback_id] = {**copy.deepcopy(callback), "callback_id": callback_id}
            return callback_id
