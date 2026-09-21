import pytest

from minglecraft.bwapi.synthetic import SyntheticGame


@pytest.fixture
def observation():
    return SyntheticGame("test_match").observe()
