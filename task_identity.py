"""谱面身份及旧终极记录迁移；空类型保留旧记录的跨类型锁。"""


def chart_type(game: str, kind: str = "", label: str = "") -> str:
    if game != "maimai":
        return ""
    kind = kind.lower().strip()
    if kind in {"standard", "std", "dx"}:
        return "dx" if kind == "dx" else "standard"
    label = label.upper().strip()
    if "DX" in label:
        return "dx"
    if label in {"BASIC", "ADVANCED", "EXPERT", "MASTER", "REMASTER"} or "标准" in label:
        return "standard"
    return ""


def chart_key(game: str, song_id: str, index: int, kind: str = "") -> str:
    base = f"{game}:{song_id}"
    if index < 0:
        return base
    variant = chart_type(game, kind)
    return f"{base}:{variant}:{index}" if variant else f"{base}:{index}"


def migrate_task_identity(conn):
    """在单个事务内迁移；可恢复的类型精确区分，无法恢复的保留旧锁。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        if "chart_type" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN chart_type TEXT NOT NULL DEFAULT ''")
            for row in conn.execute("SELECT id, game, difficulty_label FROM tasks").fetchall():
                conn.execute("UPDATE tasks SET chart_type=? WHERE id=?",
                             (chart_type(row["game"], label=row["difficulty_label"]), row["id"]))
            # 已接取任务也必须展示精确要求，避免继续沿用旧的“或以上”。
            conn.execute("""UPDATE tasks SET difficulty_label=difficulty_label || ' 标准'
                WHERE game='maimai' AND chart_type='standard' AND difficulty_label NOT LIKE '%标准%'""")
            conn.execute("""UPDATE tasks SET requirement_text='仅 ' || difficulty_label || ' ' || target_level || ' · SSS+'
                WHERE task_kind='ultimate' AND difficulty_index IS NOT NULL AND difficulty_label<>''""")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(ultimate_completed_charts)")}
        if "chart_type" not in columns:
            conn.execute("ALTER TABLE ultimate_completed_charts RENAME TO ultimate_completed_charts_legacy")
            conn.execute("""CREATE TABLE ultimate_completed_charts (
                qq_id TEXT NOT NULL, game TEXT NOT NULL, song_id TEXT NOT NULL,
                difficulty_index INTEGER NOT NULL DEFAULT -1, completed_at TEXT NOT NULL,
                chart_type TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(qq_id, game, song_id, chart_type, difficulty_index),
                FOREIGN KEY(qq_id) REFERENCES players(qq_id))""")
            for row in conn.execute("SELECT * FROM ultimate_completed_charts_legacy").fetchall():
                matches = conn.execute("""SELECT DISTINCT chart_type FROM tasks
                    WHERE qq_id=? AND game=? AND song_id=? AND difficulty_index=?
                    AND task_kind='ultimate' AND status='approved' AND awarded=1""",
                    (row["qq_id"], row["game"], row["song_id"], row["difficulty_index"])).fetchall()
                variants = {item[0] for item in matches} or {""}
                for variant in variants:
                    conn.execute("INSERT OR IGNORE INTO ultimate_completed_charts VALUES(?,?,?,?,?,?)",
                                 (*tuple(row), variant))
            conn.execute("DROP TABLE ultimate_completed_charts_legacy")
        conn.execute("""INSERT OR IGNORE INTO ultimate_completed_charts
            (qq_id, game, song_id, difficulty_index, completed_at, chart_type)
            SELECT qq_id, game, song_id, COALESCE(difficulty_index,-1),
                   COALESCE(reviewed_at,created_at), chart_type FROM tasks
            WHERE task_kind='ultimate' AND status='approved' AND awarded=1""")
        conn.execute("""INSERT OR IGNORE INTO ultimate_completed_charts
            (qq_id, game, song_id, difficulty_index, completed_at)
            SELECT s.qq_id,s.game,s.song_id,-1,s.completed_at FROM ultimate_completed_songs s
            WHERE NOT EXISTS (SELECT 1 FROM ultimate_completed_charts c
                WHERE c.qq_id=s.qq_id AND c.game=s.game AND c.song_id=s.song_id)""")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
