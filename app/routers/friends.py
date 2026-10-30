"""Friends page and read-only views of a friend's applications."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response
from sqlalchemy import select

from app.deps import CSRFProtected, CurrentUser, DbSession
from app.i18n import t
from app.models import Application
from app.routers.applications import list_context, parse_filters, render_results
from app.services import applications as svc
from app.services import duo as duo_svc
from app.services import friends as friends_svc
from app.services.applications import NotFoundError
from app.web import flash, is_htmx, redirect, render

router = APIRouter(tags=["friends"])


@router.get("/friends")
def friends_page(request: Request, user: CurrentUser, db: DbSession) -> Response:
    return render(
        request,
        "friends.html",
        {
            "user": user,
            "friends": friends_svc.friends(db, user.id),
            "incoming": friends_svc.incoming_requests(db, user.id),
            "outgoing": friends_svc.outgoing_requests(db, user.id),
        },
    )


@router.post("/friends/request", dependencies=[CSRFProtected])
def send_request(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    email: Annotated[str, Form(max_length=254)] = "",
) -> Response:
    outcome = friends_svc.send_request(db, user, email)
    email = email.strip().lower()
    messages = {
        friends_svc.RequestOutcome.SENT: (t("friends_request_sent", email=email), "success"),
        friends_svc.RequestOutcome.ACCEPTED: (t("friends_accepted", email=email), "success"),
        friends_svc.RequestOutcome.SELF: (t("friends_self"), "warning"),
        friends_svc.RequestOutcome.ALREADY: (t("friends_already"), "info"),
    }
    flash(request, *messages[outcome])
    return redirect(request, "/friends")


@router.post("/friends/{friendship_id:int}/accept", dependencies=[CSRFProtected])
def accept(request: Request, friendship_id: int, user: CurrentUser, db: DbSession) -> Response:
    friend = friends_svc.accept(db, user.id, friendship_id)
    flash(request, t("friends_accepted", email=friend.email), "success")
    return redirect(request, "/friends")


@router.post("/friends/{friendship_id:int}/remove", dependencies=[CSRFProtected])
def remove(request: Request, friendship_id: int, user: CurrentUser, db: DbSession) -> Response:
    friends_svc.remove(db, user.id, friendship_id)
    flash(request, t("friends_removed"), "success")
    return redirect(request, "/friends")


@router.post("/duo/invite", dependencies=[CSRFProtected])
def invite_to_duo(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    friend_id: Annotated[int, Form()],
) -> Response:
    outcome = duo_svc.send_invite(db, user.id, friend_id)
    messages = {
        duo_svc.InviteOutcome.SENT: ("duo_invite_sent", "success"),
        duo_svc.InviteOutcome.ALREADY_EXISTS: ("duo_invite_exists", "info"),
        duo_svc.InviteOutcome.SENDER_BUSY: ("duo_already_in_battle", "warning"),
        duo_svc.InviteOutcome.FRIEND_BUSY: ("duo_friend_busy", "warning"),
    }
    key, kind = messages[outcome]
    flash(request, t(key), kind)
    return redirect(request, "/")


@router.post("/duo/{battle_id:int}/accept", dependencies=[CSRFProtected])
def accept_duo(request: Request, battle_id: int, user: CurrentUser, db: DbSession) -> Response:
    duo_svc.accept_invite(db, user.id, battle_id)
    flash(request, t("duo_accepted"), "success")
    return redirect(request, "/")


@router.post("/duo/{battle_id:int}/leave", dependencies=[CSRFProtected])
def leave_duo(request: Request, battle_id: int, user: CurrentUser, db: DbSession) -> Response:
    duo_svc.leave_battle(db, user.id, battle_id)
    flash(request, t("duo_left"), "info")
    return redirect(request, "/")


@router.get("/friends/{friend_id:int}")
def friend_tracker(request: Request, friend_id: int, user: CurrentUser, db: DbSession) -> Response:
    friend = friends_svc.get_friend(db, user.id, friend_id)
    params = request.query_params
    view = "board" if params.get("view") == "board" else "list"
    context = {
        "user": user,
        "friend": friend,
        "friend_name": friends_svc.display_name(friend),
        **list_context(db, friend, parse_filters(params), view, viewer=user),
    }
    if is_htmx(request) and request.headers.get("hx-target") == "results":
        return render_results(request, context)
    return render(request, "friend.html", context)


@router.get("/friends/{friend_id:int}/applications/{application_id:int}")
def friend_application(
    request: Request, friend_id: int, application_id: int, user: CurrentUser, db: DbSession
) -> Response:
    friend = friends_svc.get_friend(db, user.id, friend_id)
    app = db.scalar(
        select(Application).where(
            Application.id == application_id, Application.user_id == friend.id
        )
    )
    if app is None:
        raise NotFoundError
    return render(
        request,
        "friend_application.html",
        {
            "user": user,
            "friend": friend,
            "friend_name": friends_svc.display_name(friend),
            "app": app,
            "events": svc.list_events(db, app.id),
        },
    )
