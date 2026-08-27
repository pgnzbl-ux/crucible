"""PHP tree-sitter 请求传参面 + script_file PVE / 兜底。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.contexts.agent.api_inventory.models import prioritize_pve
from app.contexts.agent.api_inventory.php_request_surface import extract_php_id_params
from app.contexts.agent.api_inventory.registry import build_inventory_bom
from app.contexts.agent.contracts.outputs import LanguageFact, ProfileHandoff


def _php_profile() -> ProfileHandoff:
    return ProfileHandoff(
        languages=[LanguageFact(id="php")],
        primary_language="php",
        language="php",
    )


@pytest.mark.parametrize(
    ("src", "expect"),
    [
        ("<?php $x = $_GET['user_id'];", ["user_id"]),
        ("<?php $x = $_POST['order_id'];", ["order_id"]),
        ("<?php $x = filter_input(INPUT_GET, 'user_id');", ["user_id"]),
        ("<?php $x = request('id');", ["id"]),
        ("<?php $x = $request->input('order_id');", ["order_id"]),
        ("<?php $x = Input::get('id');", ["id"]),
        ("<?php $x = $request->query->get('id');", ["id"]),
        ("<?php $x = $request->request->get('user_id');", ["user_id"]),
        ("<?php $x = input('get.user_id');", ["user_id"]),
        ("<?php $x = input('post.id');", ["id"]),
        ("<?php $x = Request::param('order_id');", ["order_id"]),
        ("<?php $x = $request->param('id');", ["id"]),
        ("<?php $x = $this->input->get('id');", ["id"]),
        ("<?php $x = $this->input->get_post('user_id');", ["user_id"]),
        ("<?php $x = $this->request->getGet('user_id');", ["user_id"]),
        ("<?php $x = $this->request->getPost('order_id');", ["order_id"]),
        ("<?php $x = Yii::$app->request->get('user_id');", ["user_id"]),
        ("<?php $x = Yii::app()->request->getParam('id');", ["id"]),
        ("<?php $x = $this->request->getQuery('id');", ["id"]),
        ("<?php $x = $this->request->getData('order_id');", ["order_id"]),
        ("<?php $x = $this->request->get('id');", ["id"]),
        ("<?php $x = $request->getQuery('id');", ["id"]),
        ("<?php $x = $request->fromGet('id');", ["id"]),
        ("<?php $x = Input::post('user_id');", ["user_id"]),
        ("<?php $x = get_query_var('id');", ["id"]),
        ("<?php $x = $request->getParam('user_id');", ["user_id"]),
        ("<?php $x = $_GET[$k];", []),
        ("<?php $x = $request->input($name);", []),
        ("<?php $x = $request->getQueryParams();", []),
        ("<?php $x = $_GET['username'];", []),
    ],
)
def test_extract_php_id_params_framework_shapes(src: str, expect: list[str]):
    assert extract_php_id_params(src) == expect


def test_php_script_marks_pve_from_request_surface(tmp_path: Path):
    root = tmp_path / "repo"
    public = root / "public"
    public.mkdir(parents=True)
    (public / "order.php").write_text(
        "<?php\n$id = $_GET['order_id'];\necho $id;\n",
        encoding="utf-8",
    )
    (public / "ping.php").write_text("<?php echo 'ok';", encoding="utf-8")
    bom = build_inventory_bom(root, _php_profile())
    by_path = {e["path_template"]: e for e in bom["endpoints"] if e["method"] == "GET"}
    assert by_path["/order"]["is_pve"] is True
    assert "order_id" in by_path["/order"]["id_params"]
    assert by_path["/ping"]["is_pve"] is False
    assert bom["pve_count"] >= 1


def test_php_script_laravel_style_input(tmp_path: Path):
    """script_file 仅原生传参面：Laravel $request->input 不计入。"""
    root = tmp_path / "repo"
    public = root / "public"
    public.mkdir(parents=True)
    (public / "user.php").write_text(
        "<?php\n$uid = $request->input('user_id');\n",
        encoding="utf-8",
    )
    bom = build_inventory_bom(root, _php_profile())
    hit = next(e for e in bom["endpoints"] if e["path_template"] == "/user" and e["method"] == "GET")
    assert "user_id" not in hit["id_params"]
    assert hit["is_pve"] is False


def test_extract_php_id_params_native_only_flag():
    src = "<?php $a = $_GET['user_id']; $b = $request->input('order_id');"
    assert extract_php_id_params(src, enabled_frameworks=set()) == ["user_id"]
    assert extract_php_id_params(src, enabled_frameworks={"laravel"}) == ["user_id", "order_id"]


def test_prioritize_falls_back_to_script_file_when_no_pve():
    endpoints = [
        {
            "endpoint_id": "GET /a",
            "method": "GET",
            "path_template": "/a",
            "is_pve": False,
            "has_object_id": False,
            "acquisition": "script_file",
            "auth_observed": [],
        },
        {
            "endpoint_id": "POST /b",
            "method": "POST",
            "path_template": "/b",
            "is_pve": False,
            "has_object_id": False,
            "acquisition": "script_file",
            "auth_observed": [],
        },
        {
            "endpoint_id": "GET /openapi",
            "method": "GET",
            "path_template": "/openapi",
            "is_pve": False,
            "has_object_id": False,
            "acquisition": "openapi",
            "auth_observed": [],
        },
    ]
    top = prioritize_pve(endpoints, top_k=10)
    assert len(top) == 2
    assert top[0]["method"] == "POST"
    assert all(e["acquisition"] == "script_file" for e in top)


def test_prioritize_falls_back_to_router_write_when_no_pve():
    endpoints = [
        {
            "endpoint_id": "GET /plain",
            "method": "GET",
            "path_template": "/plain",
            "is_pve": False,
            "has_object_id": False,
            "acquisition": "router",
            "auth_observed": [],
        },
        {
            "endpoint_id": "POST /orders",
            "method": "POST",
            "path_template": "/orders",
            "is_pve": False,
            "has_object_id": False,
            "acquisition": "router",
            "auth_observed": [],
        },
    ]
    top = prioritize_pve(endpoints, top_k=10)
    assert len(top) == 2
    assert top[0]["endpoint_id"] == "POST /orders"


def test_prioritize_keeps_pve_gate_for_router_only():
    endpoints = [
        {
            "endpoint_id": "GET /plain",
            "method": "GET",
            "path_template": "/plain",
            "is_pve": False,
            "has_object_id": False,
            "acquisition": "router",
            "auth_observed": [],
        },
        {
            "endpoint_id": "GET /orders/{id}",
            "method": "GET",
            "path_template": "/orders/{id}",
            "is_pve": True,
            "has_object_id": True,
            "acquisition": "router",
            "auth_observed": [],
        },
    ]
    top = prioritize_pve(endpoints, top_k=10)
    assert len(top) == 1
    assert top[0]["endpoint_id"] == "GET /orders/{id}"
