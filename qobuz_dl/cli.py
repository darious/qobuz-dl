import configparser
import logging
import glob
import os
import sys

from qobuz_dl.bundle import Bundle
from qobuz_dl.color import GREEN, RED, YELLOW
from qobuz_dl.commands import qobuz_dl_args
from qobuz_dl.core import QobuzDL
from qobuz_dl.downloader import DEFAULT_FOLDER, DEFAULT_TRACK

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)

if os.name == "nt":
    OS_CONFIG = os.environ.get("APPDATA")
else:
    OS_CONFIG = os.path.join(os.environ["HOME"], ".config")

CONFIG_PATH = os.path.join(OS_CONFIG, "qobuz-dl")
CONFIG_FILE = os.path.join(CONFIG_PATH, "config.ini")
QOBUZ_DB = os.path.join(CONFIG_PATH, "qobuz_dl.db")


def _reset_config(config_file):
    logging.info(f"{YELLOW}Creating config file: {config_file}")
    config = configparser.ConfigParser()
    config["DEFAULT"]["user_id"] = ""
    config["DEFAULT"]["user_auth_token"] = ""
    config["DEFAULT"]["default_folder"] = (
        input("Folder for downloads (leave empty for default 'Qobuz Downloads')\n- ")
        or "Qobuz Downloads"
    )
    config["DEFAULT"]["default_quality"] = (
        input(
            "Download quality (5, 6, 7, 27) "
            "[320, LOSSLESS, 24B <96KHZ, 24B >96KHZ]"
            "\n(leave empty for default '27')\n- "
        )
        or "27"
    )
    config["DEFAULT"]["default_limit"] = "20"
    config["DEFAULT"]["no_m3u"] = "false"
    config["DEFAULT"]["albums_only"] = "false"
    config["DEFAULT"]["no_fallback"] = "false"
    config["DEFAULT"]["og_cover"] = "false"
    config["DEFAULT"]["embed_art"] = "false"
    config["DEFAULT"]["no_cover"] = "false"
    config["DEFAULT"]["no_database"] = "false"
    logging.info(f"{YELLOW}Getting tokens. Please wait...")
    bundle = Bundle()
    config["DEFAULT"]["app_id"] = str(bundle.get_app_id())
    config["DEFAULT"]["secrets"] = ",".join(bundle.get_secrets().values())
    config["DEFAULT"]["private_key"] = bundle.get_private_key() or ""
    config["DEFAULT"]["folder_format"] = DEFAULT_FOLDER
    config["DEFAULT"]["track_format"] = DEFAULT_TRACK
    config["DEFAULT"]["smart_discography"] = "false"
    with open(config_file, "w") as configfile:
        config.write(configfile)
    logging.info(
        f"{GREEN}Config file updated. Edit more options in {config_file}"
        "\nso you don't have to call custom flags every time you run "
        "a qobuz-dl command."
    )
    return config


def _remove_leftovers(directory):
    directory = os.path.join(directory, "**", ".*.tmp")
    for i in glob.glob(directory, recursive=True):
        try:
            os.remove(i)
        except:  # noqa
            pass


def _save_token(config_file, token_info):
    config = configparser.ConfigParser()
    config.read(config_file)
    if "DEFAULT" not in config:
        config["DEFAULT"] = {}
    if token_info.get("user_id"):
        config["DEFAULT"]["user_id"] = str(token_info["user_id"])
    if token_info.get("user_auth_token"):
        config["DEFAULT"]["user_auth_token"] = token_info["user_auth_token"]
    if token_info.get("app_id"):
        config["DEFAULT"]["app_id"] = str(token_info["app_id"])
    if token_info.get("app_secret"):
        config["DEFAULT"]["secrets"] = token_info["app_secret"]
    with open(config_file, "w") as configfile:
        config.write(configfile)
    logging.info(f"{GREEN}OAuth token saved to {config_file}")


def _handle_commands(qobuz, arguments):
    try:
        if arguments.command == "dl":
            qobuz.download_list_of_urls(arguments.SOURCE)
        elif arguments.command == "lucky":
            query = " ".join(arguments.QUERY)
            qobuz.lucky_type = arguments.type
            qobuz.lucky_limit = arguments.number
            qobuz.lucky_mode(query)
        elif arguments.command == "oauth":
            token_info = qobuz.handle_oauth_login(
                arguments.CODE_OR_URL,
                listen=arguments.listen,
                manual=arguments.manual,
                callback_url=arguments.oauth_callback,
                bind_host=arguments.oauth_host,
                bind_port=arguments.oauth_port,
            )
            if token_info:
                _save_token(CONFIG_FILE, token_info)
        else:
            qobuz.interactive_limit = arguments.limit
            qobuz.interactive()

    except KeyboardInterrupt:
        logging.info(
            f"{RED}Interrupted by user\n{YELLOW}Already downloaded items will "
            "be skipped if you try to download the same releases again."
        )

    finally:
        _remove_leftovers(qobuz.directory)


def _initial_checks():
    if not os.path.isdir(CONFIG_PATH) or not os.path.isfile(CONFIG_FILE):
        os.makedirs(CONFIG_PATH, exist_ok=True)
        if "-r" not in sys.argv and "--reset" not in sys.argv:
            _reset_config(CONFIG_FILE)

    if len(sys.argv) < 2:
        sys.exit(qobuz_dl_args().print_help())


def main():
    if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        sys.exit(qobuz_dl_args().print_help())

    _initial_checks()

    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)

    try:
        if not config.defaults() and ("-r" in sys.argv or "--reset" in sys.argv):
            raise KeyError("config missing; reset requested")
        user_id = config["DEFAULT"].get("user_id", "")
        user_auth_token = config["DEFAULT"].get("user_auth_token", "")
        default_folder = config["DEFAULT"]["default_folder"]
        default_limit = config["DEFAULT"]["default_limit"]
        default_quality = config["DEFAULT"]["default_quality"]
        no_m3u = config.getboolean("DEFAULT", "no_m3u")
        albums_only = config.getboolean("DEFAULT", "albums_only")
        no_fallback = config.getboolean("DEFAULT", "no_fallback")
        og_cover = config.getboolean("DEFAULT", "og_cover")
        embed_art = config.getboolean("DEFAULT", "embed_art")
        no_cover = config.getboolean("DEFAULT", "no_cover")
        no_database = config.getboolean("DEFAULT", "no_database")
        app_id = config["DEFAULT"]["app_id"]
        private_key = config["DEFAULT"].get("private_key", "")
        smart_discography = config.getboolean("DEFAULT", "smart_discography")
        folder_format = config["DEFAULT"]["folder_format"]
        track_format = config["DEFAULT"]["track_format"]

        secrets = [
            secret for secret in config["DEFAULT"]["secrets"].split(",") if secret
        ]
        arguments = qobuz_dl_args(
            default_quality, default_limit, default_folder
        ).parse_args()
    except (KeyError, UnicodeDecodeError, configparser.Error) as error:
        arguments = qobuz_dl_args().parse_args()
        if not arguments.reset:
            sys.exit(
                f"{RED}Your config file is corrupted: {error}! "
                "Run 'qobuz-dl -r' to fix this."
            )

    if arguments.reset:
        _reset_config(CONFIG_FILE)
        config.read(CONFIG_FILE)
        app_id = config["DEFAULT"]["app_id"]
        private_key = config["DEFAULT"].get("private_key", "")
        secrets = [
            secret for secret in config["DEFAULT"]["secrets"].split(",") if secret
        ]
        qobuz = QobuzDL(
            config["DEFAULT"]["default_folder"],
            config["DEFAULT"]["default_quality"],
            downloads_db=None if config.getboolean("DEFAULT", "no_database") else QOBUZ_DB,
            folder_format=config["DEFAULT"]["folder_format"],
            track_format=config["DEFAULT"]["track_format"],
            smart_discography=config.getboolean("DEFAULT", "smart_discography"),
        )
        qobuz.app_id = app_id
        qobuz.secrets = secrets
        qobuz.private_key = private_key
        token_info = qobuz.handle_oauth_login(
            callback_url=getattr(arguments, "callback", None),
            bind_host=getattr(arguments, "host", "0.0.0.0"),
            bind_port=getattr(arguments, "port", 0),
        )
        if token_info:
            _save_token(CONFIG_FILE, token_info)
        sys.exit()

    if arguments.show_config:
        print(f"Configuation: {CONFIG_FILE}\nDatabase: {QOBUZ_DB}\n---")
        with open(CONFIG_FILE, "r") as f:
            print(f.read())
        sys.exit()

    if arguments.purge:
        try:
            os.remove(QOBUZ_DB)
        except FileNotFoundError:
            pass
        sys.exit(f"{GREEN}The database was deleted.")

    qobuz = QobuzDL(
        arguments.directory,
        arguments.quality,
        arguments.embed_art or embed_art,
        ignore_singles_eps=arguments.albums_only or albums_only,
        no_m3u_for_playlists=arguments.no_m3u or no_m3u,
        quality_fallback=not arguments.no_fallback or not no_fallback,
        cover_og_quality=arguments.og_cover or og_cover,
        no_cover=arguments.no_cover or no_cover,
        downloads_db=None if no_database or arguments.no_db else QOBUZ_DB,
        folder_format=arguments.folder_format or folder_format,
        track_format=arguments.track_format or track_format,
        smart_discography=arguments.smart_discography or smart_discography,
    )
    qobuz.app_id = app_id
    qobuz.secrets = secrets
    qobuz.private_key = private_key

    if arguments.command == "oauth":
        _handle_commands(qobuz, arguments)
        return

    if not user_auth_token:
        sys.exit(f"{RED}No Qobuz token found. Run 'qobuz-dl -r' to authenticate.")

    qobuz.initialize_client_with_token(user_auth_token, app_id, secrets, user_id or None)

    _handle_commands(qobuz, arguments)


if __name__ == "__main__":
    sys.exit(main())
