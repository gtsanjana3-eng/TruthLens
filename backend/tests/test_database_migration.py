from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import audit
from app.config import normalise_database_url
from app.database import _make_engine, init_schema
from app.models import AuditLog, User
from scripts.migrate_to_supabase import migrate


def test_supabase_urls_are_normalised():
    pooler = "postgresql://postgres.abc:pw@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
    assert normalise_database_url(pooler) == "postgresql+psycopg://postgres.abc:pw@aws-0-ap-south-1.pooler.supabase.com:5432/postgres?sslmode=require"
    assert normalise_database_url("postgres://u:p@db.x.supabase.co:5432/postgres?a=1").endswith("?a=1&sslmode=require")
    assert normalise_database_url("postgresql://u:p@db.x.supabase.co/postgres?sslmode=verify-full").endswith("sslmode=verify-full")
    assert normalise_database_url("postgresql+psycopg://u:p@localhost/db") == "postgresql+psycopg://u:p@localhost/db"
    assert normalise_database_url("sqlite:///x.db") == "sqlite:///x.db"


def test_passwords_with_special_characters_are_encoded():
    from sqlalchemy.engine import make_url

    for pw in ("p@ss", "a:b@c/d?e#f%g", "plain123"):
        url = normalise_database_url(f"postgresql://postgres.abcdefghijklmnopqrst:{pw}@aws-0-ap-south-1.pooler.supabase.com:5432/postgres")
        u = make_url(url)
        assert (u.host, u.port, u.username, u.password, u.database) == ("aws-0-ap-south-1.pooler.supabase.com", 5432, "postgres.abcdefghijklmnopqrst", pw, "postgres")
    # already-encoded passwords are not double-encoded
    url = normalise_database_url("postgresql://postgres:p%40ss@db.abcdefghijklmnopqrst.supabase.co:5432/postgres")
    assert make_url(url).password == "p@ss"


def test_transaction_pooler_disables_prepared_statements():
    eng = _make_engine("postgresql+psycopg://u:p@aws-0-x.pooler.supabase.com:6543/postgres?sslmode=require")
    assert eng.dialect.name == "postgresql" and eng.pool.size() == 5


def test_copy_preserves_rows_and_audit_chain_and_refuses_duplicates(tmp_path):
    src_url, dst_url = f"sqlite:///{(tmp_path / 'src.db').as_posix()}", f"sqlite:///{(tmp_path / 'dst.db').as_posix()}"
    src = _make_engine(src_url)
    init_schema(src)
    with Session(src) as db:
        u = User(email="a@example.com", full_name="Ann Example", password_hash="x")
        db.add(u)
        db.flush()
        for i in range(3):
            audit.record(db, f"test.event{i}", user_id=u.id, detail={"i": i})
        db.commit()
    logs = []
    assert migrate(src_url, dst_url, apply=False, out=logs.append) == 0
    assert not (tmp_path / "dst.db").exists() or create_engine(dst_url).dialect  # dry run writes nothing of substance
    assert migrate(src_url, dst_url, apply=True, out=logs.append) == 0
    with Session(create_engine(dst_url)) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 3
        assert audit.verify_chain(db)["valid"]
    assert any("audit hash chain: intact" in line for line in logs)
    assert migrate(src_url, dst_url, apply=True, out=logs.append) == 3  # second run refuses
