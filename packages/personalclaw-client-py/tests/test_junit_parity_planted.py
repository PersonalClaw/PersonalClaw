"""Planted for #3920's JUnit comparison: one failing test, read locally and on CI. Never merged."""


def test_a_planted_failure_reads_the_same_here_and_on_ci():
    assert "planted" == "failure", "planted for #3920's local-and-CI JUnit comparison"
