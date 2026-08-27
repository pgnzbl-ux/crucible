import flask
import httpx
import aiohttp
from fastapi import FastAPI, Query

app = flask.Flask(__name__)
api = FastAPI()


@app.route("/httpx-get")
def httpx_get():
    url = flask.request.args.get("url")
    # ruleid: httpx-aiohttp-ssrf
    httpx.get(url)


@app.route("/httpx-post")
def httpx_post():
    url = flask.request.args["url"]
    # ruleid: httpx-aiohttp-ssrf
    httpx.post(url, json={})


@app.route("/httpx-request")
def httpx_request():
    url = flask.request.args.get("url")
    # ruleid: httpx-aiohttp-ssrf
    httpx.request("GET", url)


@app.route("/httpx-client")
def httpx_client():
    url = flask.request.args.get("url")
    client = httpx.Client()
    # ruleid: httpx-aiohttp-ssrf
    client.get(url)


def django_httpx(request):
    url = request.GET.get("url")
    # ruleid: httpx-aiohttp-ssrf
    httpx.get(url)


@api.get("/fetch")
async def fastapi_httpx(url: str = Query(...)):
    # ruleid: httpx-aiohttp-ssrf
    httpx.get(url)


async def aiohttp_get(request):
    url = request.GET.get("url")
    session = aiohttp.ClientSession()
    # ruleid: httpx-aiohttp-ssrf
    await session.get(url)


async def aiohttp_post_ctx():
    url = flask.request.args.get("url")
    async with aiohttp.ClientSession() as session:
        # ruleid: httpx-aiohttp-ssrf
        await session.post(url)


@app.route("/safe-literal")
def safe_literal():
    # ok: httpx-aiohttp-ssrf
    httpx.get("https://example.com/health")


@app.route("/safe-allowlist")
def safe_allowlist():
    allowed = {"a": "https://example.com/a", "b": "https://example.com/b"}
    key = flask.request.args.get("target")
    url = allowed.get(key, "https://example.com/")
    # ok: httpx-aiohttp-ssrf
    httpx.get(url)


# Bare client.get without httpx/aiohttp import guard must NOT match this rule.
# (This file imports httpx above; negative coverage lives in rule design —
#  no `$C.get($URL)` sink without import + ClientSession/Client binding.)
