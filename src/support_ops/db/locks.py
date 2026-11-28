"""PostgreSQL advisory-lock keys shared by application tooling and the test harness.

Integration-test sessions hold ``TEST_DATABASE_LOCK_KEY`` *shared*; destructive tests and the
test-target synthetic seed/reset take it *exclusively*, so they never overlap a test session.
"""

from typing import Final

TEST_DATABASE_LOCK_KEY: Final = 7_233_301_550_001
DEVELOPMENT_SEED_LOCK_KEY: Final = 7_233_301_550_002
