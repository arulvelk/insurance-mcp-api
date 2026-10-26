"""MCP tools exposed to the Azure AI Foundry voice agent.

Tools never raise to the model: errors come back as ``{"error": ..., "summary": ...}`` so a
voice agent can say what went wrong and ask the caller for a correction.
"""

from __future__ import annotations

from typing import Annotated, Any, Callable

from fastmcp import FastMCP
from pydantic import Field

from .services import InsuranceService, InvalidRequestError, NotFoundError

INSTRUCTIONS = """\
Tools for Contoso Mutual Insurance policy servicing and claims.
Typical flow: find_customer or ask for the policy number -> verify_customer_identity
(date of birth + ZIP) -> then policy, coverage, billing, and claim tools.
Every result has a short 'summary' you can read aloud. Never read out full account numbers.
"""

# Module level so the postponed annotations on the tool functions can resolve it.
PolicyNo = Annotated[str, Field(description="Policy number such as AUT-100234, HOM-200871 or REN-300045. Spaces or missing dashes are fine.")]


def _safe(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return fn()
    except (NotFoundError, InvalidRequestError) as e:
        return {"error": type(e).__name__, "summary": str(e)}


def build_mcp(service: InsuranceService) -> FastMCP:
    mcp = FastMCP(name="contoso-insurance", instructions=INSTRUCTIONS)

    @mcp.tool
    def find_customer(
        full_name: Annotated[str | None, Field(description="Caller's full or partial name")] = None,
        phone: Annotated[str | None, Field(description="Phone number on the account")] = None,
        email: Annotated[str | None, Field(description="Email on the account")] = None,
        policy_number: Annotated[str | None, Field(description="Any policy number the caller has")] = None,
    ) -> dict[str, Any]:
        """Look up a customer by name, phone, email or policy number. Returns masked contact info and policy numbers.
        Use before verification when the caller doesn't know their policy number."""
        return _safe(lambda: service.find_customer(full_name, phone, email, policy_number))

    @mcp.tool
    def verify_customer_identity(
        policy_number: PolicyNo,
        date_of_birth: Annotated[str, Field(description="Policyholder date of birth, preferably YYYY-MM-DD")],
        zip_code: Annotated[str, Field(description="5-digit ZIP code of the policyholder's address")],
    ) -> dict[str, Any]:
        """Verify the caller is the policyholder. Call this before sharing policy, billing or claim details."""
        return _safe(lambda: service.verify_identity(policy_number, date_of_birth, zip_code))

    @mcp.tool
    def list_customer_policies(
        customer_id: Annotated[str, Field(description="Customer id returned by verify_customer_identity or find_customer, e.g. C1001")],
    ) -> dict[str, Any]:
        """List all policies (auto, home, renters, life) that belong to a verified customer."""
        return _safe(lambda: service.list_customer_policies(customer_id))

    @mcp.tool
    def get_policy_details(policy_number: PolicyNo) -> dict[str, Any]:
        """Get policy status, term dates, insured vehicles or property, drivers, coverages with limits and deductibles, and exclusions."""
        return _safe(lambda: service.get_policy_details(policy_number))

    @mcp.tool
    def check_coverage(
        policy_number: PolicyNo,
        incident_type: Annotated[str, Field(description="What happened in plain words, e.g. 'rear-ended', 'hail damage to roof', 'laptop stolen', 'cracked windshield', 'basement flooded'")],
        incident_date: Annotated[str | None, Field(description="Date of the incident (YYYY-MM-DD). Defaults to today.")] = None,
    ) -> dict[str, Any]:
        """Check whether an incident is likely covered, which coverage applies, and the deductible and limit."""
        return _safe(lambda: service.check_coverage(policy_number, incident_type, incident_date))

    @mcp.tool
    def get_billing_summary(policy_number: PolicyNo) -> dict[str, Any]:
        """Get premium, amount due, next due date, past-due balance, autopay status and payment options."""
        return _safe(lambda: service.get_billing_summary(policy_number))

    @mcp.tool
    def list_claims(policy_number: PolicyNo) -> dict[str, Any]:
        """List claims on a policy, newest first, with their status."""
        return _safe(lambda: service.list_claims(policy_number))

    @mcp.tool
    def get_claim_status(
        claim_number: Annotated[str, Field(description="Claim number such as CLM-2026-000118")],
    ) -> dict[str, Any]:
        """Get a claim's status, adjuster contact, estimate, payments, next step and timeline."""
        return _safe(lambda: service.get_claim_status(claim_number))

    @mcp.tool
    def file_claim(
        policy_number: PolicyNo,
        incident_type: Annotated[str, Field(description="Short incident type, e.g. 'collision', 'hail', 'theft', 'water damage'")],
        incident_date: Annotated[str, Field(description="Date of the incident, YYYY-MM-DD")],
        description: Annotated[str, Field(description="What happened, in the caller's words")],
        location: Annotated[str | None, Field(description="Where it happened")] = None,
        injuries: Annotated[bool, Field(description="True if anyone was hurt")] = False,
        police_report_number: Annotated[str | None, Field(description="Police report number, if any")] = None,
        contact_phone: Annotated[str | None, Field(description="Best phone number to reach the caller")] = None,
        vehicle: Annotated[str | None, Field(description="For auto policies with several vehicles, which vehicle was involved")] = None,
    ) -> dict[str, Any]:
        """File a new claim (first notice of loss). Confirm the details with the caller before calling.
        Returns the new claim number, assigned adjuster and next steps."""
        return _safe(lambda: service.file_claim(policy_number, incident_type, incident_date, description,
                                                location, injuries, police_report_number, contact_phone, vehicle))

    @mcp.tool
    def add_claim_note(
        claim_number: Annotated[str, Field(description="Claim number such as CLM-2026-000131")],
        note: Annotated[str, Field(description="Information to add, e.g. a police report number or repair shop name")],
    ) -> dict[str, Any]:
        """Add information from the caller to an existing claim."""
        return _safe(lambda: service.add_claim_note(claim_number, note))

    @mcp.tool
    def request_callback(
        policy_number: PolicyNo,
        phone: Annotated[str, Field(description="10-digit callback phone number")],
        reason: Annotated[str, Field(description="Why the caller wants a human agent")],
        preferred_time: Annotated[str | None, Field(description="Preferred time, e.g. 'tomorrow after 3 pm'")] = None,
    ) -> dict[str, Any]:
        """Schedule a callback from a licensed human agent, e.g. for policy changes, payments or complaints."""
        return _safe(lambda: service.request_callback(policy_number, phone, reason, preferred_time))

    return mcp
