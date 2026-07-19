"""native_capture.url_validation 的測試：正常網址通過、攻擊案例全部拒絕、log 遮罩正確。"""

from __future__ import annotations

import pytest

from native_capture.url_validation import UrlValidationError, mask_url_for_log, validate_url


class TestValidateUrlAccepts:
    def test_accepts_https(self) -> None:
        assert validate_url("https://example.com/") == "https://example.com/"

    def test_accepts_http_with_path_and_query(self) -> None:
        url = "http://example.com/page?token=abc&id=1"
        assert validate_url(url) == url


class TestValidateUrlRejectsAttacks:
    @pytest.mark.parametrize(
        "url",
        [
            "ftp://example.com/",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "",
            "not-a-url",
        ],
    )
    def test_rejects_disallowed_scheme(self, url: str) -> None:
        with pytest.raises(UrlValidationError):
            validate_url(url)

    def test_rejects_username_password(self) -> None:
        with pytest.raises(UrlValidationError, match="username/password"):
            validate_url("https://user:pass@example.com/")

    def test_rejects_missing_hostname(self) -> None:
        with pytest.raises(UrlValidationError, match="hostname"):
            validate_url("https:///path")

    def test_rejects_localhost(self) -> None:
        with pytest.raises(UrlValidationError, match="localhost"):
            validate_url("http://localhost:8080/")

    def test_rejects_localhost_subdomain(self) -> None:
        with pytest.raises(UrlValidationError, match="localhost"):
            validate_url("http://foo.localhost/")

    def test_rejects_loopback_ip(self) -> None:
        with pytest.raises(UrlValidationError, match="loopback"):
            validate_url("http://127.0.0.1/")

    @pytest.mark.parametrize(
        "host",
        ["10.0.0.5", "192.168.1.1", "172.16.0.1"],
    )
    def test_rejects_private_ip(self, host: str) -> None:
        with pytest.raises(UrlValidationError, match="loopback／private／link-local"):
            validate_url(f"http://{host}/")

    def test_rejects_link_local_ip(self) -> None:
        with pytest.raises(UrlValidationError, match="loopback／private／link-local"):
            validate_url("http://169.254.1.1/")

    def test_rejects_dot_local_domain(self) -> None:
        with pytest.raises(UrlValidationError, match="link-local"):
            validate_url("http://myprinter.local/")

    def test_rejects_ipv6_loopback(self) -> None:
        with pytest.raises(UrlValidationError, match="loopback"):
            validate_url("http://[::1]/")


class TestMaskUrlForLog:
    def test_masks_query_values_keeps_keys(self) -> None:
        masked = mask_url_for_log("https://example.com/page?token=secret123&id=42")
        assert "secret123" not in masked
        assert "42" not in masked
        assert "token=***" in masked
        assert "id=***" in masked

    def test_strips_userinfo(self) -> None:
        masked = mask_url_for_log("https://user:pass@example.com/page")
        assert "user" not in masked
        assert "pass" not in masked
        assert masked == "https://example.com/page"

    def test_no_query_unchanged_shape(self) -> None:
        masked = mask_url_for_log("https://example.com/page")
        assert masked == "https://example.com/page"
