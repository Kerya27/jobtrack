"""Statistics page."""

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.deps import CurrentUser, DbSession
from app.services.stats import load_stats
from app.timeutil import today_local
from app.web import render

router = APIRouter(tags=["pages"])


@router.get("/stats")
def stats_page(request: Request, user: CurrentUser, db: DbSession) -> Response:
    return render(
        request, "stats.html", {"user": user, "stats": load_stats(db, user.id, today_local())}
    )
