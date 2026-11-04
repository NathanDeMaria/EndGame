from .main import _build_params


def test_build_params_covers_both_leagues_every_year() -> None:
    params = _build_params()

    assert len(params) == 2 * 16
    assert params[0] == ("mens", 2010)
    assert ("womens", 2025) in params
    assert len(set(params)) == len(params)
