import shutil

import pytest

from instapost import cli, scriptures
from instapost.config import ROOT, get_settings


@pytest.fixture
def data(tmp_path, monkeypatch):
    shutil.copytree(ROOT / "data" / "scriptures", tmp_path / "data" / "scriptures")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    scriptures.load_verses.cache_clear()
    yield tmp_path / "data" / "scriptures"
    scriptures.load_verses.cache_clear()


def test_set_status_changes_only_that_entry_and_keeps_comments(data):
    before = (data / "gita.yaml").read_text(encoding="utf-8")
    scriptures.set_status("gita-2.47", "reviewed")
    after = (data / "gita.yaml").read_text(encoding="utf-8")
    v = scriptures.load_verses()
    assert v["gita-2.47"].reviewed and not v["gita-2.48"].reviewed
    assert after.startswith(before.split("entries:")[0])  # header comments kept
    assert after.count("reviewed_on:") == 1
    scriptures.set_status("gita-2.47", "reviewed")  # again: still one reviewed_on line
    assert (data / "gita.yaml").read_text(encoding="utf-8").count("reviewed_on:") == 1
    scriptures.set_status("gita-2.47", "pending")
    assert (data / "gita.yaml").read_text(encoding="utf-8") == before
    scriptures.set_status("gita-18.66", "reviewed")  # last entry in the file
    assert scriptures.load_verses()["gita-18.66"].reviewed


def test_rejected_verse_is_never_offered_to_the_writer(data):
    scriptures.set_status("kabir-kaal-kare", "rejected")
    assert "kabir-kaal-kare" not in scriptures.catalogue_for_prompt()
    assert "kabir-dheere-dheere" in scriptures.catalogue_for_prompt()


def test_interactive_review(data, monkeypatch, capsys):
    answers = iter(["c", "w", "x", "s", "q"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert cli.main(["verses", "review"]) == 0
    v = scriptures.load_verses()
    assert v["gita-2.14"].reviewed
    assert v["gita-2.47"].rejected
    assert v["gita-2.48"].review_status == "pending"  # skipped
    out = capsys.readouterr().out
    assert "1/17 reviewed." in out and "instapost verses set gita-2.47 reviewed" in out
    assert cli.main(["verses", "set", "gita-2.47", "reviewed"]) == 0
    assert scriptures.load_verses()["gita-2.47"].reviewed
