
import io, os
from datetime import date, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify, session, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import case, func
from werkzeug.security import generate_password_hash, check_password_hash

APP_NAME = "صندوق شركة عزم الشرق"

def normalize_database_url(url):
    url = (url or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url

DATABASE_URL = normalize_database_url(os.environ.get("DATABASE_URL", ""))
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///azm_cashbox.db"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "CHANGE-ME-AZM-CASHBOX-2026")
app.config.update(
    SQLALCHEMY_DATABASE_URI=DATABASE_URL,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    PERMANENT_SESSION_LIFETIME=timedelta(days=90),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE","1") == "1",
)

db = SQLAlchemy(app)
ROLES = {"ADMIN":"Admin - مدير","FULL":"Full User - مستخدم كامل","VIEW":"View Only - مشاهدة فقط"}
CURRENCIES = {"USD":"دولار أمريكي","IQD":"دينار عراقي"}

class User(db.Model):
    __tablename__="users"
    id=db.Column(db.Integer,primary_key=True)
    username=db.Column(db.String(80),unique=True,nullable=False,index=True)
    full_name=db.Column(db.String(160),nullable=False)
    password_hash=db.Column(db.String(255),nullable=False)
    role=db.Column(db.String(20),nullable=False,default="VIEW")
    active=db.Column(db.Boolean,nullable=False,default=True)
    last_login=db.Column(db.DateTime)
    created_at=db.Column(db.DateTime,server_default=func.now())

class Transaction(db.Model):
    __tablename__="transactions"
    id=db.Column(db.Integer,primary_key=True)
    transaction_type=db.Column(db.String(3),nullable=False,index=True)
    amount=db.Column(db.Numeric(18,2),nullable=False)
    currency=db.Column(db.String(3),nullable=False,index=True)
    person_name=db.Column(db.String(180))
    related_customer=db.Column(db.String(180))
    destination=db.Column(db.String(180))
    receiver_name=db.Column(db.String(180))
    transaction_date=db.Column(db.Date,nullable=False,index=True)
    notes=db.Column(db.Text)
    created_by=db.Column(db.Integer,db.ForeignKey("users.id"))
    created_at=db.Column(db.DateTime,server_default=func.now())

class AuditLog(db.Model):
    __tablename__="audit_logs"
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey("users.id"))
    action=db.Column(db.String(80),nullable=False)
    details=db.Column(db.Text)
    created_at=db.Column(db.DateTime,server_default=func.now())

with app.app_context():
    db.create_all()
    if not User.query.first():
        db.session.add(User(username="admin",full_name="Administrator",
                            password_hash=generate_password_hash("Azm@2026"),
                            role="ADMIN",active=True))
        db.session.commit()

def money(v,cur):
    v=float(v or 0)
    return f"{v:,.0f}" if cur=="IQD" else f"{v:,.2f}"

def parse_date(s):
    try: return date.fromisoformat(s)
    except: return None

def current_user():
    uid=session.get("uid")
    if not uid: return None
    u=db.session.get(User,uid)
    if not u or not u.active:
        session.clear(); return None
    return u

def audit(action,details=""):
    u=current_user()
    db.session.add(AuditLog(user_id=u.id if u else None,action=action,details=details))
    db.session.commit()

def balance(cur,before=None):
    signed=case((Transaction.transaction_type=="IN",Transaction.amount),else_=-Transaction.amount)
    q=db.session.query(func.coalesce(func.sum(signed),0)).filter(Transaction.currency==cur)
    if before: q=q.filter(Transaction.transaction_date<before)
    return float(q.scalar() or 0)

def tx_rows(frm=None,to=None):
    rows=Transaction.query.order_by(Transaction.transaction_date.asc(),Transaction.id.asc()).all()
    running={"USD":0.0,"IQD":0.0}; out=[]
    for tx in rows:
        running[tx.currency]+=float(tx.amount or 0)*(1 if tx.transaction_type=="IN" else -1)
        if frm and tx.transaction_date<frm: continue
        if to and tx.transaction_date>to: continue
        out.append({"id":tx.id,"transaction_type":tx.transaction_type,"amount":float(tx.amount or 0),
                    "currency":tx.currency,"person_name":tx.person_name,"related_customer":tx.related_customer,
                    "destination":tx.destination,"receiver_name":tx.receiver_name,
                    "transaction_date":tx.transaction_date.isoformat(),"notes":tx.notes or "",
                    "balance_after":running[tx.currency]})
    return list(reversed(out))

def period_summary(frm=None,to=None):
    r={}
    for cur in CURRENCIES:
        opening=balance(cur,frm) if frm else 0.0
        q=Transaction.query.filter_by(currency=cur)
        if frm: q=q.filter(Transaction.transaction_date>=frm)
        if to: q=q.filter(Transaction.transaction_date<=to)
        tin=sum(float(x.amount) for x in q.filter_by(transaction_type="IN").all())
        q2=Transaction.query.filter_by(currency=cur)
        if frm: q2=q2.filter(Transaction.transaction_date>=frm)
        if to: q2=q2.filter(Transaction.transaction_date<=to)
        tout=sum(float(x.amount) for x in q2.filter_by(transaction_type="OUT").all())
        closing=opening+tin-tout if frm else balance(cur)
        r[cur]={"opening":opening,"in":tin,"out":tout,"closing":closing}
    return r

@app.context_processor
def inject():
    return dict(current_user=current_user(),money=money,roles=ROLES,today=date.today().isoformat())

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
            if u.role not in roles: abort(403)
            return fn(*a,**kw)
        return w
    return deco

@app.route("/login",methods=["GET","POST"])
def login():
    if current_user(): return redirect(url_for("index"))
    if request.method=="POST":
        username=request.form.get("username","").strip(); password=request.form.get("password","")
        u=User.query.filter(func.lower(User.username)==username.lower()).first()
        if u and u.active and check_password_hash(u.password_hash,password):
            session.clear(); session.permanent=True; session["uid"]=u.id
            u.last_login=func.now(); db.session.commit(); audit("LOGIN",f"user={u.username}")
            return redirect(url_for("index"))
        flash("اسم المستخدم أو الرقم السري غير صحيح.","error")
    return render_template("login.html")

@app.post("/logout")
def logout():
    if current_user(): audit("LOGOUT")
    session.clear(); return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    fs=request.args.get("from",""); ts=request.args.get("to","")
    frm=parse_date(fs) if fs else None; to=parse_date(ts) if ts else None
    return render_template("index.html",rows=tx_rows(frm,to),frm=fs,to=ts,
                           usd=balance("USD"),iqd=balance("IQD"),summ=period_summary(frm,to))

@app.route("/income",methods=["GET","POST"])
@roles_required("ADMIN","FULL")
def income():
    if request.method=="POST":
        try: amount=float(request.form.get("amount","0"))
        except: amount=0
        cur=request.form.get("currency",""); person=request.form.get("person_name","").strip()
        customer=request.form.get("related_customer","").strip(); dt=parse_date(request.form.get("transaction_date","")) or date.today()
        notes=request.form.get("notes","").strip()
        if amount<=0 or cur not in CURRENCIES or not person:
            flash("تأكد من المبلغ والعملة واسم الشخص.","error")
        else:
            tx=Transaction(transaction_type="IN",amount=amount,currency=cur,person_name=person,
                           related_customer=customer,transaction_date=dt,notes=notes,created_by=current_user().id)
            db.session.add(tx); db.session.commit(); audit("ADD_INCOME",f"id={tx.id}")
            flash("تم إدخال الأموال وتحديث الرصيد.","success"); return redirect(url_for("index"))
    return render_template("income.html",usd=balance("USD"),iqd=balance("IQD"))

@app.route("/expense",methods=["GET","POST"])
@roles_required("ADMIN","FULL")
def expense():
    if request.method=="POST":
        try: amount=float(request.form.get("amount","0"))
        except: amount=0
        cur=request.form.get("currency",""); dest=request.form.get("destination","").strip()
        receiver=request.form.get("receiver_name","").strip(); dt=parse_date(request.form.get("transaction_date","")) or date.today()
        notes=request.form.get("notes","").strip()
        if amount<=0 or cur not in CURRENCIES or not dest or not receiver:
            flash("تأكد من المبلغ والعملة والجهة والمستلم.","error")
        else:
            tx=Transaction(transaction_type="OUT",amount=amount,currency=cur,destination=dest,receiver_name=receiver,
                           transaction_date=dt,notes=notes,created_by=current_user().id)
            db.session.add(tx); db.session.commit(); audit("ADD_EXPENSE",f"id={tx.id}")
            flash(f"تم تسجيل الخروج. الرصيد الحالي: {money(balance(cur),cur)} {cur}","success")
            return redirect(url_for("index"))
    return render_template("expense.html",usd=balance("USD"),iqd=balance("IQD"))

@app.post("/delete/<int:tid>")
@roles_required("ADMIN","FULL")
def delete_tx(tid):
    tx=db.session.get(Transaction,tid)
    if tx:
        audit("DELETE_TX",f"id={tx.id};type={tx.transaction_type};amount={tx.amount};cur={tx.currency}")
        db.session.delete(tx); db.session.commit(); flash("تم حذف الحركة وإعادة احتساب الرصيد.","success")
    return redirect(request.referrer or url_for("index"))

@app.route("/users")
@roles_required("ADMIN")
def users():
    return render_template("users.html",users=User.query.order_by(User.id.asc()).all())

@app.post("/users/add")
@roles_required("ADMIN")
def add_user():
    username=request.form.get("username","").strip(); full=request.form.get("full_name","").strip()
    pw=request.form.get("password",""); role=request.form.get("role","")
    if not username or not full or len(pw)<6 or role not in ROLES:
        flash("أكمل البيانات، والرقم السري 6 أحرف على الأقل.","error"); return redirect(url_for("users"))
    if User.query.filter(func.lower(User.username)==username.lower()).first():
        flash("اسم المستخدم موجود مسبقاً.","error"); return redirect(url_for("users"))
    u=User(username=username,full_name=full,password_hash=generate_password_hash(pw),role=role,active=True)
    db.session.add(u); db.session.commit(); audit("ADD_USER",f"user={username};role={role}")
    flash("تمت إضافة المستخدم.","success"); return redirect(url_for("users"))

@app.post("/users/<int:uid>/toggle")
@roles_required("ADMIN")
def toggle_user(uid):
    if uid==current_user().id:
        flash("لا يمكنك إيقاف حسابك الحالي.","error"); return redirect(url_for("users"))
    u=db.session.get(User,uid)
    if u:
        u.active=not u.active; db.session.commit(); audit("TOGGLE_USER",f"user={u.username};active={u.active}")
    return redirect(url_for("users"))

@app.post("/users/<int:uid>/password")
@roles_required("ADMIN")
def change_password(uid):
    pw=request.form.get("password","")
    if len(pw)<6:
        flash("الرقم السري يجب أن يكون 6 أحرف على الأقل.","error"); return redirect(url_for("users"))
    u=db.session.get(User,uid)
    if u:
        u.password_hash=generate_password_hash(pw); db.session.commit(); audit("CHANGE_PASSWORD",f"user={u.username}")
        flash("تم تغيير الرقم السري.","success")
    return redirect(url_for("users"))

@app.post("/users/<int:uid>/delete")
@roles_required("ADMIN")
def delete_user(uid):
    if uid==current_user().id:
        flash("لا يمكنك حذف حسابك الحالي.","error"); return redirect(url_for("users"))
    u=db.session.get(User,uid)
    if u:
        name=u.username; db.session.delete(u); db.session.commit(); audit("DELETE_USER",f"user={name}")
        flash("تم حذف المستخدم.","success")
    return redirect(url_for("users"))

def arabic(s):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(str(s or "")))
    except: return str(s or "")

def pdf_font():
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
    fs=request.args.get("from",""); ts=request.args.get("to","")
    frm=parse_date(fs) if fs else None; to=parse_date(ts) if ts else None
    rows=tx_rows(frm,to); sm=period_summary(frm,to); font=pdf_font()
    b=io.BytesIO(); doc=SimpleDocTemplate(b,pagesize=landscape(A4),rightMargin=18,leftMargin=18,topMargin=22,bottomMargin=18)
    styles=getSampleStyleSheet(); title=ParagraphStyle("t",parent=styles["Title"],fontName=font,alignment=TA_CENTER)
    story=[Paragraph(arabic("شركة عزم الشرق - كشف حساب الصندوق"),title),
           Paragraph(arabic(f"الفترة: {fs or 'البداية'} إلى {ts or date.today().isoformat()}"),title),Spacer(1,10)]
    sd=[[arabic(x) for x in ["العملة","الرصيد الافتتاحي","إجمالي الدخول","إجمالي الخروج","الرصيد النهائي"]]]
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
        ("FONTNAME",(0,0),(-1,-1),font),("FONTSIZE",(0,0),(-1,-1),7),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#111827")),
        ("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),.35,colors.grey),("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#F7F7F7")])
    ])); story.append(t); doc.build(story); b.seek(0); audit("PDF_EXPORT",f"from={fs};to={ts}")
    return send_file(b,as_attachment=True,download_name=f"Azm_Cashbox_{fs or 'start'}_{ts or date.today()}.pdf",mimetype="application/pdf")

@app.route("/manifest.webmanifest")
def manifest():
    return jsonify({"name":APP_NAME,"short_name":"عزم الشرق","start_url":"/","display":"standalone",
                    "background_color":"#f4f6f8","theme_color":"#111827","lang":"ar","dir":"rtl",
                    "icons":[{"src":"/static/icon.svg","sizes":"any","type":"image/svg+xml"}]})

@app.route("/service-worker.js")
def sw():
    return app.response_class("self.addEventListener('fetch',e=>{e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)))})",mimetype="application/javascript")

@app.route("/health")
def health(): return {"status":"ok","database":"connected"}

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","8501")),debug=False)
