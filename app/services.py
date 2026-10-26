"""Business logic shared by the REST API and the MCP tools.

Every public method returns a plain ``dict`` that includes a ``summary`` field: one or two
short sentences a voice agent can read aloud as-is. The structured fields beside it let the
model answer follow-up questions without another tool call.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Callable

from .repository import InMemoryRepository


class NotFoundError(Exception):
    """Raised when a customer, policy, or claim does not exist."""


class InvalidRequestError(Exception):
    """Raised when input is missing or cannot be processed."""


# --------------------------------------------------------------------------- helpers

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%Y/%m/%d")


def parse_date(value: str | date, field: str = "date") -> date:
    """Parse common spoken/written date formats ("1985-03-14", "03/14/1985", "March 14, 1985")."""
    if isinstance(value, date):
        return value
    if not value or not str(value).strip():
        raise InvalidRequestError(f"{field} is required.")
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", str(value).strip()).replace(",", " ")
    text = re.sub(r"\s+", " ", text)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise InvalidRequestError(f"Could not understand {field} '{value}'. Use the format YYYY-MM-DD.")


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")[-10:]


def normalize_policy_number(value: str) -> str:
    """Accept 'aut 100234', 'AUT100234', 'aut-100234' and return 'AUT-100234'."""
    text = re.sub(r"[^A-Za-z0-9]", "", value or "").upper()
    m = re.fullmatch(r"([A-Z]{3})(\d{6})", text)
    return f"{m.group(1)}-{m.group(2)}" if m else (value or "").strip().upper()


def normalize_claim_number(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]", "", value or "").upper()
    m = re.fullmatch(r"CLM(\d{4})(\d{6})", text)
    return f"CLM-{m.group(1)}-{m.group(2)}" if m else (value or "").strip().upper()


def _money(amount: float | int | None) -> str:
    if amount is None:
        return "not available"
    return f"${amount:,.2f}".replace(".00", "")


def _mask_email(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[0]}***@{domain}" if name else email


def _mask_phone(phone: str) -> str:
    d = _digits(phone)
    return f"***-***-{d[-4:]}" if len(d) >= 4 else "***"


_SPOKEN_NAMES = {"wind_hail": "wind and hail", "water_backup": "sewer and water backup"}


def _speakable(key: str) -> str:
    return _SPOKEN_NAMES.get(key, key.replace("_", " "))


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text and not text[:2].isupper() else text


_STATUS_TEXT = {
    "submitted": "submitted and waiting for an adjuster",
    "assigned": "assigned to an adjuster",
    "under_investigation": "under investigation",
    "estimate_approved": "approved, with the repair estimate accepted",
    "payment_issued": "paid",
    "closed": "closed",
    "denied": "denied",
}

# Incident keyword -> coverage key, per line of business. The first matching rule wins.
_AUTO_RULES: list[tuple[tuple[str, ...], str]] = [
    (("windshield", "glass", "window", "rock chip"), "glass"),
    (("uninsured", "underinsured", "hit and run", "hit-and-run", "no insurance"), "uninsured_motorist"),
    (("tow", "roadside", "flat tire", "jump", "lockout", "locked out", "battery", "out of gas"), "roadside_assistance"),
    (("rental", "rent a car", "loaner"), "rental_reimbursement"),
    (("injur", "medical", "hospital", "hurt"), "medical_payments"),
    (("i hit", "at fault", "my fault", "liability", "someone else's car", "damaged property"), "liability"),
    (("theft", "stolen", "vandal", "hail", "flood", "fire", "deer", "animal", "tree", "falling", "storm", "break-in", "broke into"), "comprehensive"),
    (("collision", "crash", "accident", "rear-ended", "rear ended", "fender", "hit a", "pole", "rollover"), "collision"),
]

_PROPERTY_EXCLUSION_RULES: list[tuple[tuple[str, ...], str]] = [
    (("flood", "rising water", "storm surge"), "flood"),
    (("earthquake", "sinkhole", "landslide"), "earthquake"),
    (("mold", "mildew"), "mold"),
    (("wear", "old age", "deteriorat", "rust"), "wear_and_tear"),
    (("termite", "rodent", "pest", "insect", "mice", "rats"), "pests"),
]

_HOME_RULES: list[tuple[tuple[str, ...], str]] = [
    (("sewer", "backup", "back-up", "sump pump"), "water_backup"),
    (("hail", "wind", "tornado", "hurricane"), "wind_hail"),
    (("slip", "tripped", "dog bite", "guest", "injur", "liability", "sued", "lawsuit"), "personal_liability"),
    (("theft", "stolen", "burglary", "break-in", "broke in", "jewelry", "laptop", "belongings", "furniture", "electronics"), "personal_property"),
    (("fence", "shed", "detached garage"), "other_structures"),
    (("hotel", "displaced", "can't live", "cannot live", "uninhabitable"), "loss_of_use"),
    (("fire", "smoke", "lightning", "burst", "pipe", "leak", "water", "roof", "tree", "vandal", "explosion", "ice dam", "freeze"), "dwelling"),
]

_RENTERS_RULES: list[tuple[tuple[str, ...], str]] = [
    (("slip", "tripped", "dog bite", "guest", "injur", "liability", "sued", "lawsuit", "damaged the unit"), "personal_liability"),
    (("hotel", "displaced", "can't live", "cannot live", "uninhabitable"), "loss_of_use"),
    (("theft", "stolen", "burglary", "break-in", "broke in", "fire", "smoke", "burst", "pipe", "leak", "water", "vandal", "laptop", "belongings", "electronics", "furniture"), "personal_property"),
]

_ADJUSTERS = {
    "auto": {"name": "Kevin Brooks", "phone": "1-800-555-0199 ext. 4412"},
    "home": {"name": "Angela Ruiz", "phone": "1-800-555-0199 ext. 3108"},
    "renters": {"name": "Marcus Lee", "phone": "1-800-555-0199 ext. 2270"},
    "life": {"name": "Helen Park", "phone": "1-800-555-0199 ext. 5001"},
}


def _match(rules: list[tuple[tuple[str, ...], str]], text: str) -> str | None:
    for keywords, result in rules:
        if any(k in text for k in keywords):
            return result
    return None


# --------------------------------------------------------------------------- service


class InsuranceService:
    def __init__(self, repo: InMemoryRepository | None = None, today: Callable[[], date] = date.today) -> None:
        self.repo = repo or InMemoryRepository()
        self._today = today

    # ---- internal lookups ----------------------------------------------------
    def _policy(self, policy_number: str) -> dict[str, Any]:
        pn = normalize_policy_number(policy_number)
        policy = self.repo.get_policy(pn)
        if not policy:
            raise NotFoundError(f"I couldn't find policy {policy_number}. Please double-check the policy number.")
        return policy

    def _customer(self, customer_id: str) -> dict[str, Any]:
        c = self.repo.get_customer(customer_id)
        if not c:
            raise NotFoundError(f"Customer {customer_id} was not found.")
        return c

    def _claim(self, claim_number: str) -> dict[str, Any]:
        cn = normalize_claim_number(claim_number)
        claim = self.repo.get_claim(cn)
        if not claim:
            raise NotFoundError(f"I couldn't find claim {claim_number}. Please double-check the claim number.")
        return claim

    def _in_force_on(self, policy: dict[str, Any], on: date) -> tuple[bool, str]:
        eff = parse_date(policy["effective_date"])
        exp = parse_date(policy["expiration_date"])
        cancelled = policy.get("cancellation_date")
        if on < eff:
            return False, f"coverage on policy {policy['policy_number']} doesn't start until {eff.isoformat()}"
        if cancelled and on >= parse_date(cancelled):
            return False, f"policy {policy['policy_number']} was cancelled on {cancelled}"
        if on >= exp:
            return False, f"policy {policy['policy_number']} expired on {exp.isoformat()}"
        return True, "in force"

    @staticmethod
    def _policy_brief(p: dict[str, Any]) -> dict[str, Any]:
        return {
            "policy_number": p["policy_number"],
            "line": p["line"],
            "product": p["product"],
            "status": p["status"],
            "effective_date": p["effective_date"],
            "expiration_date": p["expiration_date"],
            "insured_items": [i["description"] for i in p.get("insured_items", [])],
        }

    # ---- customers -----------------------------------------------------------
    def find_customer(
        self,
        full_name: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        policy_number: str | None = None,
    ) -> dict[str, Any]:
        if not any([full_name, phone, email, policy_number]):
            raise InvalidRequestError("Provide at least one of: full name, phone, email, or policy number.")

        candidates = self.repo.list_customers()
        if policy_number:
            policy = self.repo.get_policy(normalize_policy_number(policy_number))
            candidates = [c for c in candidates if policy and c["customer_id"] == policy["customer_id"]]
        if phone:
            d = _digits(phone)
            candidates = [c for c in candidates if _digits(c["phone"]) == d]
        if email:
            candidates = [c for c in candidates if c["email"].lower() == email.strip().lower()]
        if full_name:
            tokens = [t for t in re.split(r"\s+", full_name.lower().strip()) if t]
            candidates = [
                c for c in candidates
                if all(t in f"{c['first_name']} {c['last_name']}".lower() for t in tokens)
            ]

        matches = [
            {
                "customer_id": c["customer_id"],
                "name": f"{c['first_name']} {c['last_name']}",
                "city": c["address"]["city"],
                "state": c["address"]["state"],
                "phone_masked": _mask_phone(c["phone"]),
                "email_masked": _mask_email(c["email"]),
                "policy_numbers": [p["policy_number"] for p in self.repo.list_policies_for_customer(c["customer_id"])],
            }
            for c in candidates
        ]
        if not matches:
            summary = "I couldn't find a customer with those details."
        elif len(matches) == 1:
            m = matches[0]
            summary = f"I found {m['name']} in {m['city']}, {m['state']}. Please verify identity before sharing policy details."
        else:
            summary = f"I found {len(matches)} possible matches. Ask for a policy number or phone number to narrow it down."
        return {"match_count": len(matches), "matches": matches, "summary": summary}

    def verify_identity(self, policy_number: str, date_of_birth: str, zip_code: str) -> dict[str, Any]:
        policy = self._policy(policy_number)
        customer = self._customer(policy["customer_id"])
        dob_ok = parse_date(date_of_birth, "date of birth") == parse_date(customer["date_of_birth"])
        zip_ok = re.sub(r"\D", "", zip_code or "")[:5] == customer["address"]["zip"]
        verified = dob_ok and zip_ok
        result: dict[str, Any] = {"verified": verified, "policy_number": policy["policy_number"]}
        if verified:
            result.update(
                customer_id=customer["customer_id"],
                first_name=customer["first_name"],
                last_name=customer["last_name"],
                summary=f"Thanks, {customer['first_name']}. Your identity is verified.",
            )
        else:
            result["summary"] = (
                "Those details don't match our records, so I can't share policy information yet. "
                "Please check the date of birth and ZIP code and try again."
            )
        return result

    def list_customer_policies(self, customer_id: str) -> dict[str, Any]:
        customer = self._customer(customer_id)
        policies = [self._policy_brief(p) for p in self.repo.list_policies_for_customer(customer_id)]
        parts = [f"{p['line']} policy {p['policy_number']} ({p['status']})" for p in policies]
        summary = (
            f"{customer['first_name']} has {len(policies)} "
            f"polic{'y' if len(policies) == 1 else 'ies'}: " + "; ".join(parts) + "."
            if policies else f"{customer['first_name']} has no policies on file."
        )
        return {"customer_id": customer_id, "policies": policies, "summary": summary}

    # ---- policies ------------------------------------------------------------
    def get_policy_details(self, policy_number: str) -> dict[str, Any]:
        p = self._policy(policy_number)
        c = self._customer(p["customer_id"])
        coverages = [
            {"coverage": k, "limit": v["limit"], "deductible": v.get("deductible", 0), **({"note": v["deductible_note"]} if "deductible_note" in v else {})}
            for k, v in p["coverages"].items()
        ]
        result = {
            **self._policy_brief(p),
            "policyholder": f"{c['first_name']} {c['last_name']}",
            "status_reason": p.get("status_reason"),
            "cancellation_date": p.get("cancellation_date"),
            "drivers": p.get("drivers"),
            "beneficiaries": p.get("beneficiaries"),
            "coverages": coverages,
            "exclusions": p.get("exclusions", []),
        }
        items = ", ".join(result["insured_items"])
        if p["status"] == "active":
            status_text = f"is active through {p['expiration_date']}"
        elif p["status"] == "pending":
            status_text = f"starts on {p['effective_date']}"
        else:
            status_text = f"is {p['status']}" + (f" ({p['status_reason'].lower()})" if p.get("status_reason") else "")
        result["summary"] = (
            f"Your {p['product']} policy {p['policy_number']} covering {items} {status_text}. "
            f"It includes {len(coverages)} coverages, such as {', '.join(_speakable(cv['coverage']) for cv in coverages[:3])}."
        )
        return {k: v for k, v in result.items() if v is not None}

    def check_coverage(self, policy_number: str, incident_type: str, incident_date: str | None = None) -> dict[str, Any]:
        p = self._policy(policy_number)
        text = (incident_type or "").lower().strip()
        if not text:
            raise InvalidRequestError("Describe the incident, for example 'hail damage to roof' or 'rear-ended'.")
        on = parse_date(incident_date, "incident date") if incident_date else self._today()
        in_force, why = self._in_force_on(p, on)

        base: dict[str, Any] = {"policy_number": p["policy_number"], "incident_type": incident_type, "policy_in_force": in_force}
        if not in_force:
            return {**base, "covered": False, "reason": why, "summary": f"That wouldn't be covered because {why}."}

        line = p["line"]
        if line == "life":
            cov = "death_benefit" if any(k in text for k in ("death", "died", "passed", "deceased")) else (
                "accelerated_death_benefit" if "terminal" in text else None)
            if not cov:
                return {**base, "covered": False, "reason": "Life policies only pay death or terminal-illness benefits.",
                        "summary": "A term life policy only pays a death benefit, or an accelerated benefit for a terminal illness."}
        else:
            if line in ("home", "renters"):
                excl = _match(_PROPERTY_EXCLUSION_RULES, text)
                if excl and excl in p.get("exclusions", []) and not (excl == "flood" and "backup" in text):
                    return {**base, "covered": False, "excluded_peril": excl,
                            "reason": f"{_speakable(excl).capitalize()} is excluded from this policy.",
                            "summary": f"Unfortunately {_speakable(excl)} is excluded from your {line} policy. "
                                       f"Separate {'flood' if excl == 'flood' else _speakable(excl)} coverage would be needed."}
            rules = {"auto": _AUTO_RULES, "home": _HOME_RULES, "renters": _RENTERS_RULES}[line]
            cov = _match(rules, text)
            if line == "auto" and cov == "glass" and "glass" not in p["coverages"]:
                cov = "comprehensive"
            if line == "auto" and cov == "uninsured_motorist" and "uninsured_motorist" not in p["coverages"]:
                cov = "collision"

        if cov is None:
            return {**base, "covered": None, "needs_review": True,
                    "summary": "I'm not sure which coverage applies to that. A claims specialist can review it if you file a claim or request a callback."}
        if cov not in p["coverages"]:
            return {**base, "covered": False, "coverage": cov,
                    "reason": f"This policy does not include {_speakable(cov)} coverage.",
                    "summary": f"Your policy doesn't include {_speakable(cov)} coverage, so that wouldn't be covered."}

        detail = p["coverages"][cov]
        deductible = detail.get("deductible", 0)
        ded_text = f"a {_money(deductible)} deductible" + (f" ({detail['deductible_note']})" if detail.get("deductible_note") else "") if deductible else "no deductible"
        return {
            **base, "covered": True, "coverage": cov, "limit": detail["limit"], "deductible": deductible,
            "summary": f"Yes, that should fall under your {_speakable(cov)} coverage, with {ded_text} and a limit of {_lower_first(detail['limit'])}. "
                       "Final coverage is confirmed by the adjuster after a claim is filed.",
        }

    def get_billing_summary(self, policy_number: str) -> dict[str, Any]:
        p = self._policy(policy_number)
        b = p["billing"]
        result = {"policy_number": p["policy_number"], "policy_status": p["status"], **b}
        if p["status"] == "cancelled":
            result["summary"] = f"Policy {p['policy_number']} is cancelled, so there's nothing due. {p.get('status_reason', '')}".strip()
        elif b.get("past_due_amount"):
            result["summary"] = (
                f"Your account is past due by {_money(b['past_due_amount'])}. The total due is {_money(b['amount_due'])}. "
                f"To avoid cancellation, please pay by {b.get('grace_period_ends', b['next_due_date'])}."
            )
        elif b.get("amount_due"):
            ap = " It will be paid automatically" + (f" from your {_lower_first(b['payment_method'])}" if b.get("payment_method") else "") + "." if b.get("autopay") else ""
            result["summary"] = f"Your next payment of {_money(b['amount_due'])} is due on {b['next_due_date']}.{ap}"
        else:
            result["summary"] = f"You're all paid up. Your next bill is due on {b['next_due_date']}."
        result["payment_options"] = (
            f"Pay online at {self.repo.company['portal_url']} or call {self.repo.company['service_phone']}."
        )
        return result

    # ---- claims --------------------------------------------------------------
    @staticmethod
    def _claim_view(c: dict[str, Any]) -> dict[str, Any]:
        return {
            "claim_number": c["claim_number"],
            "policy_number": c["policy_number"],
            "incident_type": c["incident_type"],
            "incident_date": c["incident_date"],
            "status": c["status"],
            "status_text": _STATUS_TEXT.get(c["status"], c["status"]),
            "description": c["description"],
            "adjuster": c.get("adjuster"),
            "deductible": c.get("deductible"),
            "estimate_amount": c.get("estimate_amount"),
            "paid_amount": c.get("paid_amount"),
            "next_step": c.get("next_step"),
            "timeline": c.get("timeline", []),
            "notes": c.get("notes", []),
        }

    def list_claims(self, policy_number: str) -> dict[str, Any]:
        p = self._policy(policy_number)
        claims = sorted(self.repo.list_claims_for_policy(p["policy_number"]), key=lambda c: c["incident_date"], reverse=True)
        brief = [
            {"claim_number": c["claim_number"], "incident_type": c["incident_type"], "incident_date": c["incident_date"],
             "status": c["status"], "status_text": _STATUS_TEXT.get(c["status"], c["status"])}
            for c in claims
        ]
        open_claims = [c for c in brief if c["status"] not in ("closed", "denied")]
        if not brief:
            summary = f"There are no claims on policy {p['policy_number']}."
        else:
            summary = f"Policy {p['policy_number']} has {len(brief)} claim{'s' if len(brief) != 1 else ''}, {len(open_claims)} open. " + " ".join(
                f"Claim {c['claim_number']} for {c['incident_type']} on {c['incident_date']} is {c['status_text']}." for c in brief[:3])
        return {"policy_number": p["policy_number"], "claims": brief, "summary": summary}

    def get_claim_status(self, claim_number: str) -> dict[str, Any]:
        c = self._claim(claim_number)
        view = self._claim_view(c)
        adj = c.get("adjuster") or {}
        money = ""
        if c.get("paid_amount"):
            money = f" We've paid {_money(c['paid_amount'])} so far."
        elif c.get("estimate_amount"):
            money = f" The current estimate is {_money(c['estimate_amount'])}, before your {_money(c.get('deductible'))} deductible."
        view["summary"] = (
            f"Claim {c['claim_number']} for {c['incident_type']} is {view['status_text']}.{money} "
            f"{c.get('next_step') or ''}"
            + (f" Your adjuster is {adj['name']}, reachable at {adj['phone']}." if adj.get("name") else "")
        ).replace("  ", " ").strip()
        return view

    def file_claim(
        self,
        policy_number: str,
        incident_type: str,
        incident_date: str,
        description: str,
        location: str | None = None,
        injuries: bool = False,
        police_report_number: str | None = None,
        contact_phone: str | None = None,
        vehicle: str | None = None,
    ) -> dict[str, Any]:
        p = self._policy(policy_number)
        if not description or len(description.strip()) < 5:
            raise InvalidRequestError("Please include a short description of what happened.")
        when = parse_date(incident_date, "incident date")
        today = self._today()
        if when > today:
            raise InvalidRequestError("The incident date can't be in the future.")

        coverage = self.check_coverage(p["policy_number"], incident_type, when.isoformat())
        if not coverage["policy_in_force"]:
            return {"filed": False, "policy_number": p["policy_number"], "reason": coverage["reason"],
                    "summary": f"I can't open a claim on this policy because {coverage['reason']}. "
                               f"You can call {self.repo.company['claims_phone']} to speak with a claims specialist."}

        claim_number = self.repo.next_claim_number(today.year)
        adjuster = _ADJUSTERS.get(p["line"], _ADJUSTERS["auto"])
        details = [f"Location: {location}" if location else None,
                   f"Vehicle: {vehicle}" if vehicle else None,
                   f"Police report: {police_report_number}" if police_report_number else None,
                   "Injuries reported" if injuries else None,
                   f"Contact phone: {contact_phone}" if contact_phone else None]
        next_steps = [f"Adjuster {adjuster['name']} will contact you within one business day."]
        if injuries:
            next_steps.insert(0, "If anyone needs medical care, call 911 first. A medical claims specialist will also reach out.")
        if p["line"] == "auto" and "rental_reimbursement" in p["coverages"]:
            next_steps.append(f"Rental reimbursement is available: {p['coverages']['rental_reimbursement']['limit']}.")
        if not police_report_number and any(k in incident_type.lower() for k in ("theft", "stolen", "vandal", "break", "hit and run")):
            next_steps.append("Please file a police report and add the report number to this claim.")
        next_steps.append(f"Upload photos and receipts at {self.repo.company['portal_url']}.")

        claim = {
            "claim_number": claim_number,
            "policy_number": p["policy_number"],
            "incident_type": incident_type,
            "coverage": coverage.get("coverage"),
            "incident_date": when.isoformat(),
            "reported_date": today.isoformat(),
            "description": description.strip(),
            "status": "assigned",
            "adjuster": adjuster,
            "deductible": coverage.get("deductible"),
            "estimate_amount": None,
            "paid_amount": 0.0,
            "next_step": next_steps[0] if not injuries else next_steps[1],
            "timeline": [{"date": today.isoformat(), "event": "Claim reported by voice agent"},
                         {"date": today.isoformat(), "event": f"Assigned to adjuster {adjuster['name']}"}],
            "notes": [d for d in details if d],
        }
        self.repo.save_claim(claim)

        cov_text = (f"It looks like this falls under your {_speakable(coverage['coverage'])} coverage with a {_money(coverage['deductible'])} deductible. "
                    if coverage.get("covered") and coverage.get("deductible") else "")
        if coverage.get("covered") is False:
            cov_text = "Based on your policy this may not be covered, but an adjuster will review it. "
        return {
            "filed": True,
            "claim_number": claim_number,
            "status": "assigned",
            "coverage_check": {k: coverage.get(k) for k in ("covered", "coverage", "deductible", "limit", "reason") if coverage.get(k) is not None},
            "adjuster": adjuster,
            "next_steps": next_steps,
            "summary": f"Your claim is filed. Your claim number is {claim_number}. {cov_text}{next_steps[0]}",
        }

    def add_claim_note(self, claim_number: str, note: str) -> dict[str, Any]:
        c = self._claim(claim_number)
        if not note or not note.strip():
            raise InvalidRequestError("The note can't be empty.")
        today = self._today().isoformat()
        c.setdefault("notes", []).append(note.strip())
        c.setdefault("timeline", []).append({"date": today, "event": "Customer added information"})
        self.repo.save_claim(c)
        return {"claim_number": c["claim_number"], "notes": c["notes"],
                "summary": f"I've added that to claim {c['claim_number']}. Your adjuster will see it."}

    # ---- callbacks -----------------------------------------------------------
    def request_callback(self, policy_number: str, phone: str, reason: str, preferred_time: str | None = None) -> dict[str, Any]:
        p = self._policy(policy_number)
        if len(_digits(phone)) != 10:
            raise InvalidRequestError("Please provide a 10-digit callback phone number.")
        if not reason or not reason.strip():
            raise InvalidRequestError("Please include the reason for the callback.")
        callback = {"policy_number": p["policy_number"], "phone": phone, "reason": reason.strip(),
                    "preferred_time": preferred_time or "next available", "requested_on": self._today().isoformat()}
        callback_id = self.repo.save_callback(callback)
        return {"callback_id": callback_id, **callback,
                "summary": f"You're all set. A licensed agent will call you at the number ending in {_digits(phone)[-4:]} "
                           f"{'around ' + preferred_time if preferred_time else 'within one business day'}. "
                           f"Your reference number is {callback_id}."}
