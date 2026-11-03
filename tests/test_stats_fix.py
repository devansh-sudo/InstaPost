from instapost.stats import fetch


def _get_factory(answers):
    def get(path, metric, **kw):
        result = answers(metric)
        if isinstance(result, Exception):
            raise result
        return {"data": [{"name": m, "values": [{"value": result}]} for m in metric.split(",")]}
    return get


def test_a_temporary_refusal_is_not_remembered():
    refused = fetch.FetchError("Facebook 400: Object with ID '1' does not exist, cannot be "
                               "loaded due to missing permissions")
    out = fetch._insights(_get_factory(lambda m: refused), "v/video_insights",
                          ["blue_reels_play_count", "post_impressions_unique"], "k:test")
    assert "_error" in out and "does not exist" in out["_error"]
    assert fetch._unsupported("k:test") == set()  # asked again at the next checkpoint
    later = fetch._insights(_get_factory(lambda m: 42), "v/video_insights",
                            ["blue_reels_play_count", "post_impressions_unique"], "k:test")
    assert later == {"blue_reels_play_count": 42, "post_impressions_unique": 42}


def test_an_invalid_metric_is_remembered_and_the_rest_still_come():
    def answers(metric):
        if "," in metric or metric == "old_metric":
            return fetch.FetchError("Instagram 400: (#100) metric[0] must be one of the "
                                    "following values: views, reach")
        return 7

    out = fetch._insights(_get_factory(answers), "m/insights", ["views", "old_metric"], "k:ig")
    assert out == {"views": 7}
    assert fetch._unsupported("k:ig") == {"old_metric"}


def test_few_viewers_story_is_not_switched_off():
    few = fetch.FetchError("Instagram 400: (#10) Not enough viewers for the media to show "
                           "insights")
    out = fetch._insights(_get_factory(lambda m: few), "s/insights", ["views"], "k:story")
    assert "_error" in out and fetch._unsupported("k:story") == set()
