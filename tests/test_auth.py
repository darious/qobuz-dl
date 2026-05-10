from qobuz_dl.qopy import _parse_oauth_result


def test_parse_oauth_code_autorisation_url():
    result = _parse_oauth_result("http://192.168.1.10:8765/?code_autorisation=abc123")

    assert result["code"] == "abc123"
    assert result["token"] is None


def test_parse_oauth_token_url():
    result = _parse_oauth_result(
        "http://192.168.1.10:8765/?user_auth_token=tok123&user_id=42"
    )

    assert result["token"] == "tok123"
    assert result["user_id"] == "42"


def test_parse_bare_code():
    result = _parse_oauth_result("abc123")

    assert result["code"] == "abc123"
