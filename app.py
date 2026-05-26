import os
import sys
import threading
import webbrowser
from datetime import date

from flask import (Flask, render_template, request, redirect,
                   url_for, jsonify, flash)

import database as db


def _resource(rel):
    # PyInstaller 打包後資源在 _MEIPASS；開發時在原始目錄
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


app = Flask(__name__,
            template_folder=_resource('templates'),
            static_folder=_resource('static'))
app.secret_key = "pawnshop-2024"
app.config['TEMPLATES_AUTO_RELOAD'] = True


# ── 工具 ──────────────────────────────────────────────

def _today():
    return date.today().isoformat()


# ── 儀表板 ────────────────────────────────────────────

@app.route("/")
def dashboard():
    stats = db.get_dashboard_stats()
    due_soon = db.get_due_soon_tickets(14)
    recent = db.get_recent_history(8)
    settings = db.get_settings()
    return render_template("dashboard.html",
                           stats=stats,
                           due_soon=due_soon,
                           recent=recent,
                           settings=settings,
                           today=_today())


# ── 當票 ──────────────────────────────────────────────

@app.route("/tickets")
def tickets():
    status = request.args.get("status", "")
    search = request.args.get("q", "")
    rows = db.list_tickets(status or None, search or None)
    categories = db.get_categories()
    return render_template("tickets/list.html",
                           tickets=rows,
                           page_title="當票管理",
                           status_filter=status,
                           search=search,
                           categories=categories,
                           today=_today())


@app.route("/tickets/monthly")
def tickets_monthly():
    rows = db.list_tickets_monthly()
    return render_template("tickets/list.html",
                           tickets=rows,
                           page_title="當月應收",
                           status_filter="", search="", today=_today())


@app.route("/tickets/unpaid")
def tickets_unpaid():
    rows = db.list_tickets_unpaid()
    return render_template("tickets/list.html",
                           tickets=rows,
                           page_title="應收未收",
                           status_filter="", search="", today=_today())


@app.route("/tickets/new", methods=["GET", "POST"])
def new_ticket():
    categories = db.get_categories()
    settings = db.get_settings()

    if request.method == "POST":
        data = request.form.to_dict()
        cid = db.create_customer({
            "name":    data.get("customer_name", "").strip(),
            "id_card": data.get("customer_id_card", "").strip(),
            "phone":   data.get("customer_phone", "").strip(),
            "address": data.get("customer_address", "").strip(),
        })
        data["customer_id"] = cid

        tid = db.create_ticket(data)
        flash("當票開立成功！", "success")
        return redirect(url_for("ticket_detail", ticket_id=tid))

    return render_template("tickets/new.html",
                           categories=categories,
                           settings=settings,
                           today=_today())


@app.route("/tickets/<int:ticket_id>")
def ticket_detail(ticket_id):
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        flash("找不到該當票", "danger")
        return redirect(url_for("tickets"))
    return render_template("tickets/detail.html",
                           ticket=ticket,
                           today=_today())


@app.route("/tickets/<int:ticket_id>/redeem", methods=["POST"])
def redeem(ticket_id):
    calc_date = request.form.get("calc_date") or _today()
    notes = request.form.get("notes", "")
    db.redeem_ticket(ticket_id, calc_date, notes)
    flash("贖回完成！", "success")
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


@app.route("/tickets/<int:ticket_id>/renew", methods=["POST"])
def renew(ticket_id):
    new_months = int(request.form.get("new_months", 3))
    calc_date = request.form.get("calc_date") or _today()
    notes = request.form.get("notes", "")
    db.renew_ticket(ticket_id, new_months, calc_date, notes)
    flash("續當成功！", "success")
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


@app.route("/tickets/<int:ticket_id>/forfeit", methods=["POST"])
def forfeit(ticket_id):
    notes = request.form.get("notes", "")
    db.forfeit_ticket(ticket_id, notes)
    flash("已標記為流當。", "warning")
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


@app.route("/tickets/<int:ticket_id>/delete", methods=["POST"])
def delete_ticket(ticket_id):
    db.delete_ticket(ticket_id)
    return redirect(url_for("tickets"))


@app.route("/tickets/<int:ticket_id>/pay_period/<int:schedule_id>", methods=["POST"])
def pay_period(ticket_id, schedule_id):
    data = request.get_json() or {}
    db.record_period_payment(
        schedule_id,
        float(data.get("paid_amount", 0)),
        float(data.get("paid_principal", 0)),
        float(data.get("late_fee", 0)),
        data.get("notes", ""),
    )
    return jsonify({"ok": True})


@app.route("/tickets/<int:ticket_id>/repay", methods=["POST"])
def repay_principal(ticket_id):
    data = request.get_json() or {}
    db.repay_principal(ticket_id, float(data.get("amount", 0)), data.get("notes", ""))
    return jsonify({"ok": True})


@app.route("/tickets/<int:ticket_id>/settle", methods=["POST"])
def settle_schedule(ticket_id):
    db.settle_ticket_schedule(ticket_id)
    return jsonify({"ok": True})


# ── 客戶 ──────────────────────────────────────────────

@app.route("/customers")
def customers():
    search = request.args.get("q", "")
    rows = db.list_customers(search or None)
    return render_template("customers/list.html",
                           customers=rows, search=search)



@app.route("/customers/<int:customer_id>")
def customer_detail(customer_id):
    customer = db.get_customer(customer_id)
    if not customer:
        flash("找不到該客戶", "danger")
        return redirect(url_for("customers"))
    return render_template("customers/detail.html",
                           customer=customer, today=_today())


@app.route("/customers/<int:customer_id>/delete", methods=["POST"])
def delete_customer(customer_id):
    db.delete_customer(customer_id)
    flash("客戶已刪除。", "success")
    return redirect(url_for("customers"))


@app.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
def edit_customer(customer_id):
    customer = db.get_customer(customer_id)
    if not customer:
        return redirect(url_for("customers"))
    if request.method == "POST":
        db.update_customer(customer_id, request.form.to_dict())
        flash("客戶資料已更新！", "success")
        return redirect(url_for("customer_detail", customer_id=customer_id))
    return render_template("customers/form.html",
                           customer=customer, action="edit")


# ── 報表 ──────────────────────────────────────────────

@app.route("/reports")
def reports():
    year = int(request.args.get("year", date.today().year))
    monthly = db.report_monthly(year)
    categories = db.report_category()
    status_count = db.report_status_count()
    return render_template("reports.html",
                           year=year,
                           monthly=monthly,
                           categories=categories,
                           status_count=status_count,
                           current_year=date.today().year)


@app.route("/api/reports/monthly")
def api_monthly():
    year = int(request.args.get("year", date.today().year))
    return jsonify(db.report_monthly(year))


@app.route("/api/interest/<int:ticket_id>")
def api_interest(ticket_id):
    calc_date = request.args.get("date", _today())
    new_months = int(request.args.get("new_months", 3))
    t = db.get_ticket(ticket_id)
    if not t:
        return jsonify({"error": "not found"}), 404
    months = db.calc_months(t["pawn_date"], calc_date)
    interest = db.calc_interest(t["principal"], t["monthly_rate"], months)
    from dateutil.relativedelta import relativedelta
    new_due = (date.fromisoformat(calc_date) + relativedelta(months=new_months)).isoformat()
    return jsonify({
        "months": months,
        "interest": interest,
        "total": t["principal"] + interest,
        "principal": t["principal"],
        "new_due": new_due,
    })


# ── 設定 ──────────────────────────────────────────────

@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        db.save_settings(request.form.to_dict())
        # 更新分類利率
        categories = db.get_categories()
        with db.get_conn() as conn:
            for cat in categories:
                rate_key = f"cat_rate_{cat['id']}"
                name_key = f"cat_name_{cat['id']}"
                if rate_key in request.form:
                    conn.execute(
                        "UPDATE categories SET default_rate=?, name=? WHERE id=?",
                        (request.form[rate_key], request.form.get(name_key, cat["name"]), cat["id"])
                    )
        flash("設定已儲存！", "success")
        return redirect(url_for("settings"))
    cfg = db.get_settings()
    categories = db.get_categories()
    return render_template("settings.html", cfg=cfg, categories=categories)


# ── 啟動 ──────────────────────────────────────────────

def open_browser(port):
    webbrowser.open(f"http://127.0.0.1:{port}")


if __name__ == "__main__":
    db.init_db()
    port = 5678
    # debug=True 讓程式碼和模板變更後自動重載，F5 即可看到效果
    # WERKZEUG_RUN_MAIN 判斷避免瀏覽器開兩次
    if not os.environ.get("NO_BROWSER") and not os.environ.get("WERKZEUG_RUN_MAIN"):
        threading.Timer(1.2, open_browser, args=[port]).start()
    app.run(host="127.0.0.1", port=port, debug=True)
