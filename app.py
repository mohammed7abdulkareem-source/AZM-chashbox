
import io, os, sqlite3, secrets
from pathlib import Path
from datetime import date, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify, session, abort
from werkzeug.security import generate_password_hash, check_password_hash

APP_NAME = "صندوق شركة عزم الشرق"
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("AZM_CASHBOX_DB", str(BASE_DIR/"azm_cashbox.db")))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "CHANGE-ME-AZM-CASHBOX-2026")
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(days=90),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE","1") == "1",
)

ROLES = {
    "ADMIN": "Admin - مدير",
    "FULL": "Full User - مستخدم كامل",
    "VIEW": "View Only - مشاهدة فقط",
}
CURRENCIES = {"USD":"دولار أمريكي","IQD":"دينار عراقي"}

def conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c=sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory=sqlite3.Row
    return c

def init_db():
    c=conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL COLLATE NOCASE,
      full_name TEXT NOT NULL,
      password_hash TEXT NOT NULL,
      role TEXT NOT NULL CHECK(role IN ('ADMIN','FULL','VIEW')),
      active INTEGER NOT NULL DEFAULT 1,
      last_login TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS transactions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      transaction_type TEXT NOT NULL CHECK(transaction_type IN ('IN','OUT')),
      amount REAL NOT NULL CHECK(amount>0),
      currency TEXT NOT NULL CHECK(currency IN ('USD','IQD')),
      person_name TEXT,
      related_customer TEXT,
      destination TEXT,
      receiver_name TEXT,
      transaction_date TEXT NOT NULL,
      notes TEXT,
      created_by INTEGER,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(created_by) REFERENCES users(id)
    );
    CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(transaction_date);
    CREATE INDEX IF NOT EXISTS idx_tx_cur ON transactions(currency);
    """)
    if not c.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        c.execute("""INSERT INTO users(username,full_name,password_hash,role,active)
                     VALUES(?,?,?,?,1)""",
                  ("admin","Administrator",generate_password_hash("Azm@2026"),"ADMIN"))
    c.commit(); c.close()
init_db()

def money(x,cur):
    x=float(x or 0)
    return f"{x:,.0f}" if cur=="IQD" else f"{x:,.2f}"

def balance(cur, before=None):
    c=conn()
    q="""SELECT COALESCE(SUM(CASE WHEN transaction_type='IN' THEN amount ELSE -amount END),0) b
         FROM transactions WHERE currency=?"""
    p=[cur]
    if before:
        q+=" AND transaction_date < ?"; p.append(before)
    r=c.execute(q,p).fetchone(); c.close()
    return float(r["b"] or 0)

def transactions(frm=None,to=None, running=True):
    c=conn()
    allrows=c.execute("SELECT * FROM transactions ORDER BY transaction_date ASC,id ASC").fetchall()
    c.close()
    bals={"USD":0.0,"IQD":0.0}; out=[]
    for rr in allrows:
        r=dict(rr)
        signed=float(r["amount"])*(1 if r["transaction_type"]=="IN" else -1)
        bals[r["currency"]]+=signed
        r["balance_after"]=bals[r["currency"]]
        if frm and r["transaction_date"]<frm: continue
        if to and r["transaction_date"]>to: continue
        out.append(r)
    return list(reversed(out))

def summary(frm=None,to=None):
    rows=transactions(frm,to)
    s={c:{"opening":balance(c,frm) if frm else 0.0,"in":0.0,"out":0.0,"closing":0.0} for c in CURRENCIES}
    for r in rows:
        s[r["currency"]]["in" if r["transaction_type"]=="IN" else "out"] += float(r["amount"])
    for cur in CURRENCIES:
        s[cur]["closing"]=s[cur]["opening"]+s[cur]["in"]-s[cur]["out"]
        if not frm:
            s[cur]["closing"]=balance(cur)
    return s

def current_user():
    uid=session.get("uid")
    if not uid: return None
    c=conn(); u=c.execute("SELECT * FROM users WHERE id=? AND active=1",(uid,)).fetchone(); c.close()
    return u

@app.context_processor
def inject():
    return dict(current_user=current_user(), money=money, roles=ROLES, today=date.today().isoformat())

def login_required(fn):
    @wraps(fn)
    def w(*a,**kw):
        if not current_user(): return redirect(url_for("login",next=request.path))
        return fn(*a,**kw)
    return w

def roles_required(*roles):
    def deco(fn):
        @wraps(fn)
        def w(*a,**kw):
            u=current_user()
            if not u: return redirect(url_for("login"))
            if u["role"] not in roles: abort(403)
            return fn(*a,**kw)
        return w
    return deco

@app.route("/login",methods=["GET","POST"])
def login():
    if current_user(): return redirect(url_for("index"))
    if request.method=="POST":
        username=request.form.get("username","").strip()
        password=request.form.get("password","")
        c=conn(); u=c.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE",(username,)).fetchone()
        if u and u["active"] and check_password_hash(u["password_hash"],password):
            session.clear(); session.permanent=True; session["uid"]=u["id"]
            c.execute("UPDATE users SET last_login=CURRENT_TIMESTAMP WHERE id=?",(u["id"],)); c.commit(); c.close()
            return redirect(url_for("index"))
        c.close(); flash("اسم المستخدم أو الرقم السري غير صحيح.","error")
    return render_template("login.html")

@app.post("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    frm=request.args.get("from",""); to=request.args.get("to","")
    rows=transactions(frm or None,to or None)
    return render_template("index.html",rows=rows,frm=frm,to=to,
                           usd=balance("USD"),iqd=balance("IQD"),summ=summary(frm or None,to or None))

@app.route("/income",methods=["GET","POST"])
@roles_required("ADMIN","FULL")
def income():
    if request.method=="POST":
        try: amount=float(request.form["amount"])
        except: amount=0
        cur=request.form.get("currency"); person=request.form.get("person_name","").strip()
        customer=request.form.get("related_customer","").strip(); dt=request.form.get("transaction_date") or date.today().isoformat()
        notes=request.form.get("notes","").strip()
        if amount<=0 or cur not in CURRENCIES or not person:
            flash("تأكد من المبلغ والعملة واسم الشخص.","error")
        else:
            c=conn(); c.execute("""INSERT INTO transactions
            (transaction_type,amount,currency,person_name,related_customer,transaction_date,notes,created_by)
            VALUES('IN',?,?,?,?,?,?,?)""",(amount,cur,person,customer,dt,notes,current_user()["id"]))
            c.commit(); c.close(); flash("تم إدخال الأموال وتحديث الرصيد.","success"); return redirect(url_for("index"))
    return render_template("income.html",usd=balance("USD"),iqd=balance("IQD"))

@app.route("/expense",methods=["GET","POST"])
@roles_required("ADMIN","FULL")
def expense():
    if request.method=="POST":
        try: amount=float(request.form["amount"])
        except: amount=0
        cur=request.form.get("currency"); dest=request.form.get("destination","").strip()
        receiver=request.form.get("receiver_name","").strip(); dt=request.form.get("transaction_date") or date.today().isoformat()
        notes=request.form.get("notes","").strip()
        if amount<=0 or cur not in CURRENCIES or not dest or not receiver:
            flash("تأكد من المبلغ والعملة والجهة والمستلم.","error")
        else:
            c=conn(); c.execute("""INSERT INTO transactions
            (transaction_type,amount,currency,destination,receiver_name,transaction_date,notes,created_by)
            VALUES('OUT',?,?,?,?,?,?,?)""",(amount,cur,dest,receiver,dt,notes,current_user()["id"]))
            c.commit(); c.close()
            flash(f"تم تسجيل الخروج. الرصيد الحالي: {money(balance(cur),cur)} {cur}","success")
            return redirect(url_for("index"))
    return render_template("expense.html",usd=balance("USD"),iqd=balance("IQD"))

@app.post("/delete/<int:tid>")
@roles_required("ADMIN","FULL")
def delete_tx(tid):
    c=conn(); c.execute("DELETE FROM transactions WHERE id=?",(tid,)); c.commit(); c.close()
    flash("تم حذف الحركة وإعادة احتساب الرصيد.","success")
    return redirect(request.referrer or url_for("index"))

@app.route("/users")
@roles_required("ADMIN")
def users():
    c=conn(); rows=c.execute("SELECT * FROM users ORDER BY id").fetchall(); c.close()
    return render_template("users.html",users=rows)

@app.route("/users/add",methods=["POST"])
@roles_required("ADMIN")
def add_user():
    username=request.form.get("username","").strip(); full=request.form.get("full_name","").strip()
    pw=request.form.get("password",""); role=request.form.get("role")
    if not username or not full or len(pw)<6 or role not in ROLES:
        flash("أكمل البيانات، والرقم السري 6 أحرف على الأقل.","error"); return redirect(url_for("users"))
    try:
        c=conn(); c.execute("INSERT INTO users(username,full_name,password_hash,role) VALUES(?,?,?,?)",
                           (username,full,generate_password_hash(pw),role)); c.commit(); c.close()
        flash("تمت إضافة المستخدم.","success")
    except sqlite3.IntegrityError:
        flash("اسم المستخدم موجود مسبقاً.","error")
    return redirect(url_for("users"))

@app.post("/users/<int:uid>/toggle")
@roles_required("ADMIN")
def toggle_user(uid):
    if uid==current_user()["id"]:
        flash("لا يمكنك إيقاف حسابك الحالي.","error"); return redirect(url_for("users"))
    c=conn(); u=c.execute("SELECT active FROM users WHERE id=?",(uid,)).fetchone()
    if u: c.execute("UPDATE users SET active=? WHERE id=?",(0 if u["active"] else 1,uid)); c.commit()
    c.close(); return redirect(url_for("users"))

@app.post("/users/<int:uid>/password")
@roles_required("ADMIN")
def user_password(uid):
    pw=request.form.get("password","")
    if len(pw)<6: flash("الرقم السري يجب أن يكون 6 أحرف على الأقل.","error")
    else:
        c=conn(); c.execute("UPDATE users SET password_hash=? WHERE id=?",(generate_password_hash(pw),uid)); c.commit(); c.close()
        flash("تم تغيير الرقم السري.","success")
    return redirect(url_for("users"))

@app.post("/users/<int:uid>/delete")
@roles_required("ADMIN")
def delete_user(uid):
    if uid==current_user()["id"]:
        flash("لا يمكنك حذف حسابك الحالي.","error"); return redirect(url_for("users"))
    c=conn(); c.execute("DELETE FROM users WHERE id=?",(uid,)); c.commit(); c.close()
    flash("تم حذف المستخدم.","success"); return redirect(url_for("users"))

def arabic(s):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(str(s or "")))
    except: return str(s or "")

def font_setup():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf","/usr/share/fonts/truetype/freefont/FreeSans.ttf"]:
        if os.path.exists(p):
            try: pdfmetrics.registerFont(TTFont("AzmArabic",p)); return "AzmArabic"
            except: pass
    return "Helvetica"

@app.route("/statement.pdf")
@login_required
def statement_pdf():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate,Table,TableStyle,Paragraph,Spacer
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    frm=request.args.get("from",""); to=request.args.get("to","")
    rows=transactions(frm or None,to or None); sm=summary(frm or None,to or None); font=font_setup()
    b=io.BytesIO(); doc=SimpleDocTemplate(b,pagesize=landscape(A4),rightMargin=18,leftMargin=18,topMargin=22,bottomMargin=18)
    styles=getSampleStyleSheet(); title=ParagraphStyle("t",parent=styles["Title"],fontName=font,alignment=TA_CENTER)
    story=[Paragraph(arabic("شركة عزم الشرق - كشف حساب الصندوق"),title),
           Paragraph(arabic(f"الفترة: {frm or 'البداية'} إلى {to or date.today().isoformat()}"),title),Spacer(1,10)]
    sd=[[arabic("العملة"),arabic("الرصيد الافتتاحي"),arabic("إجمالي الدخول"),arabic("إجمالي الخروج"),arabic("الرصيد النهائي")]]
    for cur in ("USD","IQD"):
        x=sm[cur]; sd.append([cur,money(x["opening"],cur),money(x["in"],cur),money(x["out"],cur),money(x["closing"],cur)])
    st=Table(sd,colWidths=[90,120,120,120,120]); st.setStyle(TableStyle([
        ("FONTNAME",(0,0),(-1,-1),font),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#111827")),
        ("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),.5,colors.grey),("ALIGN",(0,0),(-1,-1),"CENTER")
    ])); story += [st,Spacer(1,12)]
    data=[[arabic(x) for x in ["التاريخ","الحركة","المبلغ","العملة","الشخص/المستلم","الزبون/الجهة","الرصيد بعد العملية","ملاحظات"]]]
    for r in reversed(rows):
        data.append([r["transaction_date"],arabic("دخول" if r["transaction_type"]=="IN" else "خروج"),
                     money(r["amount"],r["currency"]),r["currency"],
                     arabic(r["person_name"] if r["transaction_type"]=="IN" else r["receiver_name"]),
                     arabic(r["related_customer"] if r["transaction_type"]=="IN" else r["destination"]),
                     money(r["balance_after"],r["currency"]),arabic(r["notes"] or "")])
    if len(data)==1: data.append(["-",arabic("لا توجد حركات"),"-","-","-","-","-","-"])
    t=Table(data,repeatRows=1,colWidths=[70,55,75,45,105,110,90,130]); t.setStyle(TableStyle([
        ("FONTNAME",(0,0),(-1,-1),font),("FONTSIZE",(0,0),(-1,-1),7),
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#111827")),("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("GRID",(0,0),(-1,-1),.35,colors.grey),("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#F7F7F7")])
    ])); story.append(t); doc.build(story); b.seek(0)
    return send_file(b,as_attachment=True,download_name=f"Azm_Cashbox_{frm or 'start'}_{to or date.today()}.pdf",mimetype="application/pdf")

@app.route("/manifest.webmanifest")
def manifest():
    return jsonify({"name":APP_NAME,"short_name":"عزم الشرق","start_url":"/","display":"standalone",
                    "background_color":"#f4f6f8","theme_color":"#111827","lang":"ar","dir":"rtl",
                    "icons":[{"src":"/static/icon.svg","sizes":"any","type":"image/svg+xml"}]})

@app.route("/service-worker.js")
def sw():
    return app.response_class("self.addEventListener('fetch',e=>{e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)))})",mimetype="application/javascript")

@app.route("/health")
def health(): return {"status":"ok"}

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","8501")),debug=False)
