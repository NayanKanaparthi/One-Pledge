"""Points 5 (columns) and 7 to 10 of the least-privilege check (SPEC_DECISIONS 103).

Each function runs fixed catalog queries for the connected role and returns its violations as
(code, object) pairs sorted in Python by code point (no query orders text);
`role_check.inspect_role` puts them in point order.
`aclexplode` grantee 0 is PUBLIC, and a privilege PUBLIC holds is a privilege the role holds.
A non-system schema is any but `pg_*` and `information_schema`.
"""

from __future__ import annotations

from sqlalchemy import Connection, TextClause, text

_COLUMNS = text("""
SELECT n.nspname || '.' || c.relname || '.' || a.attname || ' ' || x.privilege_type
FROM pg_attribute AS a
    JOIN pg_class AS c ON c.oid = a.attrelid
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(a.attacl) AS x
WHERE n.nspname = 'public' AND x.grantee IN (current_user::regrole, 0)
""")
_ROLE_SETTINGS = text("""
SELECT CASE WHEN s.setdatabase = 0 THEN 'in every database' ELSE 'in database ' || d.datname END
FROM pg_db_role_setting AS s LEFT JOIN pg_database AS d ON d.oid = s.setdatabase
WHERE s.setrole = current_user::regrole
""")
# A setting of session_replication_role for every role, in this database or in every one.
_DATABASE_REPLICA = text("""
SELECT CASE WHEN s.setdatabase = 0 THEN 'every database' ELSE 'database ' || d.datname END
    || ': session_replication_role'
FROM pg_db_role_setting AS s LEFT JOIN pg_database AS d ON d.oid = s.setdatabase
WHERE s.setrole = 0
    AND (s.setdatabase = 0 OR d.datname = current_database())
    AND EXISTS (SELECT 1 FROM unnest(s.setconfig) AS c(item) WHERE c.item LIKE 'session_replication_role=%')
""")
# Whether a setting of the role itself sets session_replication_role for this database.
_ROLE_REPLICA = text("""
SELECT EXISTS (
    SELECT 1 FROM pg_db_role_setting AS s LEFT JOIN pg_database AS d ON d.oid = s.setdatabase
    WHERE s.setrole = current_user::regrole
        AND (s.setdatabase = 0 OR d.datname = current_database())
        AND EXISTS (
            SELECT 1 FROM unnest(s.setconfig) AS c(item) WHERE c.item LIKE 'session_replication_role=%'
        )
)
""")
_PARAMETERS = text("""
SELECT p.parname FROM pg_parameter_acl AS p CROSS JOIN LATERAL aclexplode(p.paracl) AS x
WHERE x.grantee IN (current_user::regrole, 0)
GROUP BY p.parname
""")
_SESSION_REPLICA = text("SELECT current_setting('session_replication_role') COLLATE \"C\" <> 'origin'")
_DEFINERS = text("""
SELECT n.nspname || '.' || p.proname FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid = p.pronamespace
WHERE p.prosecdef AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
""")
_EXECUTABLE = text("""
SELECT n.nspname || '.' || p.proname FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid = p.pronamespace
WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
    AND has_function_privilege(current_user, p.oid, 'EXECUTE')
""")
_DEFAULTS = text("""
SELECT pg_get_userbyid(d.defaclrole) || ' ' || CASE d.defaclobjtype
    WHEN 'r' THEN 'tables' WHEN 'S' THEN 'sequences' WHEN 'f' THEN 'functions'
    WHEN 'T' THEN 'types' WHEN 'n' THEN 'schemas' ELSE 'objects' END
FROM pg_default_acl AS d CROSS JOIN LATERAL aclexplode(d.defaclacl) AS x
WHERE x.grantee IN (current_user::regrole, 0)
""")
# Every catalog with an access control list, each row one object the role may grant onwards.
_GRANT_OPTIONS = text("""
SELECT kind || ' ' || name || ' ' || privilege FROM (
    SELECT 'table' AS kind, n.nspname || '.' || c.relname AS name, x.privilege_type AS privilege,
        x.grantee, x.is_grantable
    FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(c.relacl) AS x
    UNION ALL SELECT 'column', n.nspname || '.' || c.relname || '.' || a.attname, x.privilege_type,
        x.grantee, x.is_grantable
    FROM pg_attribute AS a JOIN pg_class AS c ON c.oid = a.attrelid
        JOIN pg_namespace AS n ON n.oid = c.relnamespace CROSS JOIN LATERAL aclexplode(a.attacl) AS x
    UNION ALL SELECT 'schema', n.nspname, x.privilege_type, x.grantee, x.is_grantable
    FROM pg_namespace AS n CROSS JOIN LATERAL aclexplode(n.nspacl) AS x
    UNION ALL SELECT 'database', d.datname, x.privilege_type, x.grantee, x.is_grantable
    FROM pg_database AS d CROSS JOIN LATERAL aclexplode(d.datacl) AS x
    UNION ALL SELECT 'function', n.nspname || '.' || p.proname, x.privilege_type, x.grantee, x.is_grantable
    FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid = p.pronamespace
        CROSS JOIN LATERAL aclexplode(p.proacl) AS x
    UNION ALL SELECT 'type', n.nspname || '.' || t.typname, x.privilege_type, x.grantee, x.is_grantable
    FROM pg_type AS t JOIN pg_namespace AS n ON n.oid = t.typnamespace
        CROSS JOIN LATERAL aclexplode(t.typacl) AS x
    UNION ALL SELECT 'language', l.lanname, x.privilege_type, x.grantee, x.is_grantable
    FROM pg_language AS l CROSS JOIN LATERAL aclexplode(l.lanacl) AS x
    UNION ALL SELECT 'large object', m.oid::text, x.privilege_type, x.grantee, x.is_grantable
    FROM pg_largeobject_metadata AS m CROSS JOIN LATERAL aclexplode(m.lomacl) AS x
    UNION ALL SELECT 'parameter', p.parname, x.privilege_type, x.grantee, x.is_grantable
    FROM pg_parameter_acl AS p CROSS JOIN LATERAL aclexplode(p.paracl) AS x
    UNION ALL SELECT 'tablespace', s.spcname, x.privilege_type, x.grantee, x.is_grantable
    FROM pg_tablespace AS s CROSS JOIN LATERAL aclexplode(s.spcacl) AS x
    UNION ALL SELECT 'foreign data wrapper', w.fdwname, x.privilege_type, x.grantee, x.is_grantable
    FROM pg_foreign_data_wrapper AS w CROSS JOIN LATERAL aclexplode(w.fdwacl) AS x
    UNION ALL SELECT 'foreign server', f.srvname, x.privilege_type, x.grantee, x.is_grantable
    FROM pg_foreign_server AS f CROSS JOIN LATERAL aclexplode(f.srvacl) AS x
) AS held
WHERE held.grantee = current_user::regrole AND held.is_grantable
""")

type Found = list[tuple[str, str]]


def ordered(connection: Connection, query: TextClause) -> list[str]:
    """The distinct first column of a query's rows, sorted here by code point (SPEC_DECISIONS 133):
    the order never depends on a database collation."""
    return sorted({str(row[0]) for row in connection.execute(query)})


def column_privileges(connection: Connection) -> Found:
    """5. Any column-level privilege of the role or PUBLIC on a table of `public`."""
    return [("COLUMN_PRIVILEGE", name) for name in ordered(connection, _COLUMNS)]


def settings(connection: Connection) -> Found:
    """7. Role settings, a database setting of `session_replication_role`, parameter privileges,
    and a session that would not fire triggers for a reason neither setting explains
    (SPEC_DECISIONS 103, 128)."""
    found = [("ROLE_HAS_SETTING", name) for name in ordered(connection, _ROLE_SETTINGS)]
    database = [("DATABASE_HAS_SETTING", name) for name in ordered(connection, _DATABASE_REPLICA)]
    found.extend(database)
    found.extend(("PARAMETER_PRIVILEGE", name) for name in ordered(connection, _PARAMETERS))
    replica: bool = connection.execute(_SESSION_REPLICA).scalar_one()
    explained: bool = bool(database) or connection.execute(_ROLE_REPLICA).scalar_one()
    if replica and not explained:
        found.append(("TRIGGERS_DISABLED_FOR_SESSION", "session"))
    return found


def functions(connection: Connection) -> Found:
    """8. A SECURITY DEFINER function in a non-system schema, and EXECUTE on any function there."""
    found = [("SECURITY_DEFINER_FUNCTION", name) for name in ordered(connection, _DEFINERS)]
    found.extend(("FUNCTION_PRIVILEGE", name) for name in ordered(connection, _EXECUTABLE))
    return found


def default_privileges(connection: Connection) -> Found:
    """9. An entry of `pg_default_acl` that grants anything to the role or PUBLIC."""
    return [("DEFAULT_PRIVILEGE", name) for name in ordered(connection, _DEFAULTS)]


def grant_options(connection: Connection) -> Found:
    """10. Anything the role holds WITH GRANT OPTION."""
    return [("GRANT_OPTION", name) for name in ordered(connection, _GRANT_OPTIONS)]
