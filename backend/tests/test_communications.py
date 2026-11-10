from datetime import date, timedelta


def _application(client, company="Acme", applied_on=None) -> str:
    response = client.post(
        "/api/applications",
        json={
            "company": company,
            "role_title": "Engineer",
            "status": "applied",
            "applied_on": (applied_on or date.today()).isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _log(client, application_id, **changes):
    body = {"kind": "email", "direction": "inbound", "occurred_on": date.today().isoformat()}
    body.update(changes)
    return client.post(f"/api/applications/{application_id}/communications", json=body)


def test_log_list_edit_and_delete(client, other_client):
    app_id = _application(client)
    first = _log(client, app_id, occurred_on="2026-11-01", contact="Dana, recruiter")
    second = _log(
        client,
        app_id,
        kind="call",
        occurred_on="2026-11-03",
        summary="  Phone screen booked  ",
        next_step="Prep for the screen",
        next_step_due="2026-11-05",
    )
    assert first.status_code == second.status_code == 201
    listed = client.get(f"/api/applications/{app_id}/communications").json()
    assert [item["kind"] for item in listed] == ["call", "email"]
    assert listed[0]["summary"] == "Phone screen booked"

    assert other_client.get(f"/api/applications/{app_id}/communications").status_code == 404
    assert other_client.patch(f"/api/communications/{listed[0]['id']}", json={}).status_code == 404

    done = client.patch(f"/api/communications/{listed[0]['id']}", json={"next_step_done": True})
    assert done.json()["next_step_done"] is True
    assert client.delete(f"/api/communications/{listed[1]['id']}").status_code == 204
    assert len(client.get(f"/api/applications/{app_id}/communications").json()) == 1


def test_notes_have_no_direction_and_dates_need_a_step(client):
    app_id = _application(client)
    note = _log(client, app_id, kind="note", direction="outbound").json()
    assert note["direction"] == "none"
    assert _log(client, app_id, next_step_due="2026-11-05").status_code == 422
    cleared = client.patch(
        f"/api/communications/{note['id']}", json={"next_step": "Email them", "next_step_due": None}
    )
    assert cleared.status_code == 200
    assert (
        client.patch(
            f"/api/communications/{note['id']}",
            json={"next_step": "", "next_step_due": "2026-11-05"},
        ).status_code
        == 422
    )


def test_next_steps_sort_overdue_first_and_skip_done(client):
    acme = _application(client, "Acme")
    beta = _application(client, "Beta")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    later = (date.today() + timedelta(days=3)).isoformat()
    _log(client, acme, next_step="Send portfolio", next_step_due=later)
    _log(client, beta, next_step="Reply to recruiter", next_step_due=yesterday)
    _log(client, acme, next_step="Thank-you note")
    done = _log(client, beta, next_step="Old step").json()
    client.patch(f"/api/communications/{done['id']}", json={"next_step_done": True})

    steps = client.get("/api/communications/next-steps").json()
    assert [(s["company"], s["next_step"], s["overdue"]) for s in steps] == [
        ("Beta", "Reply to recruiter", True),
        ("Acme", "Send portfolio", False),
        ("Acme", "Thank-you note", False),
    ]


def test_logged_contact_restarts_the_follow_up_clock(client):
    old = date.today() - timedelta(days=30)
    app_id = _application(client, applied_on=old)
    stale = client.get("/api/analytics/overview").json()["stale"]
    assert [item["days_waiting"] for item in stale] == [30]

    _log(client, app_id, direction="outbound", occurred_on=date.today().isoformat())
    assert client.get("/api/analytics/overview").json()["stale"] == []

    _log(client, app_id, occurred_on=(date.today() - timedelta(days=20)).isoformat())
    # The newest contact counts, not the last one logged.
    assert client.get("/api/analytics/overview").json()["stale"] == []


def test_deleting_an_application_deletes_its_communications(client):
    app_id = _application(client)
    _log(client, app_id, next_step="Follow up")
    assert client.delete(f"/api/applications/{app_id}").status_code == 204
    assert client.get("/api/communications/next-steps").json() == []


def test_an_application_from_elsewhere_isnt_badged(client):
    _application(client)
    [application] = client.get("/api/applications").json()
    assert application["from_scout"] is False
