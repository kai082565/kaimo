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
        """)

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
          )
        ORDER BY t.id DESC
    """
    with get_conn() as conn:
        rows = conn.execute(sql, (ym,)).fetchall()
        result = []
        for r in rows:
            d = _enrich_ticket(dict(r), conn, today)
            # 用本月那一期的資料覆蓋顯示欄位
            nxt = conn.execute(
                """SELECT due_date, interest, late_fee, paid_principal, principal_balance
                   FROM payment_schedule
                   WHERE ticket_id=? AND status='pending'
                     AND strftime('%Y-%m', due_date)=?
                   ORDER BY period_no LIMIT 1""",
                (d["id"], ym)
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
        d["is_overdue"] = d["status"] == "active" and d["due_date"] < date.today().isoformat()

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
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO tickets
               (ticket_no, customer_id, item_name, item_description,
                category_id, principal, monthly_rate, pawn_date, due_date, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
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
        conn.execute(
            """UPDATE tickets SET
               item_name=?, item_description=?, category_id=?,
               principal=?, monthly_rate=?, pawn_date=?, due_date=?, notes=?
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
        conn.execute("DELETE FROM tickets WHERE id=?", (tid,))


def delete_customer(cid: int):
    with get_conn() as conn:
        conn.execute("""DELETE FROM payment_schedule WHERE ticket_id IN
                        (SELECT id FROM tickets WHERE customer_id=?)""", (cid,))
        conn.execute("""DELETE FROM ticket_history WHERE ticket_id IN
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
