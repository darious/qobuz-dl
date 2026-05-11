import logging
import os
import time
from typing import Tuple

import requests
from pathvalidate import sanitize_filename, sanitize_filepath
from tqdm import tqdm

import qobuz_dl.metadata as metadata
from qobuz_dl.color import OFF, GREEN, RED, YELLOW, CYAN
from qobuz_dl.exceptions import NonStreamable

QL_DOWNGRADE = "FormatRestrictedByFormatAvailability"
DOWNLOAD_TIMEOUT = 30
DOWNLOAD_RETRIES = 3
DOWNLOAD_BACKOFF_SECONDS = 2
QUALITY_FALLBACKS = {
    27: [27, 7, 6],
    7: [7, 6],
    6: [6],
    5: [5],
}
# used in case of error
DEFAULT_FORMATS = {
    "MP3": [
        "{artist} - {album} ({year}) [MP3]",
        "{tracknumber}. {tracktitle}",
    ],
    "Unknown": [
        "{artist} - {album}",
        "{tracknumber}. {tracktitle}",
    ],
}

DEFAULT_FOLDER = "{artist} - {album} ({year}) [{bit_depth}B-{sampling_rate}kHz]"
DEFAULT_TRACK = "{tracknumber}. {tracktitle}"

logger = logging.getLogger(__name__)


class TransferError(RuntimeError):
    """Raised when a stream transfer fails after retry attempts."""


class DownloadError(RuntimeError):
    def __init__(self, track_id, track_title, quality, stage, cause):
        self.track_id = track_id
        self.track_title = track_title
        self.quality = quality
        self.stage = stage
        self.cause = cause
        super().__init__(
            "{} failed at quality {} during {}: {}".format(
                track_title,
                quality,
                stage,
                cause,
            )
        )


class DownloadSummaryError(RuntimeError):
    def __init__(self, release_title, failures):
        self.release_title = release_title
        self.failures = failures
        super().__init__(
            "{} failed to download {} track(s)".format(
                release_title,
                len(failures),
            )
        )


class Download:
    def __init__(
        self,
        client,
        item_id: str,
        path: str,
        quality: int,
        embed_art: bool = False,
        albums_only: bool = False,
        downgrade_quality: bool = False,
        cover_og_quality: bool = False,
        no_cover: bool = False,
        folder_format=None,
        track_format=None,
    ):
        self.client = client
        self.item_id = item_id
        self.path = path
        self.quality = quality
        self.albums_only = albums_only
        self.embed_art = embed_art
        self.downgrade_quality = downgrade_quality
        self.cover_og_quality = cover_og_quality
        self.no_cover = no_cover
        self.folder_format = folder_format or DEFAULT_FOLDER
        self.track_format = track_format or DEFAULT_TRACK

    def download_id_by_type(self, track=True):
        if not track:
            self.download_release()
        else:
            self.download_track()

    def download_release(self):
        count = 0
        meta = self.client.get_album_meta(self.item_id)

        if not meta.get("streamable"):
            raise NonStreamable("This release is not streamable")

        if self.albums_only and (
            meta.get("release_type") != "album"
            or meta.get("artist").get("name") == "Various Artists"
        ):
            logger.info(f'{OFF}Ignoring Single/EP/VA: {meta.get("title", "n/a")}')
            return

        album_title = _get_title(meta)

        format_info = self._get_format(meta)
        file_format, quality_met, bit_depth, sampling_rate = format_info

        if not self.downgrade_quality and not quality_met:
            logger.info(
                f"{OFF}Skipping {album_title} as it doesn't meet quality requirement"
            )
            return

        logger.info(
            f"\n{YELLOW}Downloading: {album_title}\nQuality: {file_format}"
            f" ({bit_depth}/{sampling_rate})\n"
        )
        album_attr = self._get_album_attr(
            meta, album_title, file_format, bit_depth, sampling_rate
        )
        folder_format, track_format = _clean_format_str(
            self.folder_format, self.track_format, file_format
        )
        sanitized_title = sanitize_filepath(folder_format.format(**album_attr))
        dirn = os.path.join(self.path, sanitized_title)
        os.makedirs(dirn, exist_ok=True)

        if self.no_cover:
            logger.info(f"{OFF}Skipping cover")
        else:
            _get_extra(meta["image"]["large"], dirn, og_quality=self.cover_og_quality)

        if "goodies" in meta:
            try:
                _get_extra(meta["goodies"][0]["url"], dirn, "booklet.pdf")
            except:  # noqa
                pass
        media_numbers = [track["media_number"] for track in meta["tracks"]["items"]]
        is_multiple = True if len([*{*media_numbers}]) > 1 else False
        failures = []
        for i in meta["tracks"]["items"]:
            try:
                self._download_with_fallback(
                    dirn,
                    count,
                    i,
                    meta,
                    False,
                    i["media_number"] if is_multiple else None,
                )
            except DownloadError as exc:
                failures.append(exc)
                logger.error(f"{RED}{exc}. Continuing with remaining tracks...")
            count = count + 1

        if failures:
            logger.error(
                f"{RED}Completed with {len(failures)}/{len(meta['tracks']['items'])} "
                "track failure(s):"
            )
            for failure in failures:
                logger.error(
                    "{}- #{} {} ({}, quality {}): {}".format(
                        RED,
                        failure.track_id,
                        failure.track_title,
                        failure.stage,
                        failure.quality,
                        failure.cause,
                    )
                )
            raise DownloadSummaryError(album_title, failures)

        logger.info(f"{GREEN}Completed")

    def download_track(self):
        parse = self.client.get_track_url(self.item_id, self.quality)

        if "sample" not in parse and parse["sampling_rate"]:
            meta = self.client.get_track_meta(self.item_id)
            track_title = _get_title(meta)
            artist = _safe_get(meta, "performer", "name")
            logger.info(f"\n{YELLOW}Downloading: {artist} - {track_title}")
            format_info = self._get_format(meta, is_track_id=True, track_url_dict=parse)
            file_format, quality_met, bit_depth, sampling_rate = format_info

            folder_format, track_format = _clean_format_str(
                self.folder_format, self.track_format, str(bit_depth)
            )

            if not self.downgrade_quality and not quality_met:
                logger.info(
                    f"{OFF}Skipping {track_title} as it doesn't "
                    "meet quality requirement"
                )
                return
            track_attr = self._get_track_attr(
                meta, track_title, bit_depth, sampling_rate
            )
            sanitized_title = sanitize_filepath(folder_format.format(**track_attr))

            dirn = os.path.join(self.path, sanitized_title)
            os.makedirs(dirn, exist_ok=True)
            if self.no_cover:
                logger.info(f"{OFF}Skipping cover")
            else:
                _get_extra(
                    meta["album"]["image"]["large"],
                    dirn,
                    og_quality=self.cover_og_quality,
                )
            self._download_with_fallback(
                dirn,
                1,
                meta,
                meta,
                True,
                False,
            )
        else:
            logger.info(f"{OFF}Demo. Skipping")
        logger.info(f"{GREEN}Completed")

    def _download_and_tag(
        self,
        root_dir,
        tmp_count,
        track_url_dict,
        track_metadata,
        album_or_track_metadata,
        is_track,
        is_mp3,
        multiple=None,
    ):
        extension = ".mp3" if is_mp3 else ".flac"

        try:
            url = track_url_dict["url"]
        except KeyError:
            logger.info(f"{OFF}Track not available for download")
            return

        if multiple:
            root_dir = os.path.join(root_dir, f"Disc {multiple}")
            os.makedirs(root_dir, exist_ok=True)

        filename = os.path.join(root_dir, f".{tmp_count:02}.tmp")

        # Determine the filename
        track_title = track_metadata.get("title")
        artist = _safe_get(track_metadata, "performer", "name")
        filename_attr = self._get_filename_attr(artist, track_metadata, track_title)

        # track_format is a format string
        # e.g. '{tracknumber}. {artist} - {tracktitle}'
        formatted_path = sanitize_filename(self.track_format.format(**filename_attr))
        final_file = os.path.join(root_dir, formatted_path)[:250] + extension

        if os.path.isfile(final_file):
            logger.info(f"{OFF}{track_title} was already downloaded")
            return

        try:
            tqdm_download(url, filename, filename)
        except Exception:
            _remove_partial_file(filename)
            raise

        tag_function = metadata.tag_mp3 if is_mp3 else metadata.tag_flac
        try:
            tag_function(
                filename,
                root_dir,
                final_file,
                track_metadata,
                album_or_track_metadata,
                is_track,
                self.embed_art,
            )
        except Exception as e:
            logger.error(f"{RED}Error tagging the file: {e}", exc_info=True)
            _remove_partial_file(filename)
            raise

    def _download_with_fallback(
        self,
        root_dir,
        tmp_count,
        track_metadata,
        album_or_track_metadata,
        is_track,
        multiple=None,
    ):
        track_id = track_metadata.get("id", self.item_id)
        track_title = track_metadata.get("title")
        qualities = self._quality_attempts()
        last_error = None
        seen_streams = set()

        for index, quality in enumerate(qualities):
            try:
                track_url = self.client.get_track_url(track_id, fmt_id=quality)
            except Exception as exc:
                raise DownloadError(track_id, track_title, quality, "stream lookup", exc)

            if "sample" in track_url or not track_url.get("sampling_rate"):
                logger.info(f"{OFF}Demo. Skipping")
                return

            stream_key = (
                track_url.get("url"),
                track_url.get("bit_depth"),
                track_url.get("sampling_rate"),
            )
            if stream_key in seen_streams:
                continue
            seen_streams.add(stream_key)

            is_mp3 = True if int(quality) == 5 else False
            try:
                self._download_and_tag(
                    root_dir,
                    tmp_count,
                    track_url,
                    track_metadata,
                    album_or_track_metadata,
                    is_track,
                    is_mp3,
                    multiple,
                )
                if quality != int(self.quality):
                    logger.warning(
                        f"{YELLOW}{track_title} downloaded at fallback quality "
                        f"{quality} after quality {self.quality} failed"
                    )
                return
            except TransferError as exc:
                last_error = exc
                has_fallback = self.downgrade_quality and index < len(qualities) - 1
                if has_fallback:
                    logger.warning(
                        f"{YELLOW}{track_title} failed at quality {quality}: {exc}. "
                        "Trying next available quality..."
                    )
                    continue
                raise DownloadError(track_id, track_title, quality, "download", exc)
            except Exception as exc:
                raise DownloadError(track_id, track_title, quality, "tagging", exc)

        if last_error:
            raise DownloadError(
                track_id,
                track_title,
                qualities[-1],
                "download",
                last_error,
            )
        raise DownloadError(track_id, track_title, self.quality, "download", "no stream")

    def _quality_attempts(self):
        quality = int(self.quality)
        if not self.downgrade_quality:
            return [quality]
        return QUALITY_FALLBACKS.get(quality, [quality])

    @staticmethod
    def _get_filename_attr(artist, track_metadata, track_title):
        return {
            "artist": artist,
            "albumartist": _safe_get(
                track_metadata, "album", "artist", "name", default=artist
            ),
            "bit_depth": track_metadata["maximum_bit_depth"],
            "sampling_rate": track_metadata["maximum_sampling_rate"],
            "tracktitle": track_title,
            "version": track_metadata.get("version"),
            "tracknumber": f"{track_metadata['track_number']:02}",
        }

    @staticmethod
    def _get_track_attr(meta, track_title, bit_depth, sampling_rate):
        return {
            "album": sanitize_filename(meta["album"]["title"]),
            "artist": sanitize_filename(meta["album"]["artist"]["name"]),
            "tracktitle": track_title,
            "year": meta["album"]["release_date_original"].split("-")[0],
            "bit_depth": bit_depth,
            "sampling_rate": sampling_rate,
        }

    @staticmethod
    def _get_album_attr(meta, album_title, file_format, bit_depth, sampling_rate):
        return {
            "artist": sanitize_filename(meta["artist"]["name"]),
            "album": sanitize_filename(album_title),
            "year": meta["release_date_original"].split("-")[0],
            "format": file_format,
            "bit_depth": bit_depth,
            "sampling_rate": sampling_rate,
        }

    def _get_format(self, item_dict, is_track_id=False, track_url_dict=None):
        quality_met = True
        if int(self.quality) == 5:
            return ("MP3", quality_met, None, None)
        track_dict = item_dict
        if not is_track_id:
            track_dict = item_dict["tracks"]["items"][0]

        try:
            new_track_dict = (
                self.client.get_track_url(track_dict["id"], fmt_id=self.quality)
                if not track_url_dict
                else track_url_dict
            )
            restrictions = new_track_dict.get("restrictions")
            if isinstance(restrictions, list):
                if any(
                    restriction.get("code") == QL_DOWNGRADE
                    for restriction in restrictions
                ):
                    quality_met = False

            return (
                "FLAC",
                quality_met,
                new_track_dict["bit_depth"],
                new_track_dict["sampling_rate"],
            )
        except (KeyError, requests.exceptions.HTTPError):
            return ("Unknown", quality_met, None, None)


def tqdm_download(
    url,
    fname,
    desc,
    timeout=DOWNLOAD_TIMEOUT,
    retries=DOWNLOAD_RETRIES,
    backoff=DOWNLOAD_BACKOFF_SECONDS,
):
    last_error = None
    for attempt in range(1, retries + 1):
        download_size = 0
        response = None
        try:
            response = requests.get(
                url,
                allow_redirects=True,
                stream=True,
                timeout=timeout,
            )
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            with open(fname, "wb") as file, tqdm(
                total=total,
                unit="iB",
                unit_scale=True,
                unit_divisor=1024,
                desc=desc,
                bar_format=CYAN + "{n_fmt}/{total_fmt} /// {desc}",
            ) as bar:
                for data in response.iter_content(chunk_size=1024):
                    size = file.write(data)
                    bar.update(size)
                    download_size += size

            if total and total != download_size:
                # requests may stop iterating without surfacing a stream error.
                raise TransferError("File download was interrupted for " + fname)
            return
        except Exception as exc:
            last_error = exc
            _remove_partial_file(fname)
            if attempt >= retries:
                break
            logger.warning(
                "%sDownload attempt %s/%s failed for %s: %s",
                YELLOW,
                attempt,
                retries,
                desc,
                exc,
            )
            time.sleep(backoff * (2 ** (attempt - 1)))
        finally:
            if response is not None:
                response.close()

    raise TransferError(
        "download failed after {} attempt(s): {}".format(retries, last_error)
    )


def _remove_partial_file(path):
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError as exc:
        logger.warning("Failed to remove partial file %s: %s", path, exc)


def _get_description(item: dict, track_title, multiple=None):
    downloading_title = f"{track_title} "
    f'[{item["bit_depth"]}/{item["sampling_rate"]}]'
    if multiple:
        downloading_title = f"[Disc {multiple}] {downloading_title}"
    return downloading_title


def _get_title(item_dict):
    album_title = item_dict["title"]
    version = item_dict.get("version")
    if version:
        album_title = (
            f"{album_title} ({version})"
            if version.lower() not in album_title.lower()
            else album_title
        )
    return album_title


def _get_extra(item, dirn, extra="cover.jpg", og_quality=False):
    extra_file = os.path.join(dirn, extra)
    if os.path.isfile(extra_file):
        logger.info(f"{OFF}{extra} was already downloaded")
        return
    tqdm_download(
        item.replace("_600.", "_org.") if og_quality else item,
        extra_file,
        extra,
    )


def _clean_format_str(folder: str, track: str, file_format: str) -> Tuple[str, str]:
    """Cleans up the format strings, avoids errors
    with MP3 files.
    """
    final = []
    for i, fs in enumerate((folder, track)):
        if fs.endswith(".mp3"):
            fs = fs[:-4]
        elif fs.endswith(".flac"):
            fs = fs[:-5]
        fs = fs.strip()

        # default to pre-chosen string if format is invalid
        if file_format in ("MP3", "Unknown") and (
            "bit_depth" in fs or "sampling_rate" in fs
        ):
            default = DEFAULT_FORMATS[file_format][i]
            logger.error(
                f"{RED}invalid format string for format {file_format}"
                f". defaulting to {default}"
            )
            fs = default
        final.append(fs)

    return tuple(final)


def _safe_get(d: dict, *keys, default=None):
    """A replacement for chained `get()` statements on dicts:
    >>> d = {'foo': {'bar': 'baz'}}
    >>> _safe_get(d, 'baz')
    None
    >>> _safe_get(d, 'foo', 'bar')
    'baz'
    """
    curr = d
    res = default
    for key in keys:
        res = curr.get(key, default)
        if res == default or not hasattr(res, "__getitem__"):
            return res
        else:
            curr = res
    return res
