from .spread import tidy_name


def test_tidy__keeps_at():
    tidied = tidy_name("At Cincinnati")
    assert tidied == "At Cincinnati"


def test_tidy__drops_at_london_parentheses():
    tidied = tidy_name("LA Rams (At London)")
    assert tidied == "LA Rams"


def test_tidy__drops_at_london_parentheses_lowercase():
    tidied = tidy_name("LA Rams (at London)")
    assert tidied == "LA Rams"


def test_tidy__drops_at_london():
    tidied = tidy_name("LA Rams At London")
    assert tidied == "LA Rams"


def test_tidy__drops_wembley():
    tidied = tidy_name("Cincinnati (At Wembley)")
    assert tidied == "Cincinnati"


def test_tidy__drops_toronto():
    tidied = tidy_name("Buffalo (Toronto)")
    assert tidied == "Buffalo"


def test_tidy__drops_mexico():
    tidied = tidy_name("LA Rams (Mexico City)")
    assert tidied == "LA Rams"


def test_tidy__handles_buffalo_detroit():
    tidied = tidy_name("At Buffalo (Detroit)")
    assert tidied == "At Buffalo"
