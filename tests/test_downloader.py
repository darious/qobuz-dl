import pytest

import qobuz_dl.downloader as downloader


def _download(tmp_path):
    return downloader.Download(
        client=None,
        item_id="track",
        path=str(tmp_path),
        quality=27,
    )


def _track_metadata():
    return {
        "id": "track",
        "title": "Broken Track",
        "track_number": 1,
        "maximum_bit_depth": 24,
        "maximum_sampling_rate": 96,
        "performer": {"name": "Artist"},
        "album": {"artist": {"name": "Artist"}},
    }


def test_download_failure_removes_tmp_file(monkeypatch, tmp_path):
    tmp_file = tmp_path / ".00.tmp"

    def fail_download(url, fname, desc):
        tmp_file.write_text("partial")
        raise ConnectionError("broken stream")

    monkeypatch.setattr(downloader, "tqdm_download", fail_download)

    with pytest.raises(ConnectionError, match="broken stream"):
        _download(tmp_path)._download_and_tag(
            str(tmp_path),
            0,
            {"url": "https://example.invalid/track.flac"},
            _track_metadata(),
            {},
            False,
            False,
        )

    assert not tmp_file.exists()


def test_tag_failure_removes_tmp_file_and_raises(monkeypatch, tmp_path):
    tmp_file = tmp_path / ".00.tmp"

    def successful_download(url, fname, desc):
        tmp_file.write_text("downloaded")

    def fail_tag(*args, **kwargs):
        raise RuntimeError("bad tags")

    monkeypatch.setattr(downloader, "tqdm_download", successful_download)
    monkeypatch.setattr(downloader.metadata, "tag_flac", fail_tag)

    with pytest.raises(RuntimeError, match="bad tags"):
        _download(tmp_path)._download_and_tag(
            str(tmp_path),
            0,
            {"url": "https://example.invalid/track.flac"},
            _track_metadata(),
            {},
            False,
            False,
        )

    assert not tmp_file.exists()


def test_successful_tag_leaves_no_tmp_file(monkeypatch, tmp_path):
    tmp_file = tmp_path / ".00.tmp"
    final_file = tmp_path / "01. Broken Track.flac"

    def successful_download(url, fname, desc):
        tmp_file.write_text("downloaded")

    def successful_tag(filename, root_dir, final_name, *args, **kwargs):
        assert filename == str(tmp_file)
        tmp_file.rename(final_name)

    monkeypatch.setattr(downloader, "tqdm_download", successful_download)
    monkeypatch.setattr(downloader.metadata, "tag_flac", successful_tag)

    _download(tmp_path)._download_and_tag(
        str(tmp_path),
        0,
        {"url": "https://example.invalid/track.flac"},
        _track_metadata(),
        {},
        False,
        False,
    )

    assert not tmp_file.exists()
    assert final_file.exists()


def _album_meta():
    return {
        "id": "album",
        "title": "Album",
        "streamable": True,
        "release_type": "album",
        "release_date_original": "2024-01-01",
        "artist": {"name": "Artist"},
        "image": {"large": "https://example.invalid/cover.jpg"},
        "tracks": {
            "items": [
                {
                    "id": "bad",
                    "title": "Bad Track",
                    "track_number": 1,
                    "media_number": 1,
                    "maximum_bit_depth": 24,
                    "maximum_sampling_rate": 96,
                    "performer": {"name": "Artist"},
                    "album": {"artist": {"name": "Artist"}},
                },
                {
                    "id": "good",
                    "title": "Good Track",
                    "track_number": 2,
                    "media_number": 1,
                    "maximum_bit_depth": 24,
                    "maximum_sampling_rate": 96,
                    "performer": {"name": "Artist"},
                    "album": {"artist": {"name": "Artist"}},
                },
            ]
        },
    }


class _AlbumClient:
    def get_album_meta(self, item_id):
        return _album_meta()

    def get_track_url(self, track_id, fmt_id):
        return {
            "url": "https://example.invalid/{}.flac".format(track_id),
            "bit_depth": 24,
            "sampling_rate": 96,
        }


def test_album_download_continues_then_reports_failures(monkeypatch, tmp_path):
    calls = []

    def download_or_fail(self, root_dir, tmp_count, track_url_dict, track_metadata, *args):
        calls.append(track_metadata["id"])
        if track_metadata["id"] == "bad":
            raise downloader.TransferError("broken stream")

    monkeypatch.setattr(downloader.Download, "_download_and_tag", download_or_fail)

    dload = downloader.Download(
        client=_AlbumClient(),
        item_id="album",
        path=str(tmp_path),
        quality=27,
        no_cover=True,
    )

    with pytest.raises(downloader.DownloadSummaryError) as exc:
        dload.download_release()

    assert calls == ["bad", "good"]
    assert len(exc.value.failures) == 1
    assert exc.value.failures[0].track_id == "bad"


def test_transfer_failure_falls_back_to_next_quality(monkeypatch, tmp_path):
    qualities = []

    class Client:
        def get_track_url(self, track_id, fmt_id):
            return {
                "url": "https://example.invalid/{}.flac".format(fmt_id),
                "bit_depth": 24 if fmt_id != 6 else 16,
                "sampling_rate": 96 if fmt_id != 6 else 44.1,
            }

    def download_or_fail(self, root_dir, tmp_count, track_url_dict, track_metadata, *args):
        quality = int(track_url_dict["url"].rsplit("/", 1)[1].split(".", 1)[0])
        qualities.append(quality)
        if quality == 27:
            raise downloader.TransferError("broken stream")

    monkeypatch.setattr(downloader.Download, "_download_and_tag", download_or_fail)

    dload = downloader.Download(
        client=Client(),
        item_id="track",
        path=str(tmp_path),
        quality=27,
        downgrade_quality=True,
    )
    dload._download_with_fallback(
        str(tmp_path),
        1,
        _track_metadata(),
        _track_metadata(),
        True,
    )

    assert qualities == [27, 7]


def test_tqdm_download_retries_with_timeout(monkeypatch, tmp_path):
    calls = []

    class Response:
        def __init__(self, chunks):
            self.chunks = chunks
            self.headers = {"content-length": "4"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            return iter(self.chunks)

        def close(self):
            return None

    def fake_get(url, allow_redirects, stream, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            return Response([b"ab"])
        return Response([b"abcd"])

    monkeypatch.setattr(downloader.requests, "get", fake_get)
    monkeypatch.setattr(downloader.time, "sleep", lambda seconds: None)

    target = tmp_path / "track.tmp"
    downloader.tqdm_download(
        "https://example.invalid/track.flac",
        str(target),
        "track",
        timeout=12,
        retries=2,
        backoff=0,
    )

    assert calls == [12, 12]
    assert target.read_bytes() == b"abcd"
