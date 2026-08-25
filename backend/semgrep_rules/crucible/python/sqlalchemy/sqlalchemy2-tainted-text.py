import flask
from fastapi import Body, FastAPI, Path, Query, Request
from sqlalchemy import text

app = flask.Flask(__name__)
api = FastAPI()


@app.route("/flask-text")
def flask_text(session=None):
    q = flask.request.args.get("q")
    # ruleid: sqlalchemy2-tainted-text
    session.execute(text(f"SELECT * FROM users WHERE name = '{q}'"))


@app.route("/flask-driver")
def flask_driver(conn=None):
    q = flask.request.args["q"]
    # ruleid: sqlalchemy2-tainted-text
    conn.exec_driver_sql(f"SELECT * FROM t WHERE x = '{q}'")


@app.route("/flask-scalars")
def flask_scalars(session=None):
    q = flask.request.args.get("q")
    # ruleid: sqlalchemy2-tainted-text
    session.scalars(text("SELECT * FROM t WHERE q = '%s'" % q))


def django_view(request, session=None):
    name = request.GET.get("name")
    # ruleid: sqlalchemy2-tainted-text
    session.scalar(text(f"SELECT id FROM users WHERE name = '{name}'"))


def django_post(request, session=None):
    body = request.POST["term"]
    # ruleid: sqlalchemy2-tainted-text
    session.execute(text("SELECT * FROM t WHERE x = '" + body + "'"))


@api.get("/users")
async def list_users(q: str = Query(...), session=None):
    # ruleid: sqlalchemy2-tainted-text
    session.execute(text(f"SELECT * FROM users WHERE name = '{q}'"))


@api.get("/items/{item_id}")
async def get_item(item_id: str = Path(...), session=None):
    # ruleid: sqlalchemy2-tainted-text
    session.scalars(text("SELECT * FROM items WHERE id = " + item_id))


@api.post("/search")
async def search(body: dict = Body(...), session=None):
    term = body["term"]
    # ruleid: sqlalchemy2-tainted-text
    session.execute(text(f"SELECT * FROM t WHERE x = '{term}'"))


@api.get("/raw")
async def raw(request: Request, session=None):
    q = request.query_params.get("q")
    # ruleid: sqlalchemy2-tainted-text
    session.scalar_one(text(f"SELECT * FROM t WHERE q = '{q}'"))


@app.route("/safe-bound")
def safe_bound(session=None):
    q = flask.request.args.get("q")
    # ok: sqlalchemy2-tainted-text
    session.execute(text("SELECT * FROM users WHERE name = :q"), {"q": q})


@app.route("/safe-int")
def safe_int(session=None):
    q = flask.request.args.get("q")
    n = int(q)
    # ok: sqlalchemy2-tainted-text
    session.execute(text(f"SELECT * FROM users WHERE id = {n}"))
