import pytest

from support_ops.synthetic.dataset import PreparedDataset, prepare_dataset


@pytest.fixture(scope="session")
def prepared() -> PreparedDataset:
    """The default-seed dataset (built and validated once per test session)."""
    return prepare_dataset()
