import logging
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from bs4 import BeautifulSoup as bso
from pathvalidate import sanitize_filename

from qobuz_dl.bundle import Bundle
from qobuz_dl import downloader, qopy
from qobuz_dl.color import CYAN, GREEN, OFF, RED, YELLOW, DF, RESET
from qobuz_dl.exceptions import NonStreamable
from qobuz_dl.db import create_db, handle_download_id
from qobuz_dl.utils import (
    get_url_info,
    make_m3u,
    smart_discography_filter,
    format_duration,
    create_and_return_dir,
    PartialFormatter,
)

WEB_URL = "https://play.qobuz.com/"
ARTISTS_SELECTOR = "td.chartlist-artist > a"
TITLE_SELECTOR = "td.chartlist-name > a"
QUALITIES = {
    5: "5 - MP3",
    6: "6 - 16 bit, 44.1kHz",
    7: "7 - 24 bit, <96kHz",
    27: "27 - 24 bit, >96kHz",
}

logger = logging.getLogger(__name__)


class QobuzDL:
    def __init__(
        self,
        directory="Qobuz Downloads",
        quality=6,
        embed_art=False,
        lucky_limit=1,
        lucky_type="album",
        interactive_limit=20,
        ignore_singles_eps=False,
        no_m3u_for_playlists=False,
        quality_fallback=True,
        cover_og_quality=False,
        no_cover=False,
        downloads_db=None,
        folder_format="{artist} - {album} ({year}) [{bit_depth}B-"
        "{sampling_rate}kHz]",
        track_format="{tracknumber}. {tracktitle}",
        smart_discography=False,
    ):
        self.directory = create_and_return_dir(directory)
        self.quality = quality
        self.embed_art = embed_art
        self.lucky_limit = lucky_limit
        self.lucky_type = lucky_type
        self.interactive_limit = interactive_limit
        self.ignore_singles_eps = ignore_singles_eps
        self.no_m3u_for_playlists = no_m3u_for_playlists
        self.quality_fallback = quality_fallback
        self.cover_og_quality = cover_og_quality
        self.no_cover = no_cover
        self.downloads_db = create_db(downloads_db) if downloads_db else None
        self.folder_format = folder_format
        self.track_format = track_format
        self.smart_discography = smart_discography

    def initialize_client_with_token(self, user_auth_token, app_id, secrets, user_id=None):
        self.client = qopy.Client(app_id, secrets)
        usr_info = self.client.auth_with_token(user_auth_token, user_id)
        self.token_user_id = self.client.user_id
        self.token_user_auth_token = self.client.uat
        logger.info(f"{YELLOW}Set max quality: {QUALITIES[int(self.quality)]}\n")
        return usr_info

    def initialize_client_with_oauth(self, code_or_url, app_id, secrets, private_key=None):
        self.client = qopy.Client(app_id, secrets)
        usr_info = self.client.login_with_oauth_result(code_or_url, private_key)
        self.token_user_id = self.client.user_id
        self.token_user_auth_token = self.client.uat
        logger.info(f"{YELLOW}Set max quality: {QUALITIES[int(self.quality)]}\n")
        return usr_info

    def get_tokens(self):
        bundle = Bundle()
        self.app_id = bundle.get_app_id()
        self.secrets = [
            secret for secret in bundle.get_secrets().values() if secret
        ]  # avoid empty fields
        self.private_key = bundle.get_private_key()

    def handle_oauth_login(
        self,
        code_or_url=None,
        listen=False,
        manual=False,
        callback_url=None,
        bind_host="127.0.0.1",
        bind_port=0,
    ):
        if not getattr(self, "app_id", None) or not getattr(self, "secrets", None):
            logger.info(f"{YELLOW}Getting tokens. Please wait...")
            self.get_tokens()

        if not code_or_url:
            if manual:
                code_or_url = self._prompt_oauth_redirect()
            elif callback_url:
                code_or_url = self._capture_oauth_redirect(
                    callback_url=callback_url,
                    bind_host=bind_host,
                    bind_port=bind_port,
                )
            else:
                code_or_url = self._capture_oauth_redirect(
                    bind_host=bind_host,
                    bind_port=bind_port,
                )

        self.initialize_client_with_oauth(
            code_or_url,
            self.app_id,
            self.secrets,
            getattr(self, "private_key", None),
        )
        logger.info(f"{GREEN}OAuth login successful!")
        return {
            "user_id": getattr(self, "token_user_id", None),
            "user_auth_token": getattr(self, "token_user_auth_token", None),
            "app_id": self.app_id,
            "app_secret": getattr(self.client, "sec", None),
        }

    def _oauth_url(self, redirect_url):
        return (
            "https://www.qobuz.com/signin/oauth?"
            + urlencode(
                {
                    "ext_app_id": self.app_id,
                    "redirect_url": redirect_url,
                }
            )
        )

    def _prompt_oauth_redirect(self):
        redirect_url = "http://127.0.0.1:8765"
        oauth_url = self._oauth_url(redirect_url)
        logger.info(f"{YELLOW}Open this URL in your browser to authenticate with Qobuz:")
        logger.info(f"{CYAN}{oauth_url}{RESET}")
        logger.info(
            f"{YELLOW}After login, your browser should land on 127.0.0.1 and may show a connection error."
        )
        logger.info(
            f"{YELLOW}That is expected. Copy the full 127.0.0.1 URL from the address bar and paste it here."
        )
        code_or_url = input(f"{YELLOW}Paste final redirect URL/code:\n- ").strip()
        if not code_or_url:
            raise RuntimeError("No OAuth redirect URL/code was provided")
        return code_or_url

    def _capture_oauth_redirect(self, callback_url=None, bind_host="127.0.0.1", bind_port=0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((bind_host, bind_port))
            host, port = sock.getsockname()[:2]

        if callback_url:
            oauth_url = self._oauth_url(callback_url)
        else:
            redirect_host = (
                _detect_lan_ip()
                if bind_host in ("", "0.0.0.0")
                else bind_host
            )
            oauth_url = self._oauth_url(f"http://{redirect_host}:{port}")

        class OAuthHandler(BaseHTTPRequestHandler):
            result = None

            def do_GET(self):
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                token = _first(params, "user_auth_token") or _first(params, "token")
                code = _first(params, "code_autorisation") or _first(params, "code")
                if token or code:
                    OAuthHandler.result = "http://{}:{}{}".format(bind_host, port, self.path)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(
                        b"<html><body style='font-family:system-ui;text-align:center;padding:60px'>"
                        b"<h2>Login successful</h2>"
                        b"<p>You can close this tab and return to your terminal.</p>"
                        b"</body></html>"
                    )
                else:
                    OAuthHandler.result = "http://{}:{}{}".format(bind_host, port, self.path)
                    self.send_response(400)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(
                        b"<html><body><h2>Unexpected OAuth response</h2></body></html>"
                    )

            def log_message(self, format, *args):
                pass

        logger.info(f"{YELLOW}Open this URL in your browser to authenticate with Qobuz:")
        logger.info(f"{CYAN}{oauth_url}{RESET}")
        logger.info(
            f"{YELLOW}Listening on {bind_host}:{port} to capture the OAuth redirect."
        )
        logger.info(
            f"{YELLOW}The browser must be able to reach the advertised callback URL."
        )

        server = HTTPServer((bind_host, port), OAuthHandler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        pasted = input(f"{YELLOW}Press Enter after login, or paste redirect URL/code:\n- ").strip()
        server.server_close()
        thread.join(timeout=1)

        if pasted:
            return pasted
        if OAuthHandler.result:
            return OAuthHandler.result
        raise RuntimeError("No OAuth redirect was captured")

    def download_from_id(self, item_id, album=True, alt_path=None):
        if handle_download_id(self.downloads_db, item_id, add_id=False):
            logger.info(
                f"{OFF}This release ID ({item_id}) was already downloaded "
                "according to the local database.\nUse the '--no-db' flag "
                "to bypass this."
            )
            return
        try:
            dloader = downloader.Download(
                self.client,
                item_id,
                alt_path or self.directory,
                int(self.quality),
                self.embed_art,
                self.ignore_singles_eps,
                self.quality_fallback,
                self.cover_og_quality,
                self.no_cover,
                self.folder_format,
                self.track_format,
            )
            dloader.download_id_by_type(not album)
            handle_download_id(self.downloads_db, item_id, add_id=True)
        except (requests.exceptions.RequestException, NonStreamable) as e:
            logger.error(f"{RED}Error getting release: {e}. Skipping...")

    def handle_url(self, url):
        possibles = {
            "playlist": {
                "func": self.client.get_plist_meta,
                "iterable_key": "tracks",
            },
            "artist": {
                "func": self.client.get_artist_meta,
                "iterable_key": "albums",
            },
            "label": {
                "func": self.client.get_label_meta,
                "iterable_key": "albums",
            },
            "album": {"album": True, "func": None, "iterable_key": None},
            "track": {"album": False, "func": None, "iterable_key": None},
        }
        try:
            url_type, item_id = get_url_info(url)
            type_dict = possibles[url_type]
        except (KeyError, IndexError):
            logger.info(
                f'{RED}Invalid url: "{url}". Use urls from ' "https://play.qobuz.com!"
            )
            return
        if type_dict["func"]:
            content = [item for item in type_dict["func"](item_id)]
            content_name = content[0]["name"]
            logger.info(
                f"{YELLOW}Downloading all the music from {content_name} "
                f"({url_type})!"
            )
            new_path = create_and_return_dir(
                os.path.join(self.directory, sanitize_filename(content_name))
            )

            if self.smart_discography and url_type == "artist":
                # change `save_space` and `skip_extras` for customization
                items = smart_discography_filter(
                    content,
                    save_space=True,
                    skip_extras=True,
                )
            else:
                items = [item[type_dict["iterable_key"]]["items"] for item in content][
                    0
                ]

            logger.info(f"{YELLOW}{len(items)} downloads in queue")
            for item in items:
                self.download_from_id(
                    item["id"],
                    True if type_dict["iterable_key"] == "albums" else False,
                    new_path,
                )
            if url_type == "playlist" and not self.no_m3u_for_playlists:
                make_m3u(new_path)
        else:
            self.download_from_id(item_id, type_dict["album"])

    def download_list_of_urls(self, urls):
        if not urls or not isinstance(urls, list):
            logger.info(f"{OFF}Nothing to download")
            return
        for url in urls:
            if "last.fm" in url:
                self.download_lastfm_pl(url)
            elif os.path.isfile(url):
                self.download_from_txt_file(url)
            else:
                self.handle_url(url)

    def download_from_txt_file(self, txt_file):
        with open(txt_file, "r") as txt:
            try:
                urls = [
                    line.replace("\n", "")
                    for line in txt.readlines()
                    if not line.strip().startswith("#")
                ]
            except Exception as e:
                logger.error(f"{RED}Invalid text file: {e}")
                return
            logger.info(
                f"{YELLOW}qobuz-dl will download {len(urls)}"
                f" urls from file: {txt_file}"
            )
            self.download_list_of_urls(urls)

    def lucky_mode(self, query, download=True):
        if len(query) < 3:
            logger.info(f"{RED}Your search query is too short or invalid")
            return

        logger.info(
            f'{YELLOW}Searching {self.lucky_type}s for "{query}".\n'
            f"{YELLOW}qobuz-dl will attempt to download the first "
            f"{self.lucky_limit} results."
        )
        results = self.search_by_type(query, self.lucky_type, self.lucky_limit, True)

        if download:
            self.download_list_of_urls(results)

        return results

    def search_by_type(self, query, item_type, limit=10, lucky=False):
        if len(query) < 3:
            logger.info("{RED}Your search query is too short or invalid")
            return

        possibles = {
            "album": {
                "func": self.client.search_albums,
                "album": True,
                "key": "albums",
                "format": "{artist[name]} - {title}",
                "requires_extra": True,
            },
            "artist": {
                "func": self.client.search_artists,
                "album": True,
                "key": "artists",
                "format": "{name} - ({albums_count} releases)",
                "requires_extra": False,
            },
            "track": {
                "func": self.client.search_tracks,
                "album": False,
                "key": "tracks",
                "format": "{performer[name]} - {title}",
                "requires_extra": True,
            },
            "playlist": {
                "func": self.client.search_playlists,
                "album": False,
                "key": "playlists",
                "format": "{name} - ({tracks_count} releases)",
                "requires_extra": False,
            },
        }

        try:
            mode_dict = possibles[item_type]
            results = mode_dict["func"](query, limit)
            iterable = results[mode_dict["key"]]["items"]
            item_list = []
            for i in iterable:
                fmt = PartialFormatter()
                text = fmt.format(mode_dict["format"], **i)
                if mode_dict["requires_extra"]:

                    text = "{} - {} [{}]".format(
                        text,
                        format_duration(i["duration"]),
                        "HI-RES" if i["hires_streamable"] else "LOSSLESS",
                    )

                url = "{}{}/{}".format(WEB_URL, item_type, i.get("id", ""))
                item_list.append({"text": text, "url": url} if not lucky else url)
            return item_list
        except (KeyError, IndexError):
            logger.info(f"{RED}Invalid type: {item_type}")
            return

    def interactive(self, download=True):
        try:
            from pick import pick
        except (ImportError, ModuleNotFoundError):
            if os.name == "nt":
                sys.exit(
                    "Please install curses with "
                    '"pip3 install windows-curses" to continue'
                )
            raise

        qualities = [
            {"q_string": "320", "q": 5},
            {"q_string": "Lossless", "q": 6},
            {"q_string": "Hi-res =< 96kHz", "q": 7},
            {"q_string": "Hi-Res > 96 kHz", "q": 27},
        ]

        def get_title_text(option):
            return option.get("text")

        def get_quality_text(option):
            return option.get("q_string")

        try:
            item_types = ["Albums", "Tracks", "Artists", "Playlists"]
            selected_type = pick(item_types, "I'll search for:\n[press Intro]")[0][
                :-1
            ].lower()
            logger.info(f"{YELLOW}Ok, we'll search for " f"{selected_type}s{RESET}")
            final_url_list = []
            while True:
                query = input(
                    f"{CYAN}Enter your search: [Ctrl + c to quit]\n" f"-{DF} "
                )
                logger.info(f"{YELLOW}Searching...{RESET}")
                options = self.search_by_type(
                    query, selected_type, self.interactive_limit
                )
                if not options:
                    logger.info(f"{OFF}Nothing found{RESET}")
                    continue
                title = (
                    f'*** RESULTS FOR "{query.title()}" ***\n\n'
                    "Select [space] the item(s) you want to download "
                    "(one or more)\nPress Ctrl + c to quit\n"
                    "Don't select anything to try another search"
                )
                selected_items = pick(
                    options,
                    title,
                    multiselect=True,
                    min_selection_count=0,
                    options_map_func=get_title_text,
                )
                if len(selected_items) > 0:
                    [final_url_list.append(i[0]["url"]) for i in selected_items]
                    y_n = pick(
                        ["Yes", "No"],
                        "Items were added to queue to be downloaded. "
                        "Keep searching?",
                    )
                    if y_n[0][0] == "N":
                        break
                else:
                    logger.info(f"{YELLOW}Ok, try again...{RESET}")
                    continue
            if final_url_list:
                desc = (
                    "Select [intro] the quality (the quality will "
                    "be automatically\ndowngraded if the selected "
                    "is not found)"
                )
                self.quality = pick(
                    qualities,
                    desc,
                    default_index=1,
                    options_map_func=get_quality_text,
                )[0]["q"]

                if download:
                    self.download_list_of_urls(final_url_list)

                return final_url_list
        except KeyboardInterrupt:
            logger.info(f"{YELLOW}Bye")
            return

    def download_lastfm_pl(self, playlist_url):
        # Apparently, last fm API doesn't have a playlist endpoint. If you
        # find out that it has, please fix this!
        try:
            r = requests.get(playlist_url, timeout=10)
        except requests.exceptions.RequestException as e:
            logger.error(f"{RED}Playlist download failed: {e}")
            return
        soup = bso(r.content, "html.parser")
        artists = [artist.text for artist in soup.select(ARTISTS_SELECTOR)]
        titles = [title.text for title in soup.select(TITLE_SELECTOR)]

        track_list = []
        if len(artists) == len(titles) and artists:
            track_list = [
                artist + " " + title for artist, title in zip(artists, titles)
            ]

        if not track_list:
            logger.info(f"{OFF}Nothing found")
            return

        pl_title = sanitize_filename(soup.select_one("h1").text)
        pl_directory = os.path.join(self.directory, pl_title)
        logger.info(
            f"{YELLOW}Downloading playlist: {pl_title} " f"({len(track_list)} tracks)"
        )

        for i in track_list:
            track_id = get_url_info(self.search_by_type(i, "track", 1, lucky=True)[0])[
                1
            ]
            if track_id:
                self.download_from_id(track_id, False, pl_directory)

        if not self.no_m3u_for_playlists:
            make_m3u(pl_directory)


def _first(params, key):
    values = params.get(key) or []
    return values[0] if values else None


def _detect_lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass

    return "127.0.0.1"
