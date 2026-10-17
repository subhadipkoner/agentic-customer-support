"""Case storage. Uses PostgreSQL when DATABASE_URL is set (survives restarts/deploys on hosts with an ephemeral
disk, e.g. Render free + Neon), otherwise a local JSON file."""
import json, os

URL = os.getenv("DATABASE_URL")
FILE = os.getenv("DB_PATH", "cases.json")


def backend():
    return "postgres" if URL else "json-file"


def _pg():
    import psycopg
    return psycopg.connect(URL, autocommit=True, connect_timeout=15)


def load():
    if URL:
        try:
            with _pg() as c:
                c.execute("create table if not exists cases (id text primary key, data text not null)")
                return {r[0]: json.loads(r[1]) for r in c.execute("select id, data from cases")}
        except Exception as e:
            print(f"[store] could not load from Postgres: {e}")
            return {}
    return json.load(open(FILE)) if os.path.exists(FILE) else {}


def save(cases):
    if URL:
        try:
            with _pg() as c:
                with c.cursor() as cur:
                    cur.executemany("insert into cases (id, data) values (%s, %s) "
                                    "on conflict (id) do update set data = excluded.data",
                                    [(k, json.dumps(v)) for k, v in cases.items()])
        except Exception as e:  # never fail a customer request because the database hiccuped
            print(f"[store] could not save to Postgres: {e}")
        return
    json.dump(cases, open(FILE, "w"), indent=1)
