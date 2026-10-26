"""REST endpoints. Same service layer as the MCP tools, so both stay in sync."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from .services import InsuranceService


class VerifyRequest(BaseModel):
    policy_number: str = Field(examples=["AUT-100234"])
    date_of_birth: str = Field(examples=["1985-03-14"])
    zip_code: str = Field(examples=["60614"])


class ClaimRequest(BaseModel):
    policy_number: str = Field(examples=["AUT-100234"])
    incident_type: str = Field(examples=["collision"])
    incident_date: str = Field(examples=["2026-10-20"])
    description: str = Field(examples=["Backed into a pole in a parking garage. Rear bumper cracked."])
    location: str | None = None
    injuries: bool = False
    police_report_number: str | None = None
    contact_phone: str | None = None
    vehicle: str | None = None


class NoteRequest(BaseModel):
    note: str = Field(examples=["Police report number 26-118842"])


class CallbackRequest(BaseModel):
    policy_number: str = Field(examples=["AUT-100512"])
    phone: str = Field(examples=["512-555-0177"])
    reason: str = Field(examples=["Wants to add a teen driver"])
    preferred_time: str | None = Field(default=None, examples=["tomorrow after 3 pm"])


router = APIRouter(prefix="/api")


def _svc(request: Request) -> InsuranceService:
    return request.app.state.service


@router.get("/customers/search", tags=["customers"])
def search_customers(request: Request, full_name: str | None = None, phone: str | None = None,
                     email: str | None = None, policy_number: str | None = None) -> dict[str, Any]:
    return _svc(request).find_customer(full_name, phone, email, policy_number)


@router.post("/customers/verify", tags=["customers"])
def verify_customer(request: Request, body: VerifyRequest) -> dict[str, Any]:
    return _svc(request).verify_identity(body.policy_number, body.date_of_birth, body.zip_code)


@router.get("/customers/{customer_id}/policies", tags=["customers"])
def customer_policies(request: Request, customer_id: str) -> dict[str, Any]:
    return _svc(request).list_customer_policies(customer_id)


@router.get("/policies/{policy_number}", tags=["policies"])
def policy_details(request: Request, policy_number: str) -> dict[str, Any]:
    return _svc(request).get_policy_details(policy_number)


@router.get("/policies/{policy_number}/coverage-check", tags=["policies"])
def coverage_check(request: Request, policy_number: str,
                   incident_type: str = Query(examples=["hail damage to roof"]),
                   incident_date: str | None = None) -> dict[str, Any]:
    return _svc(request).check_coverage(policy_number, incident_type, incident_date)


@router.get("/policies/{policy_number}/billing", tags=["billing"])
def billing(request: Request, policy_number: str) -> dict[str, Any]:
    return _svc(request).get_billing_summary(policy_number)


@router.get("/policies/{policy_number}/claims", tags=["claims"])
def policy_claims(request: Request, policy_number: str) -> dict[str, Any]:
    return _svc(request).list_claims(policy_number)


@router.get("/claims/{claim_number}", tags=["claims"])
def claim_status(request: Request, claim_number: str) -> dict[str, Any]:
    return _svc(request).get_claim_status(claim_number)


@router.post("/claims", tags=["claims"], status_code=201)
def create_claim(request: Request, body: ClaimRequest) -> dict[str, Any]:
    return _svc(request).file_claim(**body.model_dump())


@router.post("/claims/{claim_number}/notes", tags=["claims"])
def create_claim_note(request: Request, claim_number: str, body: NoteRequest) -> dict[str, Any]:
    return _svc(request).add_claim_note(claim_number, body.note)


@router.post("/callbacks", tags=["service"], status_code=201)
def create_callback(request: Request, body: CallbackRequest) -> dict[str, Any]:
    return _svc(request).request_callback(body.policy_number, body.phone, body.reason, body.preferred_time)
