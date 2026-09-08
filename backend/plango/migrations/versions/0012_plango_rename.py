"""Rename owned storage in place, retaining every command and approval binding."""

import sqlalchemy as sa
from alembic import op

revision = "0012_plango_rename"
down_revision = "0011_location_context"
branch_labels = None
depends_on = None


def rename_owned_storage(source, target):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    postgres = bind.dialect.name == "postgresql"
    objects = {row[0]: row[1] for row in bind.execute(sa.text(
        "SELECT relname, relkind FROM pg_class WHERE relnamespace = current_schema()::regnamespace"
        if postgres else "SELECT name, type FROM sqlite_master"
    ))}
    table_names = set(inspector.get_table_names())
    tables = []
    destinations = set()

    def reserve_destination(name):
        if name in objects or name in destinations:
            raise RuntimeError(f"rename target already exists: {name}; reconcile before migration")
        destinations.add(name)

    # Inspect the complete operation before touching any table, including secondary indexes.
    for suffix in ("browser_binding", "browser_command", "reminder"):
        old, new = f"{source}_{suffix}", f"{target}_{suffix}"
        if old not in table_names:
            raise RuntimeError(f"rename source table is missing: {old}")
        reserve_destination(new)
        constraints = [
            inspector.get_pk_constraint(old), *inspector.get_unique_constraints(old),
            *inspector.get_check_constraints(old), *inspector.get_foreign_keys(old),
        ]
        constraint_names = {item["name"] for item in constraints if item.get("name")}
        constraint_targets = set()
        renamed_constraints = []
        for constraint in constraints:
            name = constraint.get("name")
            if not name or source not in name:
                continue
            if not postgres:
                raise RuntimeError(f"SQLite named constraint requires explicit migration: {name}")
            renamed = name.replace(source, target)
            if renamed in constraint_names or renamed in constraint_targets:
                raise RuntimeError(f"rename constraint target already exists: {renamed}")
            constraint_targets.add(renamed)
            # Primary/unique constraints also rename their schema-wide backing indexes.
            if name in objects:
                reserve_destination(renamed)
            renamed_constraints.append((name, renamed))
        indexes = []
        reflected_indexes = inspector.get_indexes(old)
        for index in reflected_indexes:
            name = index["name"]
            if not name or source not in name or index.get("duplicates_constraint"):
                continue
            renamed = name.replace(source, target)
            reserve_destination(renamed)
            columns = [column for column in index["column_names"] if column is not None]
            if len(columns) != len(index["column_names"]):
                raise RuntimeError(f"unsupported expression index: {name}")
            indexes.append((name, renamed, columns, index["unique"], index.get("dialect_options", {})))
        if not postgres:
            # SQLAlchemy skips expression indexes; reject before dropping any unsupported index.
            reflected = {item["name"] for item in reflected_indexes}
            declared = set(bind.scalars(sa.text(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = :table AND sql IS NOT NULL"
            ), {"table": old}))
            if declared - reflected:
                raise RuntimeError(f"unreflected SQLite indexes on {old}; explicit migration required")
        tables.append((old, new, renamed_constraints, indexes))
    old_sequence, new_sequence = f"{source}_browser_command_seq_seq", f"{target}_browser_command_seq_seq"
    if postgres and old_sequence in objects:
        if objects[old_sequence] != "S":
            raise RuntimeError(f"rename source is not a sequence: {old_sequence}")
        reserve_destination(new_sequence)

    quote = bind.dialect.identifier_preparer.quote
    for old, new, constraint_renames, indexes in tables:
        op.rename_table(old, new)
        for name, renamed in constraint_renames:
            op.execute(sa.text(f"ALTER TABLE {quote(new)} RENAME CONSTRAINT {quote(name)} TO {quote(renamed)}"))
        for name, renamed, columns, unique, options in indexes:
            if postgres:
                op.execute(sa.text(f"ALTER INDEX {quote(name)} RENAME TO {quote(renamed)}"))
            else:
                op.drop_index(name, table_name=new)
                op.create_index(renamed, new, columns, unique=unique, **options)
    if postgres and old_sequence in objects:
        # ALTER TABLE preserves the serial sequence and its current value.
        op.execute(sa.text(f"ALTER SEQUENCE {quote(old_sequence)} RENAME TO {quote(new_sequence)}"))


def upgrade():
    rename_owned_storage("yoyu", "plango")


def downgrade():
    rename_owned_storage("plango", "yoyu")
