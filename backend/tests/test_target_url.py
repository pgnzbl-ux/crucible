"""靶场地址：对外与复现一律 IP:port，禁止 host.docker.internal。"""
from unittest.mock import patch

from app.contexts.agent.target_url import (
    host_advertise_ip,
    port_from_url,
    publish_target_url,
    rewrite_loopback_host,
    rewrite_url_for_agent_container,
)


def test_port_from_url():
    assert port_from_url("http://localhost:3001") == 3001
    assert port_from_url("http://127.0.0.1:8080/login") == 8080
    assert port_from_url(None) is None


def test_rewrite_loopback_keeps_port():
    assert rewrite_loopback_host("http://localhost:3001", "192.168.1.8") == "http://192.168.1.8:3001"
    assert rewrite_loopback_host("http://127.0.0.1:8000", "10.0.0.2") == "http://10.0.0.2:8000"


def test_publish_target_url_uses_advertise_ip():
    assert publish_target_url(3001, advertise_ip="192.168.1.8") == "http://192.168.1.8:3001"


def test_rewrite_for_agent_uses_advertise_ip_not_docker_internal():
    assert (
        rewrite_url_for_agent_container("http://192.168.1.8:3001", advertise_ip="192.168.1.8")
        == "http://192.168.1.8:3001"
    )
    assert (
        rewrite_url_for_agent_container("http://localhost:8080", advertise_ip="10.0.0.8")
        == "http://10.0.0.8:8080"
    )
    assert (
        rewrite_url_for_agent_container("http://127.0.0.1:5000/login", advertise_ip="10.0.0.8")
        == "http://10.0.0.8:5000/login"
    )
    assert (
        rewrite_url_for_agent_container(
            "http://host.docker.internal:3001/x", advertise_ip="10.0.0.8"
        )
        == "http://10.0.0.8:3001/x"
    )
    assert rewrite_url_for_agent_container("http://example.com") == "http://example.com"
    assert rewrite_url_for_agent_container(None) is None


def test_host_advertise_ip_skips_loopback():
    with patch("app.contexts.agent.target_url.socket.socket") as sock_cls:
        inst = sock_cls.return_value
        inst.getsockname.return_value = ("192.168.9.3", 12345)
        assert host_advertise_ip() == "192.168.9.3"
