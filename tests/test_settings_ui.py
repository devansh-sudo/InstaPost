from starlette.datastructures import FormData

from instapost import agent_settings, overrides
from instapost.config import get_settings
from instapost.db import AuditLog, session
from instapost.schedule import load_schedule
from tests.test_phase2 import _csrf, _login, web  # noqa: F401


def _form(**changes) -> FormData:
    """The settings form as the page sends it, with some values changed."""
    items = []
    values = agent_settings.current_values()
    for f in agent_settings.FIELDS:
        v = changes.get(f.key, values[f.key])
        v = v.value if hasattr(v, "value") else v
        if f.kind == "bool":
            if v:
                items.append((f.name, "1"))
        elif f.kind == "platforms":
            items += [(f.name, p) for p in v]
        elif f.kind == "times":
            items.append((f.name, ", ".join(v)))
        else:
            items.append((f.name, str(v)))
    return FormData(items)


def test_dashboard_changes_win_over_the_files_and_reset_brings_them_back():
    assert load_schedule().max_posts_per_day["youtube"] == 2
    res = agent_settings.apply(_form(**{"schedule:max_posts_per_day.youtube": 4,
                                        "settings:writer_effort": "medium",
                                        "schedule:daily_plan.mon.reel_book_pov": 3,
                                        "schedule:targets.carousel_shlok": ["instagram",
                                                                            "facebook"]}))
    assert not res.errors
    assert load_schedule().max_posts_per_day["youtube"] == 4  # no restart needed
    assert get_settings().writer_effort == "medium"
    assert load_schedule().daily_plan["mon"]["reel_book_pov"] == 3
    assert load_schedule().daily_plan["tue"]["reel_book_pov"] == 1  # untouched days stay
    saved = overrides.load()
    # Only the differences from the files are stored.
    assert saved["settings"] == {"writer_effort": "medium"}
    assert saved["schedule"]["max_posts_per_day"] == {"youtube": 4}
    assert "instagram" not in saved["schedule"]["max_posts_per_day"]
    assert "schedule:max_posts_per_day.youtube" in agent_settings.changed_keys()
    agent_settings.apply(FormData(), reset=True)
    assert load_schedule().max_posts_per_day["youtube"] == 2
    assert get_settings().writer_effort == "low"


def test_bad_values_are_refused_and_nothing_is_saved():
    res = agent_settings.apply(_form(**{"schedule:generate_at": "25:00",
                                        "schedule:max_posts_per_day.youtube": 99}))
    assert len(res.errors) == 2 and overrides.load()["schedule"] == {}
    res = agent_settings.apply(_form(**{"settings:daily_budget_usd": 0}))
    assert res.errors and overrides.load()["settings"] == {}


def test_settings_page_saves_and_records_the_change(web):  # noqa: F811
    _login(web)
    page = web.get("/settings")
    assert page.status_code == 200 and "Agent settings" in page.text
    assert "ANTHROPIC_API_KEY" not in page.text.split('id="agent"')[1]  # no keys in the form
    token = _csrf(web, "/settings")
    form = _form(**{"schedule:max_posts_per_day.youtube": 5})
    data: dict = {"token": token}
    for k, v in form.multi_items():
        data.setdefault(k, []).append(v)
    r = web.post("/settings/agent", data=data, follow_redirects=False)
    assert r.status_code == 303
    assert load_schedule().max_posts_per_day["youtube"] == 5
    with session() as s:
        log = s.query(AuditLog).filter(AuditLog.action == "settings_changed").one()
        assert log.detail["changes"]["schedule:max_posts_per_day.youtube"] == ["2", "5"]
    assert "changed" in web.get("/settings").text
