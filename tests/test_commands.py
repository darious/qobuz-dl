from qobuz_dl.commands import qobuz_dl_args


def test_reset_defaults_to_oauth_callback_listener():
    args = qobuz_dl_args().parse_args(["-r"])

    assert args.reset is True
    assert args.host == "0.0.0.0"
    assert args.port == 0
    assert args.callback is None


def test_reset_accepts_callback_overrides():
    args = qobuz_dl_args().parse_args(
        ["--callback", "http://192.168.1.50:8765", "--host", "0.0.0.0", "--port", "8765", "-r"]
    )

    assert args.callback == "http://192.168.1.50:8765"
    assert args.host == "0.0.0.0"
    assert args.port == 8765


def test_oauth_subcommand_accepts_manual_mode():
    args = qobuz_dl_args().parse_args(["oauth", "--manual", "abc123"])

    assert args.command == "oauth"
    assert args.manual is True
    assert args.CODE_OR_URL == "abc123"
