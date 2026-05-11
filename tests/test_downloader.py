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
