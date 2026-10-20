import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("TEST_DATABASE_URL", "sqlite:///:memory:")
