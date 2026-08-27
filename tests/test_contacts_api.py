import base64

import pytest


BASE = "/api/v1/contacts"
MAX_PHOTO_BYTES = 2 * 1024 * 1024


def photo_data_url(media_type: str, content: bytes) -> str:
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


PNG_PHOTO = photo_data_url("image/png", b"\x89PNG\r\n\x1a\nphoto")


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "sqlite"


def test_create_contact(client, payload):
    response = client.post(BASE, json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["id"] > 0
    assert body["email"] == "ada@example.com"
    assert body["full_name"] == "Ada Lovelace"
    assert body["created_at"] and body["updated_at"]


def test_create_requires_valid_email(client, payload):
    response = client.post(BASE, json={**payload, "email": "not-an-email"})
    assert response.status_code == 422


def test_create_requires_names(client, payload):
    response = client.post(BASE, json={**payload, "first_name": ""})
    assert response.status_code == 422


def test_duplicate_email_conflicts(client, payload):
    assert client.post(BASE, json=payload).status_code == 201
    response = client.post(BASE, json={**payload, "email": "ADA@example.com"})
    assert response.status_code == 409


def test_get_contact(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.get(f"{BASE}/{contact_id}")
    assert response.status_code == 200
    assert response.json()["id"] == contact_id


def test_get_missing_contact_returns_404(client):
    assert client.get(f"{BASE}/9999").status_code == 404


def test_list_pagination_and_total(client, payload):
    for index in range(5):
        client.post(BASE, json={**payload, "email": f"user{index}@example.com"})

    response = client.get(BASE, params={"limit": 2, "offset": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["limit"] == 2 and body["offset"] == 2


def test_list_search(client, payload):
    client.post(BASE, json=payload)
    client.post(
        BASE,
        json={**payload, "first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com", "company": "US Navy"},
    )

    hits = client.get(BASE, params={"search": "hopper"}).json()
    assert hits["total"] == 1
    assert hits["items"][0]["last_name"] == "Hopper"

    by_company = client.get(BASE, params={"search": "navy"}).json()
    assert by_company["total"] == 1

    misses = client.get(BASE, params={"search": "nobody"}).json()
    assert misses["total"] == 0


def test_list_sorting(client, payload):
    client.post(BASE, json={**payload, "last_name": "Zhang", "email": "z@example.com"})
    client.post(BASE, json={**payload, "last_name": "Adams", "email": "a@example.com"})

    names = [
        item["last_name"]
        for item in client.get(BASE, params={"sort_by": "last_name", "order": "asc"}).json()["items"]
    ]
    assert names == ["Adams", "Zhang"]


def test_list_rejects_bad_sort_field(client):
    assert client.get(BASE, params={"sort_by": "; DROP TABLE contacts"}).status_code == 422


def test_patch_updates_only_sent_fields(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"phone": "+1-000-000-0000"})
    assert response.status_code == 200
    body = response.json()
    assert body["phone"] == "+1-000-000-0000"
    assert body["first_name"] == "Ada"
    assert body["company"] == "Analytical Engines"


def test_patch_duplicate_email_conflicts(client, payload):
    first = client.post(BASE, json=payload).json()["id"]
    client.post(BASE, json={**payload, "email": "grace@example.com"})
    response = client.patch(f"{BASE}/{first}", json={"email": "grace@example.com"})
    assert response.status_code == 409


def test_patch_same_email_is_allowed(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"email": payload["email"]})
    assert response.status_code == 200


def test_put_replaces_contact(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.put(
        f"{BASE}/{contact_id}",
        json={"first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Grace Hopper"
    assert body["company"] is None  # omitted fields are cleared by PUT


def test_put_missing_contact_returns_404(client):
    response = client.put(
        f"{BASE}/9999",
        json={"first_name": "A", "last_name": "B", "email": "ab@example.com"},
    )
    assert response.status_code == 404


def test_photo_defaults_to_null(client, payload):
    response = client.post(BASE, json=payload)

    assert response.status_code == 201
    assert response.json()["photo"] is None


@pytest.mark.parametrize(
    "media_type, content",
    [
        ("image/jpeg", b"\xff\xd8\xffphoto"),
        ("image/png", b"\x89PNG\r\n\x1a\nphoto"),
        ("image/webp", b"RIFF\x00\x00\x00\x00WEBPphoto"),
        ("image/gif", b"GIF89aphoto"),
    ],
)
def test_photo_round_trips_through_create_list_and_get(client, payload, media_type, content):
    photo = photo_data_url(media_type, content)

    created = client.post(BASE, json={**payload, "photo": photo})
    assert created.status_code == 201
    contact_id = created.json()["id"]
    assert created.json()["photo"] == photo

    listed = client.get(BASE)
    assert listed.json()["items"][0]["photo"] == photo

    fetched = client.get(f"{BASE}/{contact_id}")
    assert fetched.json()["photo"] == photo


def test_patch_omitting_photo_preserves_it(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": PNG_PHOTO}).json()["id"]

    response = client.patch(f"{BASE}/{contact_id}", json={"job_title": "Countess"})

    assert response.status_code == 200
    assert response.json()["photo"] == PNG_PHOTO


def test_patch_can_replace_photo(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": PNG_PHOTO}).json()["id"]
    replacement = photo_data_url("image/gif", b"GIF89areplacement")

    replaced = client.patch(f"{BASE}/{contact_id}", json={"photo": replacement})
    assert replaced.status_code == 200
    assert replaced.json()["photo"] == replacement


def test_patch_can_clear_photo(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": PNG_PHOTO}).json()["id"]

    cleared = client.patch(f"{BASE}/{contact_id}", json={"photo": None})

    assert cleared.status_code == 200
    assert cleared.json()["photo"] is None


def test_put_omitting_photo_clears_it(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": PNG_PHOTO}).json()["id"]

    response = client.put(f"{BASE}/{contact_id}", json=payload)

    assert response.status_code == 200
    assert response.json()["photo"] is None


def test_put_resubmitting_photo_preserves_it(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": PNG_PHOTO}).json()["id"]

    response = client.put(f"{BASE}/{contact_id}", json={**payload, "photo": PNG_PHOTO})

    assert response.status_code == 200
    assert response.json()["photo"] == PNG_PHOTO


@pytest.mark.parametrize(
    "photo",
    [
        "https://example.com/photo.png",
        photo_data_url("image/svg+xml", b"<svg></svg>"),
        "data:image/png;base64,%%%",
        "data:image/png;base64,",
        photo_data_url("image/png", b"GIF89anot-a-png"),
    ],
)
def test_rejects_invalid_photo_data(client, payload, photo):
    response = client.post(BASE, json={**payload, "photo": photo})

    assert response.status_code == 422


def test_accepts_photo_at_exact_size_limit(client, payload):
    content = b"\xff\xd8\xff" + b"x" * (MAX_PHOTO_BYTES - 3)
    photo = photo_data_url("image/jpeg", content)

    response = client.post(
        BASE,
        json={**payload, "photo": photo},
    )

    assert response.status_code == 201
    assert response.json()["photo"] == photo


def test_rejects_photo_over_size_limit(client, payload):
    content = b"\xff\xd8\xff" + b"x" * (MAX_PHOTO_BYTES - 2)

    response = client.post(
        BASE,
        json={**payload, "photo": photo_data_url("image/jpeg", content)},
    )

    assert response.status_code == 422


def test_delete_contact(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    assert client.delete(f"{BASE}/{contact_id}").status_code == 204
    assert client.get(f"{BASE}/{contact_id}").status_code == 404
    assert client.delete(f"{BASE}/{contact_id}").status_code == 404


def test_root_lists_entrypoints(client):
    body = client.get("/").json()
    assert body["contacts"] == BASE
