"""按画像语言分流的确定性 API 清单。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.contexts.agent.api_inventory.fastapi_parser import (
    build_fastapi_bom,
    parse_fastapi_file,
)
from app.contexts.agent.api_inventory.models import prioritize_pve
from app.contexts.agent.api_inventory.registry import (
    build_inventory_bom,
    parser_keys_for_profile,
    phase_message_for_profile,
    unsupported_for_profile,
)
from app.contexts.agent.contracts.outputs import LanguageFact, ProfileHandoff
from app.contexts.agent.nodes.base import NodeContext


def _profile(*langs: str) -> ProfileHandoff:
    facts = [LanguageFact(id=lang) for lang in langs]
    primary = langs[0] if langs else None
    return ProfileHandoff(
        languages=facts,
        primary_language=primary,
        language=primary,
    )


def test_parse_fastapi_decorators():
    src = '''
from fastapi import FastAPI, APIRouter, Depends

app = FastAPI()
router = APIRouter()

@app.get("/health")
def health():
    return {"ok": True}

@router.get("/orders/{order_id}")
async def get_order(order_id: int):
    return {"id": order_id}

@router.delete("/admin/users/{user_id}")
def delete_user(user_id: str):
    return None
'''
    eps = parse_fastapi_file("app/main.py", src)
    ids = {e.endpoint_id for e in eps}
    assert "GET /health" in ids
    assert "GET /orders/{order_id}" in ids
    assert "DELETE /admin/users/{user_id}" in ids
    order = next(e for e in eps if e.path_template == "/orders/{order_id}")
    assert order.has_object_id
    assert order.is_pve
    assert "order_id" in order.id_params


def test_build_bom_and_prioritize(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "api.py").write_text(
        '''
from fastapi import FastAPI
app = FastAPI()

@app.get("/items/{item_id}")
def get_item(item_id: int):
    return item_id

@app.post("/items")
def create_item():
    return {}
''',
        encoding="utf-8",
    )
    bom = build_fastapi_bom(root)
    assert bom["parser"] == "fastapi"
    assert bom["endpoint_count"] == 2
    assert bom["pve_count"] >= 1
    top = prioritize_pve(bom["endpoints"], top_k=10)
    assert any(e["has_object_id"] for e in top)


@pytest.mark.parametrize(
    ("langs", "frameworks", "expect_keys", "unsupported"),
    [
        (("python",), (), ["openapi", "fastapi", "flask", "django"], []),
        (("python",), ("flask",), ["openapi", "flask"], []),
        (("nodejs",), (), ["openapi", "express", "nextjs", "nestjs"], []),
        (("php",), (), ["openapi", "php_script"], []),
        (("php",), ("laravel",), ["openapi", "laravel"], []),
        (("java",), (), ["openapi", "spring"], []),
        (("go",), (), ["openapi", "gin"], []),
        (("rust",), (), ["openapi"], ["rust"]),
        (("static",), (), ["openapi"], ["static"]),
        ((), (), ["openapi"], ["unknown"]),
    ],
)
def test_select_parsers_by_language(langs, frameworks, expect_keys, unsupported):
    profile = _profile(*langs)
    if frameworks:
        profile = profile.model_copy(update={
            "frameworks": list(frameworks),
            "framework": frameworks[0],
        })
    assert parser_keys_for_profile(profile) == expect_keys
    assert unsupported_for_profile(profile) == unsupported
    msg = phase_message_for_profile(profile)
    assert "FastAPI" not in msg
    assert "无 FastAPI" not in msg


def test_nodejs_does_not_run_fastapi(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "server.js").write_text(
        "const app = require('express')();\n"
        "app.get('/users/:id', (req, res) => res.json({id: req.params.id}));\n"
        "app.post('/users', (req, res) => res.json({}));\n",
        encoding="utf-8",
    )
    (root / "api.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.get('/secret-python')\n"
        "def leak():\n"
        "    return {}\n",
        encoding="utf-8",
    )
    bom = build_inventory_bom(root, _profile("nodejs"))
    assert "fastapi" not in bom["parsers"]
    ids = {e["endpoint_id"] for e in bom["endpoints"]}
    assert "GET /users/{id}" in ids
    assert "POST /users" in ids
    assert "GET /secret-python" not in ids
    assert "router" in bom["acquisition_kinds"]


def test_flask_and_django(tmp_path: Path):
    root = tmp_path / "repo"
    (root / "shop").mkdir(parents=True)
    (root / "app.py").write_text(
        "from flask import Flask\n"
        "app = Flask(__name__)\n"
        "@app.route('/cart/<int:item_id>', methods=['GET', 'POST'])\n"
        "def cart(item_id):\n"
        "    return item_id\n"
        "@app.get('/ping')\n"
        "def ping():\n"
        "    return 'ok'\n",
        encoding="utf-8",
    )
    (root / "shop" / "urls.py").write_text(
        "from django.urls import path, include\n"
        "urlpatterns = [\n"
        "    path('api/', include('shop.api')),\n"
        "    path('orders/<int:order_id>/', views.order),\n"
        "]\n",
        encoding="utf-8",
    )
    bom = build_inventory_bom(root, _profile("python"))
    ids = {e["endpoint_id"] for e in bom["endpoints"]}
    assert "GET /cart/{item_id}" in ids
    assert "POST /cart/{item_id}" in ids
    assert "GET /ping" in ids
    assert "GET /orders/{order_id}" in ids
    assert "GET /api" not in ids


def test_nextjs_pages_and_app_router(tmp_path: Path):
    root = tmp_path / "repo"
    pages = root / "pages" / "api"
    pages.mkdir(parents=True)
    (pages / "hello.js").write_text("export default function handler() {}", encoding="utf-8")
    route = root / "app" / "api" / "orders" / "[id]"
    route.mkdir(parents=True)
    (route / "route.ts").write_text(
        "export async function GET() { return Response.json({}) }\n"
        "export async function DELETE() { return Response.json({}) }\n",
        encoding="utf-8",
    )
    bom = build_inventory_bom(root, _profile("nodejs"))
    ids = {e["endpoint_id"] for e in bom["endpoints"]}
    assert "GET /api/hello" in ids
    assert "POST /api/hello" in ids
    assert "GET /api/orders/{id}" in ids
    assert "DELETE /api/orders/{id}" in ids
    kinds = set(bom["acquisition_kinds"])
    assert "script_file" in kinds
    assert "export_handler" in kinds


def test_nestjs_controller(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "users.controller.ts").write_text(
        "@Controller('users')\n"
        "export class UsersController {\n"
        "  @Get(':id')\n"
        "  findOne() {}\n"
        "  @Post()\n"
        "  create() {}\n"
        "}\n",
        encoding="utf-8",
    )
    bom = build_inventory_bom(root, _profile("nodejs"))
    ids = {e["endpoint_id"] for e in bom["endpoints"]}
    assert "GET /users/{id}" in ids
    assert "POST /users" in ids


def test_php_script_not_framework_classes(tmp_path: Path):
    root = tmp_path / "repo"
    public = root / "public"
    public.mkdir(parents=True)
    (public / "user.php").write_text("<?php echo 1;", encoding="utf-8")
    ctrl = root / "app" / "Http" / "Controllers"
    ctrl.mkdir(parents=True)
    (ctrl / "UserController.php").write_text("<?php class UserController {}", encoding="utf-8")
    bom = build_inventory_bom(root, _profile("php"))
    files = {e["handler_file"] for e in bom["endpoints"]}
    assert "public/user.php" in files
    assert not any("UserController.php" in f for f in files)
    ids = {e["endpoint_id"] for e in bom["endpoints"]}
    assert "GET /user" in ids
    assert "POST /user" in ids
    assert "script_file" in bom["acquisition_kinds"]


def test_laravel_routes(tmp_path: Path):
    root = tmp_path / "repo"
    routes = root / "routes"
    routes.mkdir(parents=True)
    ctrl = root / "app" / "Http" / "Controllers"
    ctrl.mkdir(parents=True)
    (ctrl / "OrderController.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Controllers;\n"
        "class OrderController {\n"
        "  public function show() { $id = $request->input('user_id'); }\n"
        "  public function store() {}\n"
        "}\n",
        encoding="utf-8",
    )
    (routes / "web.php").write_text(
        "<?php\n"
        "Route::get('/orders/{id}', [OrderController::class, 'show']);\n"
        "Route::post('/orders', [OrderController::class, 'store']);\n"
        "Route::get('/legacy/{id}', 'LegacyController@index');\n"
        "Route::get('/closure', function () { return 1; });\n",
        encoding="utf-8",
    )
    (ctrl / "LegacyController.php").write_text(
        "<?php\nclass LegacyController { public function index() {}\n}\n",
        encoding="utf-8",
    )
    profile = _profile("php").model_copy(update={
        "frameworks": ["laravel"],
        "framework": "laravel",
    })
    bom = build_inventory_bom(root, profile)
    ids = {e["endpoint_id"] for e in bom["endpoints"]}
    assert "GET /orders/{id}" in ids
    assert "POST /orders" in ids
    assert "GET /legacy/{id}" in ids
    assert "GET /closure" in ids
    assert "router" in bom["acquisition_kinds"]

    show = next(e for e in bom["endpoints"] if e["endpoint_id"] == "GET /orders/{id}")
    assert show["route_file"] == "routes/web.php"
    assert show["handler_file"] == "app/Http/Controllers/OrderController.php"
    assert show["handler_symbol"] == "show"
    assert "user_id" in show["id_params"]
    assert show["is_pve"] is True

    legacy = next(e for e in bom["endpoints"] if e["endpoint_id"] == "GET /legacy/{id}")
    assert legacy["handler_file"] == "app/Http/Controllers/LegacyController.php"
    assert legacy["handler_symbol"] == "index"
    assert legacy["route_file"] == "routes/web.php"

    closure = next(e for e in bom["endpoints"] if e["endpoint_id"] == "GET /closure")
    assert closure["handler_file"] == "routes/web.php"
    assert closure["route_file"] == "routes/web.php"
    assert closure["handler_symbol"] is None


def test_spring_and_gin(tmp_path: Path):
    root = tmp_path / "repo"
    java = root / "src" / "UserController.java"
    java.parent.mkdir(parents=True)
    java.write_text(
        "import org.springframework.web.bind.annotation.*;\n"
        "@RestController\n"
        "@RequestMapping(\"/api\")\n"
        "public class UserController {\n"
        "  @GetMapping(\"/users/{id}\")\n"
        "  public User get(@PathVariable String id) { return null; }\n"
        "  @PostMapping(\"/users\")\n"
        "  public User create() { return null; }\n"
        "}\n",
        encoding="utf-8",
    )
    go = root / "main.go"
    go.write_text(
        'package main\n'
        'func main() {\n'
        '  r := gin.Default()\n'
        '  r.GET("/items/:id", getItem)\n'
        '  r.POST("/items", createItem)\n'
        '}\n',
        encoding="utf-8",
    )
    java_bom = build_inventory_bom(root, _profile("java"))
    java_ids = {e["endpoint_id"] for e in java_bom["endpoints"]}
    assert "GET /api/users/{id}" in java_ids
    assert "POST /api/users" in java_ids
    assert "gin" not in java_bom["parsers"]

    go_bom = build_inventory_bom(root, _profile("go"))
    go_ids = {e["endpoint_id"] for e in go_bom["endpoints"]}
    assert "GET /items/{id}" in go_ids
    assert "POST /items" in go_ids
    assert "spring" not in go_bom["parsers"]


def test_openapi_and_zero_is_not_unsupported(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "openapi.json").write_text(
        '{"openapi":"3.0.0","paths":{"/pets/{petId}":{"get":{"operationId":"getPet"}}}}',
        encoding="utf-8",
    )
    rust_empty = tmp_path / "empty"
    rust_empty.mkdir()
    empty_bom = build_inventory_bom(rust_empty, _profile("rust"))
    assert empty_bom["endpoint_count"] == 0
    assert empty_bom["parsers"] == ["openapi"]
    assert unsupported_for_profile(_profile("rust")) == ["rust"]

    bom = build_inventory_bom(root, _profile("rust"))
    ids = {e["endpoint_id"] for e in bom["endpoints"]}
    assert "GET /pets/{petId}" in ids
    assert "openapi" in bom["acquisition_kinds"]
    assert unsupported_for_profile(_profile("rust")) == ["rust"]


def _ctx(tmp_path: Path, on_event) -> NodeContext:
    return NodeContext(
        task_id="t1",
        run_id="r1",
        host_workdir=str(tmp_path),
        source_path=str(tmp_path / "repo"),
        vulnerability_description="",
        project_address="x",
        project_ref="main",
        on_event=on_event,
    )


@pytest.mark.asyncio
async def test_node_nodejs_copy_is_not_fastapi(tmp_path: Path):
    from app.contexts.agent.contracts import ApiInventoryInput, SourceHandoff
    from app.contexts.agent.nodes.api_inventory import ApiInventoryNode

    root = tmp_path / "repo"
    root.mkdir()
    (root / "index.js").write_text(
        "const app = require('express')();\napp.get('/health', (req, res) => res.end('ok'));\n",
        encoding="utf-8",
    )
    events: list[dict] = []
    inp = ApiInventoryInput(
        source=SourceHandoff(project_path=str(root), source_path=str(root)),
        host_workdir=str(tmp_path),
        source_path=str(root),
        profile=_profile("nodejs"),
    )
    out = await ApiInventoryNode().execute(_ctx(tmp_path, events.append), inp)
    joined = " ".join(str(e.get("message") or "") for e in events)
    assert "FastAPI" not in joined
    assert "无 FastAPI 解析目标" not in joined
    assert "nodejs" in joined
    assert "express" in out["parsers"]
    assert "fastapi" not in out["parsers"]
    assert out["endpoint_count"] >= 1
    assert out["unsupported_languages"] == []
    assert out["ok"] is True
