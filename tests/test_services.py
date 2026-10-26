import pytest

from app.services import InvalidRequestError, NotFoundError, normalize_policy_number, parse_date


def test_normalizers_and_dates():
    assert normalize_policy_number("aut 100234") == "AUT-100234"
    assert normalize_policy_number("HOM200871") == "HOM-200871"
    assert parse_date("March 14th, 1985").isoformat() == "1985-03-14"
    assert parse_date("03/14/1985").isoformat() == "1985-03-14"
    with pytest.raises(InvalidRequestError):
        parse_date("someday")


def test_find_customer_by_phone_masks_pii(service):
    r = service.find_customer(phone="(312) 555-0142")
    assert r["match_count"] == 1
    m = r["matches"][0]
    assert m["name"] == "Maria Gonzalez"
    assert m["phone_masked"] == "***-***-0142"
    assert "maria.gonzalez" not in m["email_masked"]
    assert set(m["policy_numbers"]) == {"AUT-100234", "HOM-200871"}


def test_find_customer_requires_input(service):
    with pytest.raises(InvalidRequestError):
        service.find_customer()


def test_verify_identity(service):
    ok = service.verify_identity("AUT-100234", "March 14, 1985", "60614")
    assert ok["verified"] and ok["customer_id"] == "C1001"
    bad = service.verify_identity("AUT-100234", "1985-03-15", "60614")
    assert bad["verified"] is False and "customer_id" not in bad


def test_unknown_policy(service):
    with pytest.raises(NotFoundError):
        service.get_policy_details("AUT-999999")


def test_policy_details_and_summary(service):
    r = service.get_policy_details("AUT-100234")
    assert r["status"] == "active"
    assert any(c["coverage"] == "collision" and c["deductible"] == 500 for c in r["coverages"])
    assert "RAV4" in r["summary"]


@pytest.mark.parametrize("policy,incident,coverage,deductible", [
    ("AUT-100234", "rear-ended at a light", "collision", 500),
    ("AUT-100234", "cracked windshield", "glass", 0),
    ("AUT-100234", "car was stolen", "comprehensive", 250),
    ("AUT-100512", "cracked windshield", "comprehensive", 500),  # no glass coverage -> comprehensive
    ("HOM-200871", "hail damage to roof", "wind_hail", 9000),
    ("HOM-200871", "burst pipe in the kitchen", "dwelling", 1000),
    ("HOM-200871", "sewer backup in basement", "water_backup", 1000),
    ("REN-300045", "laptop stolen", "personal_property", 500),
])
def test_coverage_covered(service, policy, incident, coverage, deductible):
    r = service.check_coverage(policy, incident)
    assert r["covered"] is True, r
    assert r["coverage"] == coverage
    assert r["deductible"] == deductible


def test_coverage_exclusion(service):
    r = service.check_coverage("HOM-200871", "basement flooded after heavy rain")
    assert r["covered"] is False and r["excluded_peril"] == "flood"


def test_coverage_missing_coverage(service):
    r = service.check_coverage("AUT-100512", "need a rental car")
    assert r["covered"] is False and r["coverage"] == "rental_reimbursement"


def test_coverage_cancelled_and_pending(service):
    assert service.check_coverage("AUT-100777", "collision")["policy_in_force"] is False
    assert service.check_coverage("AUT-100903", "collision")["policy_in_force"] is False


def test_billing_past_due(service):
    r = service.get_billing_summary("AUT-100512")
    assert r["past_due_amount"] == 184.0
    assert "past due" in r["summary"]


def test_billing_autopay(service):
    r = service.get_billing_summary("AUT-100234")
    assert "automatically" in r["summary"]


def test_claims_list_and_status(service):
    r = service.list_claims("AUT-100234")
    assert r["claims"][0]["claim_number"] == "CLM-2026-000118"
    s = service.get_claim_status("clm 2026 000118")
    assert s["status"] == "estimate_approved"
    assert "Kevin Brooks" in s["summary"]


def test_file_claim_end_to_end(service):
    r = service.file_claim("AUT-100234", "collision", "2026-10-24", "Backed into a pole in a parking garage.",
                           location="Chicago", contact_phone="312-555-0142")
    assert r["filed"] is True
    assert r["coverage_check"]["coverage"] == "collision"
    claim_no = r["claim_number"]
    assert claim_no.startswith("CLM-2026-")
    assert service.get_claim_status(claim_no)["status"] == "assigned"
    assert claim_no in [c["claim_number"] for c in service.list_claims("AUT-100234")["claims"]]


def test_file_claim_rejects_future_and_out_of_force(service):
    with pytest.raises(InvalidRequestError):
        service.file_claim("AUT-100234", "collision", "2026-12-01", "Future crash")
    r = service.file_claim("AUT-100777", "collision", "2026-10-01", "Hit a guardrail")
    assert r["filed"] is False


def test_theft_claim_requests_police_report(service):
    r = service.file_claim("REN-300045", "theft", "2026-10-20", "Bike stolen from storage room")
    assert any("police report" in s for s in r["next_steps"])


def test_add_note_and_callback(service):
    n = service.add_claim_note("CLM-2026-000131", "Police report number 26-118842")
    assert "Police report number 26-118842" in n["notes"]
    cb = service.request_callback("AUT-100512", "512-555-0177", "Add a teen driver", "tomorrow after 3 pm")
    assert cb["callback_id"].startswith("CB-")
    with pytest.raises(InvalidRequestError):
        service.request_callback("AUT-100512", "555", "x")
