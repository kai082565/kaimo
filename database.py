import sqlite3
import os
import sys
from datetime import date, datetime
from dateutil.relativedelta import relativedelta


def _app_dir():
    # 打包成 .exe 後，資料庫放在 .exe 旁邊（而非暫存資料夾）
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


DB_PATH = os.path.join(_app_dir(), 'pawnshop.db')


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS categories (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL,
                default_rate REAL DEFAULT 3.0
            );

            CREATE TABLE IF NOT EXISTS customers (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                id_card    TEXT,
                phone      TEXT,
                address    TEXT,
                notes      TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS tickets (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_no        TEXT UNIQUE NOT NULL,
                customer_id      INTEGER REFERENCES customers(id),
                item_name        TEXT NOT NULL,
                item_description TEXT,
                category_id      INTEGER REFERENCES categories(id),
                principal        REAL NOT NULL,
                monthly_rate     REAL NOT NULL,
                pawn_date        TEXT NOT NULL,
                due_date         TEXT NOT NULL,
                status           TEXT DEFAULT 'active',
                notes            TEXT,
                created_at       TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS ticket_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id    INTEGER REFERENCES tickets(id),
                action       TEXT NOT NULL,
                principal    REAL,
                interest     REAL,
                months       INTEGER,
                total_amount REAL,
                new_due_date TEXT,
                notes        TEXT,
                action_date  TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS payment_schedule (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id         INTEGER REFERENCES tickets(id),
                period_no         INTEGER NOT NULL,
                due_date          TEXT NOT NULL,
                principal_balance REAL NOT NULL,
                interest          REAL NOT NULL,
                late_fee          REAL DEFAULT 0,
                paid_amount       REAL DEFAULT 0,
                paid_principal    REAL DEFAULT 0,
                status            TEXT DEFAULT 'pending',
                notes             TEXT,
                paid_at           TEXT
            );

            CREATE TABLE IF NOT EXISTS staff (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                phone      TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS bad_debts (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id        INTEGER NOT NULL REFERENCES tickets(id),
                customer_id      INTEGER REFERENCES customers(id),
                amount           REAL NOT NULL,
                recovered_amount REAL DEFAULT 0,
                status           TEXT DEFAULT '未回收',
                created_date     TEXT DEFAULT (date('now','localtime')),
                notes            TEXT
            );

            CREATE TABLE IF NOT EXISTS fund_providers (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL,
                income       REAL DEFAULT 0,
                expense      REAL DEFAULT 0,
                fixed_amount REAL DEFAULT 0,
                staff_id     INTEGER REFERENCES staff(id),
                notes        TEXT,
                created_at   TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                date           TEXT NOT NULL,
                item           TEXT NOT NULL,
                type           TEXT NOT NULL,
                outgoing       REAL DEFAULT 0,
                op_expense     REAL DEFAULT 0,
                income         REAL DEFAULT 0,
                other_income   REAL DEFAULT 0,
                capital_change REAL DEFAULT 0,
                staff_id       INTEGER REFERENCES staff(id),
                notes          TEXT,
                created_at     TEXT DEFAULT (datetime('now','localtime'))
            );
        """)

        # migration: staff_id on tickets
        cols = [r[1] for r in conn.execute("PRAGMA table_info(tickets)").fetchall()]
        if "staff_id" not in cols:
            conn.execute("ALTER TABLE tickets ADD COLUMN staff_id INTEGER REFERENCES staff(id)")

        # migration: contact_status on payment_schedule
        ps_cols = [r[1] for r in conn.execute("PRAGMA table_info(payment_schedule)").fetchall()]
        if "contact_status" not in ps_cols:
            conn.execute("ALTER TABLE payment_schedule ADD COLUMN contact_status TEXT DEFAULT '未聯絡'")

        # 預設分類
        existing = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        if existing == 0:
            conn.executemany(
                "INSERT INTO categories (name, default_rate) VALUES (?, ?)",
                [
                    ("金飾珠寶", 3.0),
                    ("名錶", 3.0),
                    ("3C 電子", 3.5),
                    ("名牌包", 3.0),
                    ("機車/汽車", 2.5),
                    ("其他", 4.0),
                ]
            )

        # 預設設定
        defaults = [
            ("shop_name", "當鋪管理系統"),
            ("default_months", "3"),
            ("default_rate", "3.0"),
            ("ticket_prefix", "T"),
        ]
        for key, val in defaults:
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, val)
            )


# ── 計息 ─────────────────────────────────────────────

def calc_months(pawn_date_str: str, calc_date_str: str = None) -> int:
    pawn = date.fromisoformat(pawn_date_str)
    calc = date.fromisoformat(calc_date_str) if calc_date_str else date.today()
    if calc <= pawn:
        return 1
    diff = relativedelta(calc, pawn)
    months = diff.years * 12 + diff.months
    if diff.days > 0:
        months += 1
    return max(1, months)


def calc_interest(principal: float, monthly_rate: float, months: int) -> float:
    return round(principal * (monthly_rate / 100) * months, 0)


# ── 流水號 ────────────────────────────────────────────

def next_ticket_no() -> str:
    with get_conn() as conn:
        prefix = conn.execute(
            "SELECT value FROM settings WHERE key='ticket_prefix'"
        ).fetchone()["value"]
        last = conn.execute(
            "SELECT ticket_no FROM tickets ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last:
            try:
                num = int(last["ticket_no"].replace(prefix, "")) + 1
            except ValueError:
                num = 1
        else:
            num = 1
        return f"{prefix}{num:05d}"


# ── 儀表板統計 ────────────────────────────────────────

def get_dashboard_stats():
    today = date.today().isoformat()
    with get_conn() as conn:
        active_count = conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE status='active'"
        ).fetchone()[0]

        total_principal = conn.execute(
            "SELECT COALESCE(SUM(principal),0) FROM tickets WHERE status='active'"
        ).fetchone()[0]

        # 本月已收利息（贖回+流當 history 中本月的 interest 欄）
        ym = date.today().strftime("%Y-%m")
        month_interest = conn.execute(
            """SELECT COALESCE(SUM(interest),0) FROM ticket_history
               WHERE action IN ('redeemed','forfeited')
               AND action_date LIKE ?""",
            (f"{ym}%",)
        ).fetchone()[0]

        # 即將到期（7天內）
        from datetime import timedelta
        soon = (date.today() + timedelta(days=7)).isoformat()
        due_soon = conn.execute(
            """SELECT COUNT(*) FROM tickets
               WHERE status='active' AND due_date <= ?""",
            (soon,)
        ).fetchone()[0]

        # 逾期未贖
        overdue = conn.execute(
            """SELECT COUNT(*) FROM tickets
               WHERE status='active' AND due_date < ?""",
            (today,)
        ).fetchone()[0]

        return {
            "active_count": active_count,
            "total_principal": total_principal,
            "month_interest": month_interest,
            "due_soon": due_soon,
            "overdue": overdue,
        }


def get_due_soon_tickets(days=14):
    from datetime import timedelta
    soon = (date.today() + timedelta(days=days)).isoformat()
    today = date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT t.*, c.name AS customer_name
               FROM tickets t LEFT JOIN customers c ON t.customer_id=c.id
               WHERE t.status='active' AND t.due_date <= ?
               ORDER BY t.due_date ASC LIMIT 20""",
            (soon,)
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        months = calc_months(d["pawn_date"])
        d["interest"] = calc_interest(d["principal"], d["monthly_rate"], months)
        d["total"] = d["principal"] + d["interest"]
        d["months"] = months
        d["is_overdue"] = d["due_date"] < today
        result.append(d)
    return result


def get_recent_history(limit=10):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT h.*, t.ticket_no, c.name AS customer_name
               FROM ticket_history h
               LEFT JOIN tickets t ON h.ticket_id=t.id
               LEFT JOIN customers c ON t.customer_id=c.id
               ORDER BY h.action_date DESC LIMIT ?""",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── 當票 CRUD ─────────────────────────────────────────

def list_tickets(status=None, search=None):
    sql = """SELECT t.*, c.name AS customer_name, cat.name AS category_name
             FROM tickets t
             LEFT JOIN customers c ON t.customer_id=c.id
             LEFT JOIN categories cat ON t.category_id=cat.id
             WHERE 1=1"""
    params = []
    if status:
        sql += " AND t.status=?"
        params.append(status)
    if search:
        sql += " AND (t.ticket_no LIKE ? OR c.name LIKE ? OR t.item_name LIKE ?)"
        like = f"%{search}%"
        params += [like, like, like]
    sql += " ORDER BY t.id DESC"
    today = date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        result = []
        for r in rows:
            result.append(_enrich_ticket(dict(r), conn, today))
    return result


def _enrich_ticket(d, conn, today):
    """將 next_due_date / interest / late_fee / paid_principal / principal_balance 附加到 ticket dict"""
    if d["status"] == "active":
        nxt = conn.execute(
            """SELECT due_date, interest, late_fee, paid_principal, principal_balance
               FROM payment_schedule
               WHERE ticket_id=? AND status='pending'
               ORDER BY period_no LIMIT 1""",
            (d["id"],)
        ).fetchone()
        d["next_due_date"]          = nxt["due_date"]          if nxt else None
        d["next_interest"]          = nxt["interest"]          if nxt else 0
        d["next_late_fee"]          = nxt["late_fee"]          if nxt else 0
        d["next_paid_principal"]    = nxt["paid_principal"]    if nxt else 0
        d["next_principal_balance"] = nxt["principal_balance"] if nxt else d["principal"]
        d["next_due_overdue"]       = bool(d["next_due_date"] and d["next_due_date"] < today)
    else:
        d["next_due_date"] = d["next_interest"] = d["next_late_fee"] = None
        d["next_due_overdue"] = False
        d["next_paid_principal"]    = 0
        d["next_principal_balance"] = d["principal"]
    d["is_overdue"] = d["status"] == "active" and d["due_date"] < today
    return d


def list_tickets_monthly():
    """當月應收：有任一未付期數的應繳日期在本月，顯示本月那一期的資料"""
    ym = date.today().strftime("%Y-%m")
    today = date.today().isoformat()
    sql = """
        SELECT t.*, c.name AS customer_name, cat.name AS category_name
        FROM tickets t
        LEFT JOIN customers c ON t.customer_id=c.id
        LEFT JOIN categories cat ON t.category_id=cat.id
        WHERE t.status='active'
          AND t.id IN (
              SELECT DISTINCT ticket_id FROM payment_schedule
              WHERE status='pending'
                AND strftime('%Y-%m', due_date)=?
                AND due_date >= ?
          )
        ORDER BY t.id DESC
    """
    with get_conn() as conn:
        rows = conn.execute(sql, (ym, today)).fetchall()
        result = []
        for r in rows:
            d = _enrich_ticket(dict(r), conn, today)
            # 用本月那一期的資料覆蓋顯示欄位
            nxt = conn.execute(
                """SELECT due_date, interest, late_fee, paid_principal, principal_balance
                   FROM payment_schedule
                   WHERE ticket_id=? AND status='pending'
                     AND strftime('%Y-%m', due_date)=?
                     AND due_date >= ?
                   ORDER BY period_no LIMIT 1""",
                (d["id"], ym, today)
            ).fetchone()
            if nxt:
                d["next_due_date"]          = nxt["due_date"]
                d["next_interest"]          = nxt["interest"]
                d["next_late_fee"]          = nxt["late_fee"]
                d["next_paid_principal"]    = nxt["paid_principal"]
                d["next_principal_balance"] = nxt["principal_balance"]
                d["next_due_overdue"]       = nxt["due_date"] < today
            result.append(d)
    return result


def list_tickets_unpaid():
    """應收未收：最近一期未付的應繳日期已逾期（早於今天）"""
    today = date.today().isoformat()
    sql = """
        SELECT t.*, c.name AS customer_name, cat.name AS category_name
        FROM tickets t
        LEFT JOIN customers c ON t.customer_id=c.id
        LEFT JOIN categories cat ON t.category_id=cat.id
        WHERE t.status='active'
          AND t.id IN (
              SELECT ticket_id FROM payment_schedule
              WHERE status='pending'
                AND due_date < ?
                AND period_no=(
                    SELECT MIN(period_no) FROM payment_schedule ps2
                    WHERE ps2.ticket_id=payment_schedule.ticket_id
                      AND ps2.status='pending'
                )
          )
        ORDER BY t.id DESC
    """
    with get_conn() as conn:
        rows = conn.execute(sql, (today,)).fetchall()
        result = [_enrich_ticket(dict(r), conn, today) for r in rows]
    return result


def get_ticket(ticket_id):
    with get_conn() as conn:
        row = conn.execute(
            """SELECT t.*, c.name AS customer_name, c.phone AS customer_phone,
                      cat.name AS category_name
               FROM tickets t
               LEFT JOIN customers c ON t.customer_id=c.id
               LEFT JOIN categories cat ON t.category_id=cat.id
               WHERE t.id=?""",
            (ticket_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        months = calc_months(d["pawn_date"])
        d["interest"] = calc_interest(d["principal"], d["monthly_rate"], months)
        d["total"] = d["principal"] + d["interest"]
        d["months"] = months
        today_iso = date.today().isoformat()
        d["is_overdue"] = d["status"] == "active" and d["due_date"] < today_iso
        nxt_sched = conn.execute(
            """SELECT due_date FROM payment_schedule
               WHERE ticket_id=? AND status='pending'
               ORDER BY period_no LIMIT 1""",
            (ticket_id,)
        ).fetchone()
        d["next_due_overdue"] = bool(
            d["status"] == "active" and nxt_sched and nxt_sched["due_date"] < today_iso
        )

        history = conn.execute(
            "SELECT * FROM ticket_history WHERE ticket_id=? ORDER BY action_date DESC",
            (ticket_id,)
        ).fetchall()
        d["history"] = [dict(h) for h in history]
    return d


def create_ticket(data: dict) -> int:
    pawn_date = data["pawn_date"]
    months = int(data.get("term_months", 3))
    due = (date.fromisoformat(pawn_date) + relativedelta(months=months)).isoformat()
    ticket_no = next_ticket_no()
    staff_id = data.get("staff_id") or None
    if staff_id:
        staff_id = int(staff_id)
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO tickets
               (ticket_no, customer_id, item_name, item_description,
                category_id, principal, monthly_rate, pawn_date, due_date, notes, staff_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ticket_no,
                data["customer_id"] or None,
                data["item_name"],
                data.get("item_description", ""),
                data.get("category_id") or None,
                float(data["principal"]),
                float(data["monthly_rate"]),
                pawn_date,
                due,
                data.get("notes", ""),
                staff_id,
            )
        )
        tid = cur.lastrowid
        conn.execute(
            """INSERT INTO ticket_history (ticket_id, action, principal, notes)
               VALUES (?,?,?,?)""",
            (tid, "created", float(data["principal"]), "開立當票")
        )
    generate_payment_schedule(tid, float(data["principal"]),
                              float(data["monthly_rate"]), pawn_date, months)
    return tid


def update_ticket(ticket_id: int, data: dict):
    new_principal = float(data["principal"])
    new_rate      = float(data["monthly_rate"])
    with get_conn() as conn:
        staff_id = data.get("staff_id") or None
        if staff_id:
            staff_id = int(staff_id)
        conn.execute(
            """UPDATE tickets SET
               item_name=?, item_description=?, category_id=?,
               principal=?, monthly_rate=?, pawn_date=?, due_date=?, notes=?, staff_id=?
               WHERE id=?""",
            (
                data["item_name"],
                data.get("item_description", ""),
                data.get("category_id") or None,
                new_principal,
                new_rate,
                data["pawn_date"],
                data["due_date"],
                data.get("notes", ""),
                staff_id,
                ticket_id,
            )
        )
        # 同步更新客戶資料
        cid = conn.execute(
            "SELECT customer_id FROM tickets WHERE id=?", (ticket_id,)
        ).fetchone()["customer_id"]
        if cid and data.get("customer_name", "").strip():
            conn.execute(
                "UPDATE customers SET name=?, id_card=?, phone=?, address=? WHERE id=?",
                (
                    data.get("customer_name", ""),
                    data.get("customer_id_card", ""),
                    data.get("customer_phone", ""),
                    data.get("customer_address", ""),
                    cid,
                )
            )
        # 以已付期數的回本為基準，重算第一筆 pending 的本金餘額，並重算利息
        paid_repaid = conn.execute(
            """SELECT COALESCE(SUM(paid_principal),0) FROM payment_schedule
               WHERE ticket_id=? AND status='paid'""",
            (ticket_id,)
        ).fetchone()[0]
        remaining_principal = max(0, new_principal - paid_repaid)
        first_pending = conn.execute(
            """SELECT id, period_no FROM payment_schedule
               WHERE ticket_id=? AND status='pending'
               ORDER BY period_no LIMIT 1""",
            (ticket_id,)
        ).fetchone()
        if first_pending:
            conn.execute(
                "UPDATE payment_schedule SET principal_balance=? WHERE id=?",
                (remaining_principal, first_pending["id"])
            )
            _recalculate_schedule(conn, ticket_id, first_pending["period_no"])


def redeem_ticket(ticket_id: int, calc_date_str: str = None, notes: str = ""):
    t = get_ticket(ticket_id)
    if not t or t["status"] != "active":
        return False
    calc_date = calc_date_str or date.today().isoformat()
    months = calc_months(t["pawn_date"], calc_date)
    interest = calc_interest(t["principal"], t["monthly_rate"], months)
    total = t["principal"] + interest
    with get_conn() as conn:
        conn.execute(
            "UPDATE tickets SET status='redeemed' WHERE id=?",
            (ticket_id,)
        )
        conn.execute(
            """INSERT INTO ticket_history
               (ticket_id, action, principal, interest, months, total_amount, notes)
               VALUES (?,?,?,?,?,?,?)""",
            (ticket_id, "redeemed", t["principal"], interest, months, total, notes or "贖回")
        )
    return True


def renew_ticket(ticket_id: int, new_months: int = 3, calc_date_str: str = None, notes: str = ""):
    t = get_ticket(ticket_id)
    if not t or t["status"] != "active":
        return False
    calc_date = calc_date_str or date.today().isoformat()
    months = calc_months(t["pawn_date"], calc_date)
    interest = calc_interest(t["principal"], t["monthly_rate"], months)
    new_due = (date.fromisoformat(calc_date) + relativedelta(months=new_months)).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE tickets SET pawn_date=?, due_date=? WHERE id=?",
            (calc_date, new_due, ticket_id)
        )
        conn.execute(
            """INSERT INTO ticket_history
               (ticket_id, action, principal, interest, months, total_amount, new_due_date, notes)
               VALUES (?,?,?,?,?,?,?,?)""",
            (ticket_id, "renewed", t["principal"], interest, months, interest, new_due, notes or "續當")
        )
    return True


def forfeit_ticket(ticket_id: int, notes: str = ""):
    t = get_ticket(ticket_id)
    if not t or t["status"] != "active":
        return False
    months = calc_months(t["pawn_date"])
    interest = calc_interest(t["principal"], t["monthly_rate"], months)
    with get_conn() as conn:
        conn.execute(
            "UPDATE tickets SET status='forfeited' WHERE id=?",
            (ticket_id,)
        )
        conn.execute(
            """INSERT INTO ticket_history
               (ticket_id, action, principal, interest, months, total_amount, notes)
               VALUES (?,?,?,?,?,?,?)""",
            (ticket_id, "forfeited", t["principal"], interest, months,
             t["principal"] + interest, notes or "流當")
        )
    return True


# ── 客戶 CRUD ─────────────────────────────────────────

def list_customers(search=None):
    sql = """SELECT c.*,
                    COUNT(t.id) AS ticket_count,
                    SUM(CASE WHEN t.status='active' THEN t.principal ELSE 0 END) AS active_principal
             FROM customers c
             LEFT JOIN tickets t ON t.customer_id=c.id
             WHERE 1=1"""
    params = []
    if search:
        sql += " AND (c.name LIKE ? OR c.phone LIKE ? OR c.id_card LIKE ?)"
        like = f"%{search}%"
        params += [like, like, like]
    sql += " GROUP BY c.id ORDER BY c.id DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_customer(cid):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
        if not row:
            return None
        d = dict(row)
        tickets = conn.execute(
            """SELECT t.*, cat.name AS category_name
               FROM tickets t LEFT JOIN categories cat ON t.category_id=cat.id
               WHERE t.customer_id=? ORDER BY t.id DESC""",
            (cid,)
        ).fetchall()
        d["tickets"] = [dict(t) for t in tickets]
    for t in d["tickets"]:
        t["schedule"] = get_payment_schedule(t["id"])
    return d


def create_customer(data: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO customers (name, id_card, phone, address, notes)
               VALUES (?,?,?,?,?)""",
            (data["name"], data.get("id_card", ""), data.get("phone", ""),
             data.get("address", ""), data.get("notes", ""))
        )
        return cur.lastrowid


def update_customer(cid: int, data: dict):
    with get_conn() as conn:
        conn.execute(
            """UPDATE customers SET name=?, id_card=?, phone=?, address=?, notes=?
               WHERE id=?""",
            (data["name"], data.get("id_card", ""), data.get("phone", ""),
             data.get("address", ""), data.get("notes", ""), cid)
        )


def generate_payment_schedule(ticket_id: int, principal: float,
                              monthly_rate: float, pawn_date: str, term_months: int):
    pawn = date.fromisoformat(pawn_date)
    interest = round(principal * (monthly_rate / 100), 0)
    with get_conn() as conn:
        for i in range(term_months):
            due = (pawn + relativedelta(months=i)).isoformat()
            conn.execute(
                """INSERT INTO payment_schedule
                   (ticket_id, period_no, due_date, principal_balance, interest)
                   VALUES (?,?,?,?,?)""",
                (ticket_id, i + 1, due, principal, interest)
            )


def get_payment_schedule(ticket_id: int):
    today = date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM payment_schedule WHERE ticket_id=? ORDER BY period_no",
            (ticket_id,)
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # 剩餘本金 = 本金 - 回本
        d["principal_remaining"] = max(0, d["principal_balance"] - d["paid_principal"])
        # 剩餘利息（modal 用）
        d["remaining"] = round(max(0, d["interest"] + d["late_fee"] - d["paid_amount"]), 0)
        d["is_overdue"] = d["status"] == "pending" and d["due_date"] < today
        d["overdue_days"] = 0
        if d["is_overdue"]:
            d["overdue_days"] = (date.today() - date.fromisoformat(d["due_date"])).days
        result.append(d)
    return result


def record_period_payment(schedule_id: int, paid_amount: float,
                          paid_principal: float = 0, late_fee: float = 0, notes: str = ""):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM payment_schedule WHERE id=?", (schedule_id,)
        ).fetchone()
        if not row:
            return False
        new_paid       = row["paid_amount"]    + paid_amount
        new_principal  = row["paid_principal"] + paid_principal
        new_late_fee   = row["late_fee"]       + late_fee
        total_due      = row["interest"] + new_late_fee
        status         = "paid" if new_paid >= total_due else "pending"
        paid_at        = datetime.now().isoformat() if status == "paid" else row["paid_at"]
        conn.execute(
            """UPDATE payment_schedule
               SET paid_amount=?, paid_principal=?, late_fee=?, status=?, notes=?, paid_at=?
               WHERE id=?""",
            (new_paid, new_principal, new_late_fee, status, notes, paid_at, schedule_id)
        )
        if new_principal > row["paid_principal"]:
            _recalculate_schedule(conn, row["ticket_id"], row["period_no"])
    return True


def _recalculate_schedule(conn, ticket_id: int, from_period: int):
    rate = conn.execute(
        "SELECT monthly_rate FROM tickets WHERE id=?", (ticket_id,)
    ).fetchone()["monthly_rate"]
    periods = conn.execute(
        "SELECT * FROM payment_schedule WHERE ticket_id=? ORDER BY period_no",
        (ticket_id,)
    ).fetchall()
    running = None
    for p in periods:
        if running is None:
            running = p["principal_balance"]
        if p["period_no"] >= from_period and p["status"] != "paid":
            # 利息 = 剩餘本金 × 月利率（剩餘本金 = 本金 - 回本）
            remaining_principal = max(0, running - p["paid_principal"])
            conn.execute(
                "UPDATE payment_schedule SET principal_balance=?, interest=? WHERE id=?",
                (running, round(remaining_principal * (rate / 100), 0), p["id"])
            )
        running = max(0, running - p["paid_principal"])


def repay_principal(ticket_id: int, amount: float, notes: str = "", schedule_id: int = None):
    """回本：將金額記錄在指定期數（或最早一筆未付），並重算後續各期利息"""
    with get_conn() as conn:
        if schedule_id:
            period = conn.execute(
                "SELECT * FROM payment_schedule WHERE id=? AND ticket_id=?",
                (schedule_id, ticket_id)
            ).fetchone()
        else:
            period = conn.execute(
                """SELECT * FROM payment_schedule
                   WHERE ticket_id=? AND status='pending'
                   ORDER BY period_no LIMIT 1""",
                (ticket_id,)
            ).fetchone()
        if not period:
            return False
        new_principal = period["paid_principal"] + amount
        conn.execute(
            "UPDATE payment_schedule SET paid_principal=?, notes=? WHERE id=?",
            (new_principal, notes or period["notes"], period["id"])
        )
        _recalculate_schedule(conn, ticket_id, period["period_no"])
    return True


def cancel_period_payment(schedule_id: int):
    """取消付款：清除該期已繳金額與滯納金，狀態改回 pending"""
    with get_conn() as conn:
        conn.execute(
            """UPDATE payment_schedule
               SET paid_amount=0, late_fee=0, status='pending', paid_at=NULL, notes=NULL
               WHERE id=?""",
            (schedule_id,)
        )
    return True


def cancel_period_repayment(schedule_id: int):
    """取消回本：將該期 paid_principal 歸零，並重算後續各期利息"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ticket_id, period_no FROM payment_schedule WHERE id=?",
            (schedule_id,)
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE payment_schedule SET paid_principal=0 WHERE id=?",
            (schedule_id,)
        )
        _recalculate_schedule(conn, row["ticket_id"], row["period_no"])
    return True


def settle_ticket_schedule(ticket_id: int):
    with get_conn() as conn:
        ticket = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        pending = conn.execute(
            "SELECT * FROM payment_schedule WHERE ticket_id=? AND status='pending'",
            (ticket_id,)
        ).fetchall()
        total_interest = sum(
            max(0, p["interest"] + p["late_fee"] - p["paid_amount"]) for p in pending
        )
        total_paid_principal = conn.execute(
            "SELECT COALESCE(SUM(paid_principal),0) FROM payment_schedule WHERE ticket_id=?",
            (ticket_id,)
        ).fetchone()[0]
        remaining_principal = ticket["principal"] - total_paid_principal
        now = datetime.now().isoformat()
        conn.execute(
            """UPDATE payment_schedule
               SET status='paid', paid_amount=interest+late_fee, paid_at=?
               WHERE ticket_id=? AND status='pending'""",
            (now, ticket_id)
        )
        conn.execute("UPDATE tickets SET status='redeemed' WHERE id=?", (ticket_id,))
        conn.execute(
            """INSERT INTO ticket_history
               (ticket_id, action, principal, interest, total_amount, notes)
               VALUES (?,?,?,?,?,?)""",
            (ticket_id, "redeemed", remaining_principal, total_interest,
             remaining_principal + total_interest, "結清")
        )
    return True


def delete_ticket(tid: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM payment_schedule WHERE ticket_id=?", (tid,))
        conn.execute("DELETE FROM ticket_history WHERE ticket_id=?", (tid,))
        conn.execute("DELETE FROM bad_debts WHERE ticket_id=?", (tid,))
        conn.execute("DELETE FROM tickets WHERE id=?", (tid,))


def delete_customer(cid: int):
    with get_conn() as conn:
        conn.execute("""DELETE FROM payment_schedule WHERE ticket_id IN
                        (SELECT id FROM tickets WHERE customer_id=?)""", (cid,))
        conn.execute("""DELETE FROM ticket_history WHERE ticket_id IN
                        (SELECT id FROM tickets WHERE customer_id=?)""", (cid,))
        conn.execute("""DELETE FROM bad_debts WHERE ticket_id IN
                        (SELECT id FROM tickets WHERE customer_id=?)""", (cid,))
        conn.execute("DELETE FROM tickets WHERE customer_id=?", (cid,))
        conn.execute("DELETE FROM customers WHERE id=?", (cid,))


# ── 報表資料 ──────────────────────────────────────────

def report_monthly(year: int = None):
    y = year or date.today().year
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT strftime('%m', action_date) AS month,
                      COALESCE(SUM(interest), 0) AS interest,
                      COUNT(*) AS count
               FROM ticket_history
               WHERE action IN ('redeemed','forfeited')
                 AND strftime('%Y', action_date)=?
               GROUP BY month ORDER BY month""",
            (str(y),)
        ).fetchall()
    data = {str(i).zfill(2): {"interest": 0, "count": 0} for i in range(1, 13)}
    for r in rows:
        data[r["month"]] = {"interest": r["interest"], "count": r["count"]}
    return data


def report_category():
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT cat.name, COUNT(t.id) AS count,
                      COALESCE(SUM(t.principal),0) AS principal
               FROM tickets t
               LEFT JOIN categories cat ON t.category_id=cat.id
               WHERE t.status='active'
               GROUP BY cat.name ORDER BY count DESC""",
        ).fetchall()
    return [dict(r) for r in rows]


def report_status_count():
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT status, COUNT(*) AS count FROM tickets GROUP BY status"""
        ).fetchall()
    return {r["status"]: r["count"] for r in rows}


def get_settings():
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def save_settings(data: dict):
    with get_conn() as conn:
        for key, val in data.items():
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                (key, val)
            )


def get_categories():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM categories ORDER BY id").fetchall()
    return [dict(r) for r in rows]


# ── 業務員 ────────────────────────────────────────────

def get_staff_list():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM staff ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_staff(staff_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM staff WHERE id=?", (staff_id,)).fetchone()
    return dict(row) if row else None


def create_staff(data: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO staff (name, phone) VALUES (?, ?)",
            (data["name"].strip(), data.get("phone", "").strip())
        )
        return cur.lastrowid


def update_staff(staff_id: int, data: dict):
    with get_conn() as conn:
        conn.execute(
            "UPDATE staff SET name=?, phone=? WHERE id=?",
            (data["name"].strip(), data.get("phone", "").strip(), staff_id)
        )


def delete_staff(staff_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE tickets SET staff_id=NULL WHERE staff_id=?", (staff_id,))
        conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))


# ── 業績排名 ──────────────────────────────────────────

def get_performance_ranking(year: int, month: int):
    ym = f"{year:04d}-{month:02d}"
    prev_ym = f"{year:04d}-{month-1:02d}" if month > 1 else f"{year-1:04d}-12"

    def _month_stats(conn, staff_id, ym_str):
        expected = conn.execute("""
            SELECT COALESCE(SUM(ps.interest), 0)
            FROM payment_schedule ps JOIN tickets t ON ps.ticket_id=t.id
            WHERE t.staff_id=? AND strftime('%Y-%m', ps.due_date)=?
        """, (staff_id, ym_str)).fetchone()[0]
        collected = conn.execute("""
            SELECT COALESCE(SUM(ps.paid_amount), 0)
            FROM payment_schedule ps JOIN tickets t ON ps.ticket_id=t.id
            WHERE t.staff_id=? AND strftime('%Y-%m', ps.due_date)=?
              AND ps.status='paid'
        """, (staff_id, ym_str)).fetchone()[0]
        return float(expected), float(collected)

    with get_conn() as conn:
        staff_rows = conn.execute("SELECT * FROM staff ORDER BY name").fetchall()
        result = []
        for s in staff_rows:
            sid = s["id"]
            expected, collected = _month_stats(conn, sid, ym)
            prev_exp, prev_col = _month_stats(conn, sid, prev_ym)

            principal = conn.execute("""
                SELECT COALESCE(SUM(DISTINCT t.principal), 0)
                FROM tickets t JOIN payment_schedule ps ON ps.ticket_id=t.id
                WHERE t.staff_id=? AND strftime('%Y-%m', ps.due_date)=?
            """, (sid, ym)).fetchone()[0]

            customer_count = conn.execute("""
                SELECT COUNT(DISTINCT t.customer_id)
                FROM tickets t JOIN payment_schedule ps ON ps.ticket_id=t.id
                WHERE t.staff_id=? AND strftime('%Y-%m', ps.due_date)=?
            """, (sid, ym)).fetchone()[0]

            rate = round((collected / expected * 100) if expected > 0 else 0, 1)
            prev_rate = round((prev_col / prev_exp * 100) if prev_exp > 0 else 0, 1)

            grade = "A" if rate >= 90 else "B" if rate >= 75 else "C" if rate >= 60 else "D"
            trend = "up" if rate > prev_rate + 5 else "down" if rate < prev_rate - 5 else "stable"

            result.append({
                "id": sid, "name": s["name"], "phone": s["phone"] or "",
                "principal": float(principal), "expected": expected,
                "collected": collected, "collection_rate": rate,
                "customer_count": customer_count, "grade": grade, "trend": trend,
            })

        result.sort(key=lambda x: x["collected"], reverse=True)

        # 全體統計
        total_collected = sum(r["collected"] for r in result)
        avg_rate = round(sum(r["collection_rate"] for r in result) / len(result), 1) if result else 0
        top = result[0] if result else None
        return {
            "ranking": result,
            "total_collected": total_collected,
            "avg_rate": avg_rate,
            "staff_count": len(result),
            "top_name": top["name"] if top else "—",
            "top_collected": top["collected"] if top else 0,
        }


# ── 逾期總覽 ──────────────────────────────────────────

def get_overdue_overview(search=None, ref_date=None):
    today = date.today().isoformat()
    ref = ref_date or today

    with get_conn() as conn:
        # 總逾期筆數（今日前未付）
        total_count = conn.execute(
            "SELECT COUNT(*) FROM payment_schedule WHERE status='pending' AND due_date < ?",
            (today,)
        ).fetchone()[0]

        # 總逾期金額
        total_amount = conn.execute(
            "SELECT COALESCE(SUM(interest),0) FROM payment_schedule WHERE status='pending' AND due_date < ?",
            (today,)
        ).fetchone()[0]

        # 新增逾期（選取日期當天到期且仍未付）
        new_count = conn.execute(
            "SELECT COUNT(*) FROM payment_schedule WHERE status='pending' AND due_date=?",
            (ref,)
        ).fetchone()[0]

        # 逾期回收率：曾逾期的帳單中已付清的比例
        all_past_due = conn.execute(
            "SELECT COUNT(*) FROM payment_schedule WHERE due_date < ?", (today,)
        ).fetchone()[0]
        paid_past_due = conn.execute(
            "SELECT COUNT(*) FROM payment_schedule WHERE due_date < ? AND status='paid'", (today,)
        ).fetchone()[0]
        recovery_rate = round((paid_past_due / all_past_due * 100) if all_past_due > 0 else 0, 1)

        # 逾期清單
        sql = """
            SELECT ps.id, ps.ticket_id, ps.due_date, ps.interest,
                   COALESCE(ps.contact_status, '未聯絡') AS contact_status,
                   c.id AS customer_id, c.name AS customer_name, c.phone AS customer_phone,
                   st.name AS staff_name
            FROM payment_schedule ps
            JOIN tickets t ON ps.ticket_id = t.id
            LEFT JOIN customers c ON t.customer_id = c.id
            LEFT JOIN staff st ON t.staff_id = st.id
            WHERE ps.status = 'pending' AND ps.due_date < ?
        """
        params = [today]
        if search:
            sql += " AND c.name LIKE ?"
            params.append(f"%{search}%")
        sql += " ORDER BY ps.due_date ASC"

        rows = conn.execute(sql, params).fetchall()
        records = []
        for r in rows:
            d = dict(r)
            d["overdue_days"] = (date.today() - date.fromisoformat(d["due_date"])).days
            records.append(d)

    return {
        "total_count": total_count,
        "total_amount": float(total_amount),
        "new_count": new_count,
        "recovery_rate": recovery_rate,
        "records": records,
    }


def update_contact_status(schedule_id: int, status: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE payment_schedule SET contact_status=? WHERE id=?",
            (status, schedule_id)
        )


# ── 呆帳管理 ──────────────────────────────────────────

def _auto_mark_bad_debts():
    from datetime import timedelta
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT t.id AS ticket_id, t.customer_id, t.principal
            FROM payment_schedule ps
            JOIN tickets t ON ps.ticket_id = t.id
            WHERE ps.status = 'pending'
              AND ps.due_date < ?
              AND t.status = 'active'
              AND t.id NOT IN (SELECT ticket_id FROM bad_debts)
        """, (cutoff,)).fetchall()
        for r in rows:
            conn.execute(
                "INSERT INTO bad_debts (ticket_id, customer_id, amount) VALUES (?, ?, ?)",
                (r["ticket_id"], r["customer_id"], r["principal"])
            )


def get_bad_debts_overview(search=None):
    _auto_mark_bad_debts()
    ym = date.today().strftime("%Y-%m")
    with get_conn() as conn:
        total_count = conn.execute("SELECT COUNT(*) FROM bad_debts").fetchone()[0]
        total_amount = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM bad_debts"
        ).fetchone()[0]
        new_count = conn.execute(
            "SELECT COUNT(*) FROM bad_debts WHERE strftime('%Y-%m', created_date)=?", (ym,)
        ).fetchone()[0]
        new_amount = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM bad_debts WHERE strftime('%Y-%m', created_date)=?", (ym,)
        ).fetchone()[0]
        recovered_month = conn.execute(
            "SELECT COALESCE(SUM(recovered_amount),0) FROM bad_debts WHERE strftime('%Y-%m', created_date)=?", (ym,)
        ).fetchone()[0]

        q = """
            SELECT bd.id, bd.ticket_id, bd.amount, bd.recovered_amount,
                   bd.status, bd.created_date, bd.notes,
                   c.id AS customer_id, c.name AS customer_name, c.phone AS customer_phone
            FROM bad_debts bd
            LEFT JOIN customers c ON bd.customer_id = c.id
        """
        params = []
        if search:
            q += " WHERE c.name LIKE ?"
            params.append(f"%{search}%")
        q += " ORDER BY bd.created_date DESC, bd.id DESC"
        rows = conn.execute(q, params).fetchall()

    return {
        "total_count": total_count,
        "total_amount": float(total_amount),
        "new_count": new_count,
        "new_amount": float(new_amount),
        "recovered_month": float(recovered_month),
        "records": [dict(r) for r in rows],
    }


def update_bad_debt(bad_debt_id: int, recovered_amount: float, status: str, notes: str = ""):
    with get_conn() as conn:
        conn.execute(
            "UPDATE bad_debts SET recovered_amount=?, status=?, notes=? WHERE id=?",
            (recovered_amount, status, notes or "", bad_debt_id)
        )


def cancel_bad_debt(bad_debt_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM bad_debts WHERE id=?", (bad_debt_id,))


# ── 總帳總覽 ──────────────────────────────────────────

def get_ledger_overview():
    today = date.today()
    ym    = today.strftime("%Y-%m")

    # 近 6 個月標籤
    months = []
    for i in range(5, -1, -1):
        d = today - relativedelta(months=i)
        months.append(d.strftime("%Y-%m"))

    with get_conn() as conn:
        # 資金規模
        total_principal = float(conn.execute(
            "SELECT COALESCE(SUM(principal),0) FROM tickets WHERE status='active'"
        ).fetchone()[0])

        # 本月應收 / 實收
        expected = float(conn.execute("""
            SELECT COALESCE(SUM(interest),0) FROM payment_schedule
            WHERE strftime('%Y-%m', due_date)=?
        """, (ym,)).fetchone()[0])
        collected = float(conn.execute("""
            SELECT COALESCE(SUM(paid_amount),0) FROM payment_schedule
            WHERE strftime('%Y-%m', due_date)=? AND status='paid'
        """, (ym,)).fetchone()[0])

        # 資金提供方
        fp_income  = float(conn.execute("SELECT COALESCE(SUM(income),0)       FROM fund_providers").fetchone()[0])
        fp_expense = float(conn.execute("SELECT COALESCE(SUM(expense),0)      FROM fund_providers").fetchone()[0])
        fp_fixed   = float(conn.execute("SELECT COALESCE(SUM(fixed_amount),0) FROM fund_providers").fetchone()[0])
        fp_count   = conn.execute("SELECT COUNT(*) FROM fund_providers").fetchone()[0]

        # 逾期
        overdue_count  = conn.execute(
            "SELECT COUNT(*) FROM payment_schedule WHERE status='pending' AND due_date<?",
            (today.isoformat(),)
        ).fetchone()[0]
        overdue_amount = float(conn.execute(
            "SELECT COALESCE(SUM(interest),0) FROM payment_schedule WHERE status='pending' AND due_date<?",
            (today.isoformat(),)
        ).fetchone()[0])

        # 呆帳
        bad_count  = conn.execute("SELECT COUNT(*) FROM bad_debts").fetchone()[0]
        bad_amount = float(conn.execute("SELECT COALESCE(SUM(amount),0) FROM bad_debts").fetchone()[0])

        # 月度趨勢（近 6 個月）
        trend = []
        for m in months:
            e = float(conn.execute("""
                SELECT COALESCE(SUM(interest),0) FROM payment_schedule
                WHERE strftime('%Y-%m', due_date)=?
            """, (m,)).fetchone()[0])
            c = float(conn.execute("""
                SELECT COALESCE(SUM(paid_amount),0) FROM payment_schedule
                WHERE strftime('%Y-%m', due_date)=? AND status='paid'
            """, (m,)).fetchone()[0])
            trend.append({"month": m, "expected": e, "collected": c})

        # 活躍當票數
        active_tickets = conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE status='active'"
        ).fetchone()[0]

    collection_rate = round(collected / expected * 100, 1) if expected > 0 else 0
    net = collected - fp_fixed

    return {
        "total_principal":  total_principal,
        "expected":         expected,
        "collected":        collected,
        "collection_rate":  collection_rate,
        "fp_fixed":         fp_fixed,
        "net":              net,
        "fp_income":        fp_income,
        "fp_expense":       fp_expense,
        "fp_net":           fp_income - fp_expense,
        "fp_count":         fp_count,
        "overdue_count":    overdue_count,
        "overdue_amount":   overdue_amount,
        "bad_count":        bad_count,
        "bad_amount":       bad_amount,
        "trend":            trend,
        "active_tickets":   active_tickets,
    }


# ── 資金提供方 ────────────────────────────────────────

def list_fund_providers():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT fp.*, s.name AS staff_name
            FROM fund_providers fp
            LEFT JOIN staff s ON fp.staff_id = s.id
            ORDER BY fp.created_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


def create_fund_provider(data: dict) -> int:
    staff_id = data.get("staff_id") or None
    if staff_id:
        staff_id = int(staff_id)
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO fund_providers (name, income, expense, fixed_amount, staff_id, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            data["name"].strip(),
            float(data.get("income", 0) or 0),
            float(data.get("expense", 0) or 0),
            float(data.get("fixed_amount", 0) or 0),
            staff_id,
            data.get("notes", "").strip(),
        ))
        return cur.lastrowid


def update_fund_provider(fp_id: int, data: dict):
    staff_id = data.get("staff_id") or None
    if staff_id:
        staff_id = int(staff_id)
    with get_conn() as conn:
        conn.execute("""
            UPDATE fund_providers
            SET name=?, income=?, expense=?, fixed_amount=?, staff_id=?, notes=?
            WHERE id=?
        """, (
            data["name"].strip(),
            float(data.get("income", 0) or 0),
            float(data.get("expense", 0) or 0),
            float(data.get("fixed_amount", 0) or 0),
            staff_id,
            data.get("notes", "").strip(),
            fp_id,
        ))


def delete_fund_provider(fp_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM fund_providers WHERE id=?", (fp_id,))


# ── 交易明細 ──────────────────────────────────────────

def _balance_before(conn, date_str: str) -> float:
    """計算 date_str 之前所有交易的累積餘額"""
    # 自動：出款（當票本金）
    out = float(conn.execute(
        "SELECT COALESCE(SUM(principal),0) FROM tickets WHERE date(created_at)<?", (date_str,)
    ).fetchone()[0])
    # 自動：ticket_history 結清收入
    in_settle = float(conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) FROM ticket_history WHERE action='redeemed' AND date(action_date)<?", (date_str,)
    ).fetchone()[0])
    # 自動：ticket_history 續當利息
    in_renew = float(conn.execute(
        "SELECT COALESCE(SUM(interest),0) FROM ticket_history WHERE action='renewed' AND date(action_date)<?", (date_str,)
    ).fetchone()[0])
    # 手動交易
    manual = float(conn.execute(
        """SELECT COALESCE(SUM(income + other_income + capital_change - outgoing - op_expense),0)
           FROM transactions WHERE date<?""", (date_str,)
    ).fetchone()[0])
    return in_settle + in_renew - out + manual


def list_transactions(date_from=None, date_to=None, type_filter=None, sort_desc=True):
    today_iso = date.today().isoformat()
    df = date_from or date.today().replace(day=1).isoformat()
    dt = date_to or today_iso

    with get_conn() as conn:
        rows = []

        # 自動：當票開立 → 出款
        for r in conn.execute("""
            SELECT t.id, date(t.created_at) AS tx_date, t.ticket_no,
                   t.principal, s.name AS staff_name
            FROM tickets t LEFT JOIN staff s ON t.staff_id=s.id
            WHERE date(t.created_at) BETWEEN ? AND ?
        """, (df, dt)).fetchall():
            rows.append({
                'id': f'auto_t{r["id"]}', 'date': r['tx_date'],
                'item': f'開票 #{r["ticket_no"]}', 'type': '出款',
                'outgoing': float(r['principal']), 'op_expense': 0.0,
                'income': 0.0, 'other_income': 0.0, 'capital_change': 0.0,
                'staff_name': r['staff_name'] or '—', 'is_auto': True, 'notes': '',
            })

        # 自動：ticket_history → 結清 / 利息 / 呆帳
        for r in conn.execute("""
            SELECT th.id, date(th.action_date) AS tx_date, th.action,
                   th.interest, th.total_amount, t.ticket_no, s.name AS staff_name
            FROM ticket_history th
            JOIN tickets t ON th.ticket_id=t.id
            LEFT JOIN staff s ON t.staff_id=s.id
            WHERE date(th.action_date) BETWEEN ? AND ?
        """, (df, dt)).fetchall():
            if r['action'] == 'redeemed':
                rows.append({
                    'id': f'auto_h{r["id"]}', 'date': r['tx_date'],
                    'item': f'結清 #{r["ticket_no"]}', 'type': '結清',
                    'outgoing': 0.0, 'op_expense': 0.0,
                    'income': float(r['total_amount'] or 0),
                    'other_income': 0.0, 'capital_change': 0.0,
                    'staff_name': r['staff_name'] or '—', 'is_auto': True, 'notes': '',
                })
            elif r['action'] == 'renewed':
                rows.append({
                    'id': f'auto_h{r["id"]}', 'date': r['tx_date'],
                    'item': f'續當利息 #{r["ticket_no"]}', 'type': '利息',
                    'outgoing': 0.0, 'op_expense': 0.0,
                    'income': float(r['interest'] or 0),
                    'other_income': 0.0, 'capital_change': 0.0,
                    'staff_name': r['staff_name'] or '—', 'is_auto': True, 'notes': '',
                })
            elif r['action'] == 'forfeited':
                rows.append({
                    'id': f'auto_h{r["id"]}', 'date': r['tx_date'],
                    'item': f'流當呆帳 #{r["ticket_no"]}', 'type': '呆帳',
                    'outgoing': 0.0, 'op_expense': 0.0, 'income': 0.0,
                    'other_income': 0.0, 'capital_change': 0.0,
                    'staff_name': r['staff_name'] or '—', 'is_auto': True, 'notes': '',
                })

        # 手動交易
        for r in conn.execute("""
            SELECT tx.*, s.name AS staff_name
            FROM transactions tx LEFT JOIN staff s ON tx.staff_id=s.id
            WHERE tx.date BETWEEN ? AND ?
        """, (df, dt)).fetchall():
            d = dict(r)
            d['is_auto'] = False
            rows.append(d)

        # 類型篩選
        if type_filter and type_filter != '全部':
            rows = [r for r in rows if r['type'] == type_filter]

        # 依日期+id 升冪排序後計算餘額
        rows.sort(key=lambda x: (x['date'], str(x['id'])))

        running = _balance_before(conn, df)

    result = []
    for r in rows:
        effect = (r.get('income', 0) + r.get('other_income', 0) + r.get('capital_change', 0)
                  - r.get('outgoing', 0) - r.get('op_expense', 0))
        r['prev_balance'] = running
        r['curr_balance'] = running + effect
        running = r['curr_balance']
        result.append(r)

    if sort_desc:
        result.reverse()
    return result


def create_transaction(data: dict) -> int:
    staff_id = data.get('staff_id') or None
    if staff_id:
        staff_id = int(staff_id)

    def _f(k):
        return float(data.get(k, 0) or 0)

    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO transactions
              (date, item, type, outgoing, op_expense, income, other_income, capital_change, staff_id, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            data['date'], data['item'].strip(), data['type'],
            _f('outgoing'), _f('op_expense'), _f('income'),
            _f('other_income'), _f('capital_change'),
            staff_id, data.get('notes', '').strip(),
        ))
        return cur.lastrowid


def update_transaction(tx_id: int, data: dict):
    staff_id = data.get('staff_id') or None
    if staff_id:
        staff_id = int(staff_id)

    def _f(k):
        return float(data.get(k, 0) or 0)

    with get_conn() as conn:
        conn.execute("""
            UPDATE transactions
            SET date=?, item=?, type=?, outgoing=?, op_expense=?, income=?,
                other_income=?, capital_change=?, staff_id=?, notes=?
            WHERE id=?
        """, (
            data['date'], data['item'].strip(), data['type'],
            _f('outgoing'), _f('op_expense'), _f('income'),
            _f('other_income'), _f('capital_change'),
            staff_id, data.get('notes', '').strip(), tx_id,
        ))


def delete_transaction(tx_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM transactions WHERE id=?", (tx_id,))
