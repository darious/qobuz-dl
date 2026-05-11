import pytest

from qobuz_dl import core, downloader


def test_failed_download_is_not_added_to_downloads_db(monkeypatch, tmp_path):
    calls = []

    def fake_handle_download_id(db_path, item_id, add_id=False):
        calls.append((item_id, add_id))
        return None

    def fail_download(self, track=True):
        raise downloader.DownloadSummaryError("Album", [])

    monkeypatch.setattr(core, "handle_download_id", fake_handle_download_id)
    monkeypatch.setattr(downloader.Download, "download_id_by_type", fail_download)

    qobuz = core.QobuzDL(
        directory=str(tmp_path),
        downloads_db=str(tmp_path / "downloads.db"),
    )
    qobuz.client = object()

    with pytest.raises(downloader.DownloadSummaryError):
        qobuz.download_from_id("album-id")

    assert calls == [("album-id", False)]
