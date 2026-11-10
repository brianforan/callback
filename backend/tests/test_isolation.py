"""One user can never read, change, attach or delete another user's data.

Each route that takes an id is tried by a second user against the first user's rows, and each
must answer as if the row didn't exist. Afterwards the first user's rows are unchanged.
"""

import pytest

TEX = rb"\begin{document}Python Kafka platform engineer\end{document}"


@pytest.fixture
def owners(client, session_factory):
    """The first user's resume, application, posting, profile and next step."""
    resume = client.post("/api/resumes", files={"file": ("r.tex", TEX, "text/x-tex")}).json()
    application = client.post(
        "/api/applications",
        json={
            "company": "Acme",
            "role_title": "Engineer",
            "job_url": "https://acme.io/jobs/1",
            "resume_id": resume["id"],
        },
    ).json()
    client.post(
        f"/api/applications/{application['id']}/communications",
        json={
            "kind": "email",
            "occurred_on": "2026-11-01",
            "next_step": "Send the portfolio",
            "next_step_due": "2026-11-10",
        },
    )
    profile = client.get("/api/profiles").json()["active_id"]
    return {
        "resume": resume["id"],
        "application": application["id"],
        "posting": application["posting_id"],
        "profile": profile,
    }


def _theirs(other_client):
    """The second user's own application, to try attaching the first user's rows to."""
    return other_client.post(
        "/api/applications", json={"company": "Initech", "role_title": "Engineer"}
    ).json()


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("patch", "/api/applications/{application}", {"status": "offer"}),
        ("patch", "/api/profiles/{profile}", {"name": "Taken over"}),
        ("put", "/api/resumes/{resume}/variant", {"is_variant": True}),
        ("get", "/api/resumes/{resume}/pdf", None),
        ("delete", "/api/resumes/{resume}", None),
        ("get", "/api/postings/{posting}", None),
        ("patch", "/api/postings/{posting}", {"company": "Taken over"}),
        ("delete", "/api/postings/{posting}", None),
        ("get", "/api/applications/{application}/communications", None),
    ],
)
def test_another_user_s_rows_are_not_found(other_client, owners, method, path, body):
    url = path.format(**owners)
    kwargs = {"json": body} if body is not None else {}
    response = getattr(other_client, method)(url, **kwargs)
    assert response.status_code == 404, (method, url, response.text)


def test_another_user_s_resume_or_posting_cannot_be_attached(other_client, owners):
    mine = _theirs(other_client)
    url = f"/api/applications/{mine['id']}"
    assert other_client.patch(url, json={"resume_id": owners["resume"]}).status_code in (404, 422)
    assert other_client.patch(url, json={"posting_id": owners["posting"]}).status_code in (404, 422)
    unapplied = other_client.post(
        "/api/postings", json={"company": "Globex", "title": "Engineer"}
    ).json()
    stage = other_client.post(
        f"/api/postings/{unapplied['id']}/stage", json={"resume_id": owners["resume"]}
    )
    assert stage.status_code == 422


def test_another_user_s_resume_cannot_be_replaced_as_a_baseline(other_client, owners):
    response = other_client.post(
        "/api/resumes/baselines",
        files={"file": ("b.tex", TEX, "text/x-tex")},
        data={"replaces": owners["resume"]},
    )
    assert response.status_code in (404, 422)


def test_next_steps_list_only_your_own(other_client, owners):
    assert other_client.get("/api/communications/next-steps").json() == []


def test_the_owner_s_rows_are_unchanged_afterwards(client, other_client, owners):
    for method, path, body in (
        ("patch", f"/api/applications/{owners['application']}", {"status": "offer"}),
        ("patch", f"/api/profiles/{owners['profile']}", {"name": "Taken over"}),
        ("delete", f"/api/resumes/{owners['resume']}", None),
        ("delete", f"/api/postings/{owners['posting']}", None),
    ):
        kwargs = {"json": body} if body is not None else {}
        getattr(other_client, method)(path, **kwargs)

    application = client.get(f"/api/applications/{owners['application']}").json()
    assert application["status"] == "applied"
    assert application["resume_id"] == owners["resume"]
    assert client.get(f"/api/postings/{owners['posting']}").status_code == 200
    names = [profile["name"] for profile in client.get("/api/profiles").json()["profiles"]]
    assert names == ["Default"]
    steps = client.get("/api/communications/next-steps").json()
    assert [step["next_step"] for step in steps] == ["Send the portfolio"]
