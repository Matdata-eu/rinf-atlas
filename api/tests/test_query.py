"""Query state -> SQL: a plain filter on a facet of the tracks also narrows sections of line."""
from app.query import QueryState, Where

OWNERS = {"etcs.etcsLevelType": {"running-track", "track"}, "corridor": {"running-track", "track", "section-of-line"},
          "country": {"*"}}


def build(classes, filters):
    sql, params = Where(QueryState(env="prod", classes=classes, filters=filters), OWNERS).build()
    assert sql.count("%s") == len(params)
    return sql, params


def test_track_filter_narrows_sections_through_their_tracks():
    sql, params = build(["section-of-line", "running-track"], {"etcs.etcsLevelType": ["x"]})
    assert "EXISTS (SELECT 1 FROM feature_link l" in sql and "l.rule = 'from'" in sql
    assert ["running-track", "section-of-line", "track"] in params  # every class it constrains
    assert ["running-track", "track"] in params                    # the parts it is read from


def test_no_rollup_when_the_section_owns_the_facet_or_is_not_selected():
    for classes, filters in ((["section-of-line"], {"corridor": ["x"]}),
                             (["running-track"], {"etcs.etcsLevelType": ["x"]}),
                             (["section-of-line"], {"country": ["x"]})):
        assert "EXISTS" not in build(classes, filters)[0]


def test_class_scoped_filter_stays_on_its_class():
    sql, _ = build(["section-of-line"], {"running-track/etcs.etcsLevelType": ["x"]})
    assert "EXISTS" not in sql


def test_range_filter_rolls_up_too():
    sql, params = build([], {"etcs.etcsLevelType": {"min": 1}})
    assert sql.count("@?") == 2
