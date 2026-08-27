from datetime import datetime

import pytest
from sqlalchemy import inspect, text

from app.database import SessionLocal, engine
from app.models import Contact


BASE = "/api/v1/contacts"
FLAT_ADDRESS_FIELDS = {"address", "city", "state", "postal_code", "country"}
_ADDRESSES_OMITTED = object()


def contact_payload(
    payload: dict,
    addresses: list[dict] | object = _ADDRESSES_OMITTED,
) -> dict:
    result = {
        key: value
        for key, value in payload.items()
        if key not in FLAT_ADDRESS_FIELDS | {"addresses"}
    }
    if addresses is not _ADDRESSES_OMITTED:
        result["addresses"] = addresses
    return result


def address(kind: str = "Home", street: str = "1 Market St", **overrides) -> dict:
    return {
        "type": kind,
        "address": street,
        "city": "San Francisco",
        "state": "CA",
        "postal_code": "94105",
        "country": "USA",
        **overrides,
    }


def test_addresses_table_and_relationship_contract(client):
    database = inspect(engine)
    assert "addresses" in database.get_table_names()

    columns = {column["name"]: column for column in database.get_columns("addresses")}
    assert set(columns) >= {
        "id",
        "contact_id",
        "type",
        "address",
        "city",
        "state",
        "postal_code",
        "country",
    }
    assert columns["contact_id"]["nullable"] is False
    assert columns["type"]["nullable"] is False
    assert columns["address"]["nullable"] is False

    foreign_keys = database.get_foreign_keys("addresses")
    assert len(foreign_keys) == 1
    assert foreign_keys[0]["constrained_columns"] == ["contact_id"]
    assert foreign_keys[0]["referred_table"] == "contacts"
    assert foreign_keys[0]["options"]["ondelete"] == "CASCADE"

    constraints = " ".join(
        constraint["sqltext"] for constraint in database.get_check_constraints("addresses")
    )
    assert all(value in constraints for value in ("Home", "Work", "Other"))

    relationship = inspect(Contact).relationships["addresses"]
    assert relationship.back_populates == "contact"
    assert relationship.lazy == "selectin"
    assert "delete-orphan" in relationship.cascade
    assert relationship.order_by
    assert relationship.mapper.relationships["contact"].back_populates == "addresses"


def test_create_without_addresses_defaults_to_empty_list(client, payload):
    response = client.post(BASE, json=contact_payload(payload))

    assert response.status_code == 201
    assert "addresses" in response.json()
    assert response.json()["addresses"] == []


def test_create_get_and_list_preserve_all_address_types_and_order(client, payload):
    expected = [
        address("Home", "1 Home St"),
        address("Work", "2 Work Ave"),
        address("Other", "3 Other Rd"),
    ]

    created = client.post(BASE, json=contact_payload(payload, expected))
    assert created.status_code == 201
    body = created.json()
    assert [item["type"] for item in body["addresses"]] == ["Home", "Work", "Other"]
    assert [item["address"] for item in body["addresses"]] == [
        "1 Home St",
        "2 Work Ave",
        "3 Other Rd",
    ]
    assert all(isinstance(item["id"], int) and item["id"] > 0 for item in body["addresses"])
    assert FLAT_ADDRESS_FIELDS.isdisjoint(body)

    contact_id = body["id"]
    fetched = client.get(f"{BASE}/{contact_id}").json()
    listed = client.get(BASE).json()["items"][0]
    assert fetched["addresses"] == body["addresses"]
    assert listed["addresses"] == body["addresses"]


@pytest.mark.parametrize(
    "invalid_address",
    [
        {"type": "Business", "address": "1 Main St"},
        {"type": "home", "address": "1 Main St"},
        {"type": "Home"},
        {"type": "Home", "address": ""},
        {"type": "Home", "address": "   "},
        {"type": "Home", "address": "x" * 301},
        {"type": "Home", "address": "1 Main St", "city": "x" * 121},
        {"type": "Home", "address": "1 Main St", "state": "x" * 121},
        {"type": "Home", "address": "1 Main St", "postal_code": "x" * 21},
        {"type": "Home", "address": "1 Main St", "country": "x" * 121},
    ],
)
def test_rejects_invalid_nested_address(client, payload, invalid_address):
    response = client.post(BASE, json=contact_payload(payload, [invalid_address]))

    assert response.status_code == 422


def test_required_street_is_trimmed(client, payload):
    response = client.post(
        BASE,
        json=contact_payload(payload, [address(street="  1 Market St  ")]),
    )

    assert response.status_code == 201
    assert response.json()["addresses"][0]["address"] == "1 Market St"


def test_put_with_explicit_addresses_replaces_them_and_touches_contact(client, payload):
    created = client.post(BASE, json=contact_payload(payload, [address()])).json()
    old_address_id = created["addresses"][0]["id"]
    old_updated_at = datetime.fromisoformat(created["updated_at"])
    replacement = [address("Work", "200 Howard St"), address("Other", "300 Pine St")]

    response = client.put(
        f"{BASE}/{created['id']}",
        json=contact_payload(payload, replacement),
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["address"] for item in body["addresses"]] == ["200 Howard St", "300 Pine St"]
    assert old_address_id not in {item["id"] for item in body["addresses"]}
    assert datetime.fromisoformat(body["updated_at"]) > old_updated_at


def test_put_with_empty_addresses_clears_them(client, payload):
    created = client.post(BASE, json=contact_payload(payload, [address()])).json()

    response = client.put(f"{BASE}/{created['id']}", json=contact_payload(payload, []))

    assert response.status_code == 200
    assert response.json()["addresses"] == []


def test_put_omitting_addresses_preserves_them_for_compatibility(client, payload):
    created = client.post(BASE, json=contact_payload(payload, [address()])).json()

    response = client.put(f"{BASE}/{created['id']}", json=contact_payload(payload))

    assert response.status_code == 200
    assert response.json()["addresses"] == created["addresses"]


def test_patch_omitting_addresses_preserves_them(client, payload):
    created = client.post(BASE, json=contact_payload(payload, [address()])).json()

    response = client.patch(f"{BASE}/{created['id']}", json={"company": "New Company"})

    assert response.status_code == 200
    assert response.json()["addresses"] == created["addresses"]


def test_patch_with_addresses_replaces_them_and_touches_contact(client, payload):
    created = client.post(BASE, json=contact_payload(payload, [address()])).json()
    old_updated_at = datetime.fromisoformat(created["updated_at"])

    response = client.patch(
        f"{BASE}/{created['id']}",
        json={"addresses": [address("Work", "200 Howard St")]},
    )

    assert response.status_code == 200
    assert [item["address"] for item in response.json()["addresses"]] == ["200 Howard St"]
    assert datetime.fromisoformat(response.json()["updated_at"]) > old_updated_at


def test_patch_with_empty_addresses_clears_them(client, payload):
    created = client.post(BASE, json=contact_payload(payload, [address()])).json()

    response = client.patch(f"{BASE}/{created['id']}", json={"addresses": []})

    assert response.status_code == 200
    assert response.json()["addresses"] == []


@pytest.mark.parametrize("method", ["put", "patch"])
def test_invalid_address_replacement_is_atomic(client, payload, method):
    created = client.post(BASE, json=contact_payload(payload, [address()])).json()
    request_body = {
        "addresses": [address("Work", "200 Howard St"), {"type": "Other", "address": " "}]
    }
    if method == "put":
        request_body = {**contact_payload(payload), **request_body}

    response = getattr(client, method)(f"{BASE}/{created['id']}", json=request_body)

    assert response.status_code == 422
    assert client.get(f"{BASE}/{created['id']}").json()["addresses"] == created["addresses"]


def test_delete_cascades_to_address_rows(client, payload):
    created = client.post(BASE, json=contact_payload(payload, [address(), address("Work")]))
    assert created.status_code == 201
    assert len(created.json()["addresses"]) == 2
    contact_id = created.json()["id"]

    assert client.delete(f"{BASE}/{contact_id}").status_code == 204

    with SessionLocal() as db:
        remaining = db.execute(
            text("SELECT COUNT(*) FROM addresses WHERE contact_id = :contact_id"),
            {"contact_id": contact_id},
        ).scalar_one()
    assert remaining == 0
