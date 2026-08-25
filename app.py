
import io
import os
import sqlite3
from pathlib import Path
from datetime import datetime, date

from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify

APP_NAME = "صندوق شركة عزم الشرق"
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("AZM_CASHBOX_DB", str(BASE_DIR / "azm_cashbox.db")))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "azm-alsharq-cashbox-change-me")

CURRENCIES = {
    "USD": "دولار أمريكي",
    "IQD": "دينار عراقي",
}

def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_type TEXT NOT NULL CHECK(transaction_type IN ('IN','OUT')),
        amount REAL NOT NULL CHECK(amount >= 0),
        currency TEXT NOT NULL CHECK(currency IN ('USD','IQD')),
        person_name TEXT,
        related_customer TEXT,
        destination TEXT,
        receiver_name TEXT,
        transaction_date TEXT NOT NULL,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_transactions_date
    ON transactions(transaction_date);

    CREATE INDEX IF NOT EXISTS idx_transactions_currency
    ON transactions(currency);

    CREATE INDEX IF NOT EXISTS idx_transactions_type
    ON transactions(transaction_type);
    """)
    conn.commit()
    conn.close()

init_db()

def balance(currency):
    conn = get_conn()
    row = conn.execute("""
        SELECT COALESCE(SUM(
            CASE WHEN transaction_type='IN' THEN amount ELSE -amount END
        ), 0) AS balance
        FROM transactions
        WHERE currency=?
    """, (currency,)).fetchone()
    conn.close()
    return float(row["balance"] or 0)

def get_transactions(date_from=None, date_to=None):
    q = "SELECT * FROM transactions WHERE 1=1"
    params = []
    if date_from:
        q += " AND transaction_date >= ?"
        params.append(date_from)
    if date_to:
        q += " AND transaction_date <= ?"
        params.append(date_to)
    q += " ORDER BY transaction_date DESC, id DESC"
    conn = get_conn()
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return rows

def format_money(amount, currency):
    amount = float(amount or 0)
    if currency == "IQD":
        return f"{amount:,.0f}"
    return f"{amount:,.2f}"

@app.route("/")
def index():
    today = date.today().isoformat()
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    rows = get_transactions(date_from or None, date_to or None)

    totals = {"USD_IN": 0, "USD_OUT": 0, "IQD_IN": 0, "IQD_OUT": 0}
    for r in rows:
        key = f"{r['currency']}_{r['transaction_type']}"
        totals[key] += float(r["amount"] or 0)

    return render_template(
        "index.html",
        app_name=APP_NAME,
        today=today,
        usd_balance=balance("USD"),
        iqd_balance=balance("IQD"),
        rows=rows,
        totals=totals,
        date_from=date_from,
        date_to=date_to,
        format_money=format_money,
    )

@app.route("/income", methods=["GET", "POST"])
def income():
    if request.method == "POST":
        amount = request.form.get("amount", "").strip()
        currency = request.form.get("currency", "").strip()
        person_name = request.form.get("person_name", "").strip()
        related_customer = request.form.get("related_customer", "").strip()
        transaction_date = request.form.get("transaction_date", "").strip()
        notes = request.form.get("notes", "").strip()

        try:
            amount_value = float(amount)
        except Exception:
            flash("قيمة المبلغ غير صحيحة.", "error")
            return redirect(url_for("income"))

        if amount_value <= 0:
            flash("المبلغ يجب أن يكون أكبر من صفر.", "error")
            return redirect(url_for("income"))
        if currency not in CURRENCIES:
            flash("العملة غير صحيحة.", "error")
            return redirect(url_for("income"))
        if not person_name:
            flash("اكتب اسم الشخص المستلم.", "error")
            return redirect(url_for("income"))
        if not transaction_date:
            transaction_date = date.today().isoformat()

        conn = get_conn()
        conn.execute("""
            INSERT INTO transactions
            (transaction_type, amount, currency, person_name, related_customer,
             transaction_date, notes)
            VALUES ('IN', ?, ?, ?, ?, ?, ?)
        """, (amount_value, currency, person_name, related_customer, transaction_date, notes))
        conn.commit()
        conn.close()

        flash("تمت إضافة الأموال إلى الصندوق.", "success")
        return redirect(url_for("index"))

    return render_template(
        "income.html",
        app_name=APP_NAME,
        today=date.today().isoformat(),
        currencies=CURRENCIES,
    )

@app.route("/expense", methods=["GET", "POST"])
def expense():
    if request.method == "POST":
        amount = request.form.get("amount", "").strip()
        currency = request.form.get("currency", "").strip()
        destination = request.form.get("destination", "").strip()
        receiver_name = request.form.get("receiver_name", "").strip()
        transaction_date = request.form.get("transaction_date", "").strip()
        notes = request.form.get("notes", "").strip()

        try:
            amount_value = float(amount)
        except Exception:
            flash("قيمة المبلغ غير صحيحة.", "error")
            return redirect(url_for("expense"))

        if amount_value <= 0:
            flash("المبلغ يجب أن يكون أكبر من صفر.", "error")
            return redirect(url_for("expense"))
        if currency not in CURRENCIES:
            flash("العملة غير صحيحة.", "error")
            return redirect(url_for("expense"))
        if not destination:
            flash("اكتب جهة خروج الأموال.", "error")
            return redirect(url_for("expense"))
        if not receiver_name:
            flash("اكتب اسم المستلم.", "error")
            return redirect(url_for("expense"))
        if not transaction_date:
            transaction_date = date.today().isoformat()

        current_balance = balance(currency)
        if amount_value > current_balance:
            flash(
                f"تنبيه: المبلغ أكبر من رصيد الصندوق الحالي ({format_money(current_balance, currency)} {currency}). "
                "تم تسجيل العملية كما طلبت.",
                "warning"
            )

        conn = get_conn()
        conn.execute("""
            INSERT INTO transactions
            (transaction_type, amount, currency, destination, receiver_name,
             transaction_date, notes)
            VALUES ('OUT', ?, ?, ?, ?, ?, ?)
        """, (amount_value, currency, destination, receiver_name, transaction_date, notes))
        conn.commit()
        conn.close()

        flash("تم تسجيل خروج الأموال من الصندوق.", "success")
        return redirect(url_for("index"))

    return render_template(
        "expense.html",
        app_name=APP_NAME,
        today=date.today().isoformat(),
        currencies=CURRENCIES,
    )

@app.post("/delete/<int:transaction_id>")
def delete_transaction(transaction_id):
    conn = get_conn()
    row = conn.execute("SELECT id FROM transactions WHERE id=?", (transaction_id,)).fetchone()
    if row:
        conn.execute("DELETE FROM transactions WHERE id=?", (transaction_id,))
        conn.commit()
        flash("تم حذف العملية.", "success")
    else:
        flash("العملية غير موجودة.", "error")
    conn.close()
    return redirect(request.referrer or url_for("index"))

def arabic_pdf_text(text):
    text = str(text or "")
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text

def find_arabic_font():
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None

@app.route("/statement.pdf")
def statement_pdf():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    rows = get_transactions(date_from or None, date_to or None)

    font_name = "Helvetica"
    font_path = find_arabic_font()
    if font_path:
        try:
            pdfmetrics.registerFont(TTFont("AzmArabic", font_path))
            font_name = "AzmArabic"
        except Exception:
            pass

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=25,
        leftMargin=25,
        topMargin=30,
        bottomMargin=30,
        title="Azm Alsharq Cashbox Statement",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ArabicTitle",
        parent=styles["Title"],
        fontName=font_name,
        fontSize=17,
        leading=22,
        alignment=TA_CENTER,
        spaceAfter=8,
    )
    normal_right = ParagraphStyle(
        "ArabicRight",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=9,
        leading=13,
        alignment=TA_RIGHT,
    )

    story = []
    story.append(Paragraph(arabic_pdf_text("شركة عزم الشرق - كشف حساب الصندوق"), title_style))

    period_text = "جميع الحركات"
    if date_from or date_to:
        period_text = f"الفترة: {date_from or 'البداية'} إلى {date_to or 'اليوم'}"
    story.append(Paragraph(arabic_pdf_text(period_text), normal_right))
    story.append(Paragraph(arabic_pdf_text(f"تاريخ إصدار الكشف: {date.today().isoformat()}"), normal_right))
    story.append(Spacer(1, 12))

    balances_data = [
        [arabic_pdf_text("الرصيد الحالي"), arabic_pdf_text("العملة")],
        [format_money(balance("USD"), "USD"), "USD"],
        [format_money(balance("IQD"), "IQD"), "IQD"],
    ]
    bt = Table(balances_data, colWidths=[170, 100], hAlign="RIGHT")
    bt.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), font_name),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#E5E7EB")),
        ("GRID", (0,0), (-1,-1), 0.5, colors.HexColor("#9CA3AF")),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("FONTSIZE", (0,0), (-1,-1), 9),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(bt)
    story.append(Spacer(1, 14))

    headers = [
        arabic_pdf_text("التاريخ"),
        arabic_pdf_text("الحركة"),
        arabic_pdf_text("المبلغ"),
        arabic_pdf_text("العملة"),
        arabic_pdf_text("الشخص/المستلم"),
        arabic_pdf_text("الزبون/الجهة"),
        arabic_pdf_text("ملاحظات"),
    ]
    data = [headers]

    for r in rows:
        is_in = r["transaction_type"] == "IN"
        person = r["person_name"] if is_in else r["receiver_name"]
        party = r["related_customer"] if is_in else r["destination"]
        movement = "دخول" if is_in else "خروج"
        data.append([
            str(r["transaction_date"]),
            arabic_pdf_text(movement),
            format_money(r["amount"], r["currency"]),
            r["currency"],
            arabic_pdf_text(person or ""),
            arabic_pdf_text(party or ""),
            arabic_pdf_text(r["notes"] or ""),
        ])

    if len(data) == 1:
        data.append(["-", arabic_pdf_text("لا توجد حركات"), "-", "-", "-", "-", "-"])

    table = Table(
        data,
        repeatRows=1,
        colWidths=[65, 48, 65, 42, 85, 92, 105],
    )
    table.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), font_name),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#111827")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.35, colors.HexColor("#9CA3AF")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("FONTSIZE", (0,0), (-1,-1), 7.2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F9FAFB")]),
    ]))

    # Highlight money-in and money-out rows
    for idx, r in enumerate(rows, start=1):
        if r["transaction_type"] == "IN":
            table.setStyle(TableStyle([
                ("BACKGROUND", (1,idx), (1,idx), colors.HexColor("#DCFCE7"))
            ]))
        else:
            table.setStyle(TableStyle([
                ("BACKGROUND", (1,idx), (1,idx), colors.HexColor("#FEE2E2"))
            ]))

    story.append(table)
    doc.build(story)
    buf.seek(0)

    filename = f"Azm_Alsharq_Cashbox_{date_from or 'start'}_{date_to or date.today().isoformat()}.pdf"
    return send_file(
        buf,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf",
    )

@app.route("/manifest.webmanifest")
def manifest():
    return jsonify({
        "name": APP_NAME,
        "short_name": "عزم الشرق",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f4f6f8",
        "theme_color": "#111827",
        "lang": "ar",
        "dir": "rtl",
        "icons": [
            {
                "src": "/static/icon.svg",
                "sizes": "any",
                "type": "image/svg+xml",
                "purpose": "any maskable"
            }
        ]
    })

@app.route("/service-worker.js")
def service_worker():
    content = """
const CACHE = 'azm-cashbox-v1';
const CORE = ['/', '/static/style.css', '/static/icon.svg'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(CORE)));
});
self.addEventListener('activate', event => {
  event.waitUntil(self.clients.claim());
});
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});
"""
    return app.response_class(content, mimetype="application/javascript")

@app.route("/health")
def health():
    return {"status": "ok", "app": APP_NAME}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8501"))
    app.run(host="0.0.0.0", port=port, debug=False)
