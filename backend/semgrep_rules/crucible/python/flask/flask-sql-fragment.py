import flask

app = flask.Flask(__name__)


@app.route("/where-frag")
def where_frag():
    q = flask.request.args.get("q")
    # ruleid: flask-sql-fragment
    return " where " + q


@app.route("/order-frag")
def order_frag():
    col = flask.request.args.get("o")
    # ruleid: flask-sql-fragment
    return " order by " + col


@app.route("/limit-frag")
def limit_frag():
    n = flask.request.args["n"]
    # ruleid: flask-sql-fragment
    return " LIMIT " + n


@app.route("/fstring-where")
def fstring_where():
    q = flask.request.args.get("q")
    # ruleid: flask-sql-fragment
    return f" where {q}"


@app.route("/execute-frag")
def execute_frag(cur=None):
    q = flask.request.args.get("q")
    # ruleid: flask-sql-fragment
    cur.execute("id = %s AND active = 1" % q)


@app.route("/execute-concat")
def execute_concat(cur=None):
    q = flask.request.form.get("q")
    # ruleid: flask-sql-fragment
    cur.execute("name = '" + q + "'")


@app.route("/safe-bound")
def safe_bound(cur=None):
    q = flask.request.args.get("q")
    # ok: flask-sql-fragment
    cur.execute("SELECT * FROM t WHERE name = ?", (q,))


@app.route("/safe-int-limit")
def safe_int_limit():
    n = int(flask.request.args.get("n"))
    # ok: flask-sql-fragment
    return " LIMIT " + str(n)
