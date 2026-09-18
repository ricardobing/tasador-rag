"""Consultas de solo lectura contra la base real."""

import os
import sys

import sqlalchemy as sa

e = sa.create_engine(os.environ["DATABASE_URL"])


def q(titulo, sql):
    print("--- " + titulo)
    with e.connect() as c:
        res = c.execute(sa.text(sql))
        cols = list(res.keys())
        rows = res.fetchall()
    print("   " + " | ".join(str(x) for x in cols))
    for r in rows[:60]:
        print("   " + " | ".join("" if v is None else str(v) for v in r))
    if len(rows) > 60:
        print(f"   ... ({len(rows)} filas)")
    print()


if __name__ == "__main__":
    for bloque in open(sys.argv[1], encoding="utf-8").read().split("\n===\n"):
        titulo, _, sql = bloque.partition("\n")
        if sql.strip():
            q(titulo.strip(), sql)
