import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

from dataclasses import replace
import pytest
from offshore_metocean.data import Dataset, demo_datasets
from offshore_metocean.models import Project


@pytest.fixture(scope="session")
def demo():
    return demo_datasets()


@pytest.fixture
def cfg():
    return Project(bootstrap_samples=20)


@pytest.fixture
def known(demo):
    # Test fixtures only: do not distribute these generated values as real site evidence.
    return tuple(Dataset(d.frame.copy(), replace(d.meta, synthetic=False,
        site_assessment="TEST FIXTURE: known co-located comparison, not physical validation"), d.provenance)
        for d in demo)
