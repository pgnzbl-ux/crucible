import flask
from fastapi import FastAPI, Query
from sqlalchemy import asc, text

app = flask.Flask(__name__)
api = FastAPI()


@app.route("/sort")
def sort_flask(query=None):
    col = flask.request.args.get("sort")
    # ruleid: sqlalchemy-order-by-column
    query.order_by(col)


@app.route("/sort-text")
def sort_text(query=None):
    col = flask.request.args["sort"]
    # ruleid: sqlalchemy-order-by-column
    query.order_by(text(col))


def django_sort(request, query=None):
    col = request.GET.get("o")
    # ruleid: sqlalchemy-order-by-column
    query.order_by(col)


@api.get("/items")
async def sort_fastapi(sort: str = Query(...), query=None):
    # ruleid: sqlalchemy-order-by-column
    query.order_by(text(sort))


@app.route("/safe-asc")
def safe_asc(query=None, User=None):
    # ok: sqlalchemy-order-by-column
    query.order_by(asc(User.id))


@app.route("/safe-whitelist")
def safe_whitelist(query=None, User=None):
    allowed = {"id": User.id, "name": User.name}
    key = flask.request.args.get("sort")
    col = allowed.get(key, User.id)
    # ok: sqlalchemy-order-by-column
    query.order_by(col)
