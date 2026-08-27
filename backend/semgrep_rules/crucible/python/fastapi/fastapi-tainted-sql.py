from fastapi import Body, FastAPI, Path, Query, Request

app = FastAPI()


@app.get("/users")
async def list_users(q: str = Query(...), db=None):
    # ruleid: fastapi-tainted-sql
    db.execute(f"SELECT * FROM users WHERE name = '{q}'")


@app.get("/items/{item_id}")
async def get_item(item_id: str = Path(...), conn=None):
    # ruleid: fastapi-tainted-sql
    conn.execute("SELECT * FROM items WHERE id = " + item_id)


@app.post("/search")
async def search(body: dict = Body(...), session=None):
    term = body["term"]
    # ruleid: fastapi-tainted-sql
    session.execute(f"SELECT * FROM t WHERE x = '{term}'")


@app.get("/raw")
async def raw(request: Request, cur=None):
    q = request.query_params.get("q")
    # ruleid: fastapi-tainted-sql
    cur.execute(f"SELECT * FROM t WHERE q = '{q}'")


@app.get("/safe")
async def safe(q: str = Query(...), db=None):
    # ok: fastapi-tainted-sql
    db.execute("SELECT * FROM users WHERE name = ?", (q,))


@app.get("/safe-int")
async def safe_int(q: str = Query(...), db=None):
    n = int(q)
    # ok: fastapi-tainted-sql
    db.execute(f"SELECT * FROM users WHERE id = {n}")
