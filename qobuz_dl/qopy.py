# Wrapper for Qo-DL Reborn. This is a sligthly modified version
# of qopy, originally written by Sorrow446. All credits to the
# original author.

import hashlib
import logging
import time
from urllib.parse import parse_qs, urlparse

import requests

from qobuz_dl.exceptions import (
    AuthenticationError,
    IneligibleError,
    InvalidAppIdError,
    InvalidAppSecretError,
    InvalidQuality,
)
from qobuz_dl.color import GREEN, YELLOW

RESET = "Reset your credentials with 'qobuz-dl -r'"

logger = logging.getLogger(__name__)


class Client:
    def __init__(self, app_id, secrets):
        logger.info(f"{YELLOW}Logging...")
        self.secrets = secrets
        self.id = str(app_id)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:83.0) Gecko/20100101 Firefox/83.0",
                "X-App-Id": self.id,
                "Content-Type": "application/json;charset=UTF-8"

            }
        )
        self.base = "https://www.qobuz.com/api.json/0.2/"
        self.sec = None
        self.uat = None
        self.user_id = None
        self.label = None

    def api_call(self, epoint, **kwargs):
        if epoint == "track/get":
            params = {"track_id": kwargs["id"]}
        elif epoint == "album/get":
            params = {"album_id": kwargs["id"]}
        elif epoint == "playlist/get":
            params = {
                "extra": "tracks",
                "playlist_id": kwargs["id"],
                "limit": 500,
                "offset": kwargs["offset"],
            }
        elif epoint == "artist/get":
            params = {
                "app_id": self.id,
                "artist_id": kwargs["id"],
                "limit": 500,
                "offset": kwargs["offset"],
                "extra": "albums",
            }
        elif epoint == "label/get":
            params = {
                "label_id": kwargs["id"],
                "limit": 500,
                "offset": kwargs["offset"],
                "extra": "albums",
            }
        elif epoint == "favorite/getUserFavorites":
            unix = time.time()
            # r_sig = "userLibrarygetAlbumsList" + str(unix) + kwargs["sec"]
            r_sig = "favoritegetUserFavorites" + str(unix) + kwargs["sec"]
            r_sig_hashed = hashlib.md5(r_sig.encode("utf-8")).hexdigest()
            params = {
                "app_id": self.id,
                "user_auth_token": self.uat,
                "type": "albums",
                "request_ts": unix,
                "request_sig": r_sig_hashed,
            }
        elif epoint == "track/getFileUrl":
            unix = time.time()
            track_id = kwargs["id"]
            fmt_id = kwargs["fmt_id"]
            if int(fmt_id) not in (5, 6, 7, 27):
                raise InvalidQuality("Invalid quality id: choose between 5, 6, 7 or 27")
            r_sig = "trackgetFileUrlformat_id{}intentstreamtrack_id{}{}{}".format(
                fmt_id, track_id, unix, kwargs.get("sec", self.sec)
            )
            r_sig_hashed = hashlib.md5(r_sig.encode("utf-8")).hexdigest()
            params = {
                "request_ts": unix,
                "request_sig": r_sig_hashed,
                "track_id": track_id,
                "format_id": fmt_id,
                "intent": "stream",
            }
        else:
            params = kwargs
        r = self.session.get(self.base + epoint, params=params)
        if (
            epoint in ["track/getFileUrl", "favorite/getUserFavorites"]
            and r.status_code == 400
        ):
            raise InvalidAppSecretError(f"Invalid app secret: {r.json()}.\n" + RESET)

        r.raise_for_status()
        return r.json()

    def auth_with_token(self, user_auth_token, user_id=None):
        self.uat = user_auth_token
        self.user_id = str(user_id) if user_id else None
        self.session.headers.update({"X-User-Auth-Token": self.uat})

        if self.user_id:
            response = self.session.get(
                self.base + "user/login",
                params={
                    "user_id": self.user_id,
                    "user_auth_token": self.uat,
                    "app_id": self.id,
                },
            )
        else:
            response = self.session.post(
                self.base + "user/login",
                headers={"Content-Type": "text/plain;charset=UTF-8"},
                data="extra=partner",
            )

        if response.status_code == 401:
            raise AuthenticationError("Invalid token.\n" + RESET)
        if response.status_code == 400:
            raise InvalidAppIdError("Invalid app id.\n" + RESET)
        response.raise_for_status()

        usr_info = response.json()
        self._set_user_info(usr_info)
        logger.info(f"{GREEN}Membership: {self.label}")
        self.cfg_setup()
        return usr_info

    def login_with_oauth_result(self, code_or_url, private_key=None):
        result = _parse_oauth_result(code_or_url)
        if result["token"]:
            return self.auth_with_token(result["token"], result["user_id"])

        code = result["code"]
        if not code:
            raise AuthenticationError("OAuth response did not contain a token or code")

        token_info = self._exchange_oauth_code(code, private_key)
        token = token_info.get("token") or token_info.get("user_auth_token")
        if token:
            return self.auth_with_token(token, token_info.get("user_id"))

        if token_info.get("user"):
            self._set_user_info(token_info)
            if not self.uat:
                self.uat = token_info.get("user_auth_token")
            if self.uat:
                self.session.headers.update({"X-User-Auth-Token": self.uat})
            self.cfg_setup()
            return token_info

        raise AuthenticationError("OAuth callback response did not contain a token")

    def _exchange_oauth_code(self, code, private_key=None):
        attempts = [
            ("GET", "code"),
            ("POST", "code"),
            ("GET", "code_autorisation"),
            ("POST", "code_autorisation"),
        ]
        last_error = None
        for method, param_name in attempts:
            params = {param_name: code, "app_id": self.id}
            if private_key:
                params["private_key"] = private_key
            try:
                if method == "GET":
                    response = self.session.get(self.base + "oauth/callback", params=params)
                else:
                    response = self.session.post(
                        self.base + "oauth/callback",
                        headers={"Content-Type": "text/plain;charset=UTF-8"},
                        data=requests.compat.urlencode(params),
                    )
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                last_error = exc
        raise AuthenticationError(f"OAuth code exchange failed: {last_error}")

    def _set_user_info(self, usr_info):
        user = usr_info.get("user") or {}
        credential = user.get("credential") or {}
        parameters = credential.get("parameters")
        if not parameters:
            raise IneligibleError("Free accounts are not eligible to download tracks.")
        self.label = parameters.get("short_label")
        if not self.user_id and user.get("id") is not None:
            self.user_id = str(user["id"])
        if not self.uat and usr_info.get("user_auth_token"):
            self.uat = usr_info["user_auth_token"]

    def multi_meta(self, epoint, key, id, type):
        total = 1
        offset = 0
        while total > 0:
            if type in ["tracks", "albums"]:
                j = self.api_call(epoint, id=id, offset=offset, type=type)[type]
            else:
                j = self.api_call(epoint, id=id, offset=offset, type=type)
            if offset == 0:
                yield j
                total = j[key] - 500
            else:
                yield j
                total -= 500
            offset += 500

    def get_album_meta(self, id):
        return self.api_call("album/get", id=id)

    def get_track_meta(self, id):
        return self.api_call("track/get", id=id)

    def get_track_url(self, id, fmt_id):
        return self.api_call("track/getFileUrl", id=id, fmt_id=fmt_id)

    def get_artist_meta(self, id):
        return self.multi_meta("artist/get", "albums_count", id, None)

    def get_plist_meta(self, id):
        return self.multi_meta("playlist/get", "tracks_count", id, None)

    def get_label_meta(self, id):
        return self.multi_meta("label/get", "albums_count", id, None)

    def search_albums(self, query, limit):
        return self.api_call("album/search", query=query, limit=limit)

    def search_artists(self, query, limit):
        return self.api_call("artist/search", query=query, limit=limit)

    def search_playlists(self, query, limit):
        return self.api_call("playlist/search", query=query, limit=limit)

    def search_tracks(self, query, limit):
        return self.api_call("track/search", query=query, limit=limit)

    def get_favorite_albums(self, offset, limit):
        return self.api_call(
            "favorite/getUserFavorites", type="albums", offset=offset, limit=limit
        )

    def get_favorite_tracks(self, offset, limit):
        return self.api_call(
            "favorite/getUserFavorites", type="tracks", offset=offset, limit=limit
        )

    def get_favorite_artists(self, offset, limit):
        return self.api_call(
            "favorite/getUserFavorites", type="artists", offset=offset, limit=limit
        )

    def get_user_playlists(self, limit):
        return self.api_call("playlist/getUserPlaylists", limit=limit)

    def test_secret(self, sec):
        try:
            self.api_call("track/getFileUrl", id=5966783, fmt_id=5, sec=sec)
            return True
        except InvalidAppSecretError:
            return False

    def cfg_setup(self):
        for secret in self.secrets:
            # Falsy secrets
            if not secret:
                continue

            if self.test_secret(secret):
                self.sec = secret
                break

        if self.sec is None:
            raise InvalidAppSecretError("Can't find any valid app secret.\n" + RESET)


def _parse_oauth_result(code_or_url):
    result = {"token": None, "user_id": None, "code": None}
    if not code_or_url:
        return result

    parsed = urlparse(code_or_url)
    if parsed.query:
        params = parse_qs(parsed.query)
        result["token"] = (
            _first(params, "user_auth_token")
            or _first(params, "token")
        )
        result["user_id"] = _first(params, "user_id")
        result["code"] = (
            _first(params, "code_autorisation")
            or _first(params, "code")
        )
        return result

    if code_or_url.startswith("http"):
        return result

    result["code"] = code_or_url
    return result


def _first(params, key):
    values = params.get(key) or []
    return values[0] if values else None
