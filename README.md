# qobuz-dl

Search, explore, and download Lossless and Hi-Res music from [Qobuz](https://www.qobuz.com/).

> You need an active Qobuz subscription.

## Install

```bash
pip3 install --upgrade qobuz-dl
```

On Windows, install `windows-curses` first:

```bash
pip3 install windows-curses
pip3 install --upgrade qobuz-dl
```

## Setup

Authenticate and create/reset the config:

```bash
qobuz-dl -r
```

The reset flow:

1. asks for your download folder,
2. asks for default quality,
3. fetches current Qobuz app tokens,
4. prints a Qobuz login URL,
5. listens for the OAuth callback,
6. saves `user_id` and `user_auth_token` to `~/.config/qobuz-dl/config.ini`.

Quality values:

```text
5   MP3 320
6   lossless 16-bit / 44.1 kHz
7   hi-res up to 24-bit / 96 kHz
27  hi-res above 24-bit / 96 kHz
```

If the automatically detected callback address is wrong, pass one explicitly:

```bash
qobuz-dl --callback http://192.168.1.50:8765 --host 0.0.0.0 --port 8765 -r
```

For desktop/local use, the `oauth` subcommand is also available:

```bash
qobuz-dl oauth
```

For copy/paste OAuth troubleshooting:

```bash
qobuz-dl oauth --manual
```

Email/password authentication is no longer used.

## Examples

Download an album:

```bash
qobuz-dl dl https://play.qobuz.com/album/qxjbxh1dc3xyb
```

Download in maximum quality:

```bash
qobuz-dl dl https://play.qobuz.com/playlist/5388296 -q 27
```

Download multiple URLs to a custom directory:

```bash
qobuz-dl dl https://play.qobuz.com/artist/2038380 https://play.qobuz.com/album/ip8qjy1m6dakc -d "Some pop from 2020"
```

Download URLs from a text file:

```bash
qobuz-dl dl urls.txt
```

Download all music from an artist except singles, EPs, and VA releases:

```bash
qobuz-dl dl https://play.qobuz.com/artist/2528676 --albums-only
```

Interactive search:

```bash
qobuz-dl fun -l 10
```

Lucky search:

```bash
qobuz-dl lucky playboi carti die lit
```

Skip the downloaded-ID database:

```bash
qobuz-dl dl https://play.qobuz.com/album/qxjbxh1dc3xyb --no-db
```

Purge the downloaded-ID database:

```bash
qobuz-dl -p
```

Show config:

```bash
qobuz-dl -sc
```

## Module Usage

```python
from qobuz_dl.core import QobuzDL

qobuz = QobuzDL(quality=27)
qobuz.get_tokens()
qobuz.initialize_client_with_token(
    user_auth_token="...",
    app_id=qobuz.app_id,
    secrets=qobuz.secrets,
    user_id="...",
)

qobuz.handle_url("https://play.qobuz.com/album/va4j3hdlwaubc")
```

## Development

This fork has a small pytest suite:

```bash
python3 -m pytest
```

The tests cover OAuth URL/code parsing and CLI argument parsing. They do not contact Qobuz.

## Disclaimer

This tool was written for educational purposes. `qobuz-dl` is not affiliated with Qobuz.
