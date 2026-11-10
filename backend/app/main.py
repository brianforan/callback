import logging

from fastapi import FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.deps import Db
from app.logs import configure_logging
from app.routers import (
    admin,
    analytics,
    applications,
    auth,
    communications,
    company_insights,
    company_research,
    fact_sheet,
    intake,
    keywords,
    listings,
    postings,
    profiles,
    reject_analysis,
    resumes,
    scout,
    skills,
    spend,
    tailor,
)

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Callback API", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(auth.router)
app.include_router(profiles.router)
app.include_router(resumes.router)
app.include_router(applications.router)
app.include_router(intake.router)
app.include_router(postings.router)
app.include_router(listings.router)
app.include_router(keywords.router)
app.include_router(analytics.router)
app.include_router(fact_sheet.router)
app.include_router(skills.router)
app.include_router(tailor.router)
app.include_router(reject_analysis.router)
app.include_router(company_research.router)
app.include_router(company_insights.router)
app.include_router(scout.router)
app.include_router(communications.router)
app.include_router(admin.router)
app.include_router(spend.router)


@app.get("/api/health")
def health(db: Db) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.exception("Health check could not reach the database")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Database unavailable") from exc
    return {"status": "ok"}
