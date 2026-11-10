"""The resume the matcher suggests is stored with each application, to compare with the one sent."""


def _tex(*technologies):
    bullets = "\n".join(rf"\item Built services with {name}." for name in technologies)
    return (
        "\\begin{document}\n\\section*{Experience}\n"
        "\\job{Software Engineer}{Acme}\n{Jan 2020 -- Present}\n"
        f"\\begin{{itemize}}\n{bullets}\n\\end{{itemize}}\n\\end{{document}}"
    )


def _upload(client, label, *technologies):
    response = client.post(
        "/api/resumes",
        files={"file": ("r.tex", _tex(*technologies).encode(), "text/x-tex")},
        data={"label": label},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _posting(client, url, raw_text="Requirements\n- Python\n- AWS\n"):
    response = client.post(
        "/api/postings",
        json={"company": "Acme", "title": "Backend Engineer", "url": url, "raw_text": raw_text},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _apply(client, posting, resume):
    response = client.post(
        "/api/applications",
        json={
            "company": "Acme",
            "role_title": "Backend Engineer",
            "posting_id": posting["id"],
            "resume_id": resume["id"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_a_resume_made_for_one_job_is_left_out_of_its_own_recommendation(client):
    backend = _upload(client, "Backend", "Python", "AWS")
    frontend = _upload(client, "Frontend", "React")
    application = _apply(client, _posting(client, "https://a.test/1"), frontend)

    assert application["resume_id"] == frontend["id"]
    assert application["recommended_resume_id"] == backend["id"]
    assert application["recommended_resume_label"] == "Backend"


def test_no_other_resume_means_no_recommendation(client):
    only = _upload(client, "Only", "Python", "AWS")
    application = _apply(client, _posting(client, "https://a.test/1"), only)

    assert application["resume_id"] == only["id"]
    assert application["recommended_resume_id"] is None
    assert application["recommended_resume_label"] is None


def test_a_resume_already_sent_elsewhere_stays_in_the_choice(client):
    backend = _upload(client, "Backend", "Python", "AWS")
    _upload(client, "Frontend", "React")
    _apply(client, _posting(client, "https://a.test/1"), backend)

    second = _apply(client, _posting(client, "https://a.test/2"), backend)

    assert second["recommended_resume_id"] == backend["id"]


def test_posting_trends_compare_the_suggested_resume_with_others(client):
    backend = _upload(client, "Backend", "Python", "AWS")
    _upload(client, "Frontend", "React")
    # The first send is the only use of Backend, so it is left out and Frontend is suggested.
    _apply(client, _posting(client, "https://a.test/1"), backend)
    _apply(client, _posting(client, "https://a.test/2"), backend)

    rows = client.get("/api/analytics/postings").json()["recommendation_callbacks"]

    assert [(row["source"], row["applications"], row["callbacks"]) for row in rows] == [
        ("Sent the suggested resume", 1, 0),
        ("Sent another", 1, 0),
    ]


def test_trends_skip_applications_with_no_suggestion(client):
    only = _upload(client, "Only", "Python", "AWS")
    _apply(client, _posting(client, "https://a.test/1"), only)

    assert client.get("/api/analytics/postings").json()["recommendation_callbacks"] == []


def test_the_suggestion_is_listed_with_each_application(client):
    backend = _upload(client, "Backend", "Python", "AWS")
    frontend = _upload(client, "Frontend", "React")
    created = _apply(client, _posting(client, "https://a.test/1"), frontend)

    listed = client.get("/api/applications").json()

    assert [item["id"] for item in listed] == [created["id"]]
    assert listed[0]["recommended_resume_id"] == backend["id"]
