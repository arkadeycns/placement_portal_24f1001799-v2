from flask import Flask, request, jsonify, send_from_directory
from flask_caching import Cache
from flask_cors import CORS
from flask_login import LoginManager, login_required, login_user, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from celery import Celery
from celery.schedules import crontab
from dotenv import load_dotenv
from models import db, User, Company, Student, Drive, Application
from datetime import datetime, timezone
from sqlalchemy import or_
from io import StringIO
import os, csv, time, smtplib
from email.message import EmailMessage

load_dotenv()

# ── App Setup ──────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("SQLALCHEMY_DATABASE_URI", "sqlite:///placement_portal.db")
app.config["UPLOAD_FOLDER"] = os.path.join(app.instance_path, "uploads")
app.config["CACHE_TYPE"] = os.environ.get("CACHE_TYPE", "RedisCache")
app.config["CACHE_REDIS_URL"] = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
app.config["CACHE_DEFAULT_TIMEOUT"] = 300
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

db.init_app(app)
cache = Cache(app)
CORS(app, supports_credentials=True)

login_manager = LoginManager()
login_manager.init_app(app)

celery_app = Celery(
    app.import_name,
    broker=os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/0"),
)
celery_app.conf.update(timezone="UTC", beat_schedule={
    "daily-reminders": {"task": "run.send_reminders", "schedule": crontab(hour=8, minute=0)},
    "monthly-report":  {"task": "run.monthly_report", "schedule": crontab(day_of_month=1, hour=0, minute=0)},
})


# ── Helpers ────────────────────────────────────────────────────────────────────

@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))


@login_manager.unauthorized_handler
def unauthorized():
    return err("Login required", 401)


def ok(data=None, msg="Success", status=200):
    """Standard success response."""
    return jsonify({"ok": True, "message": msg, "data": data}), status


def err(msg, status=400):
    """Standard error response."""
    return jsonify({"ok": False, "error": msg}), status


def check_role(*roles):
    """Returns an error response if user doesn't have one of the given roles, else None."""
    if not current_user.is_active:
        return err("Account deactivated", 403)
    if current_user.role not in roles:
        return err("Not authorized", 403)
    return None


def get_json():
    """Read request body as dict — works for both JSON and form data."""
    return request.get_json(silent=True) or request.form.to_dict()


# ── Serializers (model → dict for JSON) ───────────────────────────────────────

def user_dict(u):
    d = {"id": u.id, "email": u.email, "role": u.role, "name": u.name,
         "approved": u.approved, "is_active": u.is_active,
         "created_at": u.created_at.isoformat() if u.created_at else None}
    if u.company:
        d["company"] = company_dict(u.company)
    if u.student:
        d["student"] = student_dict(u.student)
    return d


def company_dict(c):
    return {"id": c.id, "name": c.name, "hr_contact": c.hr_contact,
            "website": c.website, "industry": c.industry}


def student_dict(s):
    return {"id": s.id, "name": s.name, "department": s.department,
            "branch": s.branch, "cgpa": s.cgpa,
            "graduation_year": s.graduation_year,
            "phone": s.phone, "resume": s.resume}


def drive_dict(d):
    result = {"id": d.id, "company_id": d.company_id, "title": d.title,
              "description": d.description, "eligibility_branch": d.eligibility_branch,
              "eligibility_cgpa": d.eligibility_cgpa, "eligibility_year": d.eligibility_year,
              "location": d.location, "ctc": d.ctc,
              "deadline": d.deadline.isoformat() if d.deadline else None,
              "status": d.status,
              "created_at": d.created_at.isoformat() if d.created_at else None}
    if d.company:
        result["company_name"] = d.company.name
    return result


def app_dict(a):
    result = {"id": a.id, "student_id": a.student_id, "drive_id": a.drive_id,
              "status": a.status, "interview_date": a.interview_date,
              "interview_mode": a.interview_mode,
              "applied_at": a.applied_at.isoformat() if a.applied_at else None}
    if a.student:
        result["student_name"] = a.student.name
        result["student_department"] = a.student.department
        result["student_cgpa"] = a.student.cgpa
    if a.drive:
        result["drive_title"] = a.drive.title
        if a.drive.company:
            result["company_name"] = a.drive.company.name
    return result


# ── Caching ────────────────────────────────────────────────────────────────────

def get_stats():
    """Get admin dashboard stats (cached in Redis for 60s)."""
    try:
        stats = cache.get("admin_stats")
        if stats:
            return stats
    except Exception:
        pass
    stats = {
        "total_students": User.query.filter_by(role="student").count(),
        "total_companies": User.query.filter_by(role="company").count(),
        "total_drives": Drive.query.count(),
        "total_applications": Application.query.count(),
    }
    try:
        cache.set("admin_stats", stats, timeout=60)
    except Exception:
        pass
    return stats


def clear_cache():
    """Delete cached data so they refresh on next request."""
    try:
        cache.delete("admin_stats")
        cache.delete("approved_drives")
        cache.delete("admin_reports")
    except Exception:
        pass


def get_approved_drives():
    """Get approved drives (cached for 120s)."""
    try:
        result = cache.get("approved_drives")
        if result is not None:
            return result
    except Exception:
        pass
    drives = Drive.query.filter_by(status="Approved").order_by(Drive.created_at.desc()).all()
    result = [drive_dict(d) for d in drives]
    try:
        cache.set("approved_drives", result, timeout=120)
    except Exception:
        pass
    return result


# ── Static Files ───────────────────────────────────────────────────────────────

FRONTEND = os.path.join(os.path.dirname(__file__), "frontend")


@app.route("/")
def home():
    return send_from_directory(FRONTEND, "index.html")


@app.route("/frontend/<path:filename>")
def frontend_files(filename):
    return send_from_directory(FRONTEND, filename)


# ── Auth Routes ────────────────────────────────────────────────────────────────

@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = get_json()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password, password):
        return err("Invalid credentials", 401)
    if not user.is_active:
        return err("Account deactivated", 403)
    if not user.approved:
        return err("Account pending approval", 403)

    login_user(user)
    return ok({"user": user_dict(user)}, "Logged in")


@app.route("/api/auth/logout", methods=["POST"])
@login_required
def api_logout():
    logout_user()
    return ok(msg="Logged out")


@app.route("/api/auth/register/student", methods=["POST"])
def api_register_student():
    data = get_json()
    name = data.get("name", "").strip()
    department = data.get("department", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not all([name, department, email, password]):
        return err("Name, department, email, password required")
    if len(password) < 8:
        return err("Password must be 8+ characters")
    if User.query.filter_by(email=email).first():
        return err("Email already registered", 409)

    # Parse optional numeric fields
    cgpa = None
    grad_year = None
    if data.get("cgpa"):
        try:
            cgpa = float(data["cgpa"])
        except ValueError:
            return err("CGPA must be a number")
    if data.get("graduation_year"):
        try:
            grad_year = int(data["graduation_year"])
        except ValueError:
            return err("Graduation year must be a number")

    user = User(email=email, password=generate_password_hash(password), role="student", name=name)
    student = Student(user=user, name=name, department=department,
                      branch=data.get("branch", "").strip() or None,
                      cgpa=cgpa, graduation_year=grad_year,
                      phone=data.get("phone", "").strip() or None)
    db.session.add_all([user, student])
    db.session.commit()
    return ok({"user": user_dict(user)}, "Registered. Awaiting approval.", 201)


@app.route("/api/auth/register/company", methods=["POST"])
def api_register_company():
    data = get_json()
    name = data.get("name", "").strip()
    hr_contact = data.get("hr_contact", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not all([name, hr_contact, email, password]):
        return err("Company name, HR contact, email, password required")
    if len(password) < 8:
        return err("Password must be 8+ characters")
    if User.query.filter_by(email=email).first():
        return err("Email already registered", 409)

    user = User(email=email, password=generate_password_hash(password), role="company", name=name)
    company = Company(user=user, name=name, hr_contact=hr_contact,
                      website=data.get("website", "").strip() or None,
                      industry=data.get("industry", "").strip() or None)
    db.session.add_all([user, company])
    db.session.commit()
    return ok({"user": user_dict(user)}, "Registered. Awaiting approval.", 201)


# ── Current User ───────────────────────────────────────────────────────────────

@app.route("/api/me")
@login_required
def api_me():
    return ok({"user": user_dict(current_user)})


@app.route("/api/health")
def api_health():
    return ok({"status": "healthy"})


# ── Admin Routes ───────────────────────────────────────────────────────────────

@app.route("/api/admin/summary")
@login_required
def api_admin_summary():
    role_err = check_role("admin")
    if role_err:
        return role_err
    return ok({"stats": get_stats()})


@app.route("/api/admin/users")
@login_required
def api_admin_users():
    role_err = check_role("admin")
    if role_err:
        return role_err

    query = User.query.filter(User.role != "admin")
    role_filter = request.args.get("role", "")
    search = request.args.get("q", "")
    if role_filter in ("student", "company"):
        query = query.filter(User.role == role_filter)
    if search:
        query = query.filter(or_(User.email.ilike(f"%{search}%"), User.name.ilike(f"%{search}%")))

    users = query.order_by(User.created_at.desc()).all()
    return ok({"users": [user_dict(u) for u in users]})


@app.route("/api/admin/users/<int:uid>/approve", methods=["POST"])
@login_required
def api_approve_user(uid):
    role_err = check_role("admin")
    if role_err:
        return role_err
    user = db.session.get(User, uid)
    if not user:
        return err("User not found", 404)
    user.approved = True
    user.is_active = True
    db.session.commit()
    clear_cache()
    return ok({"user": user_dict(user)}, "User approved")


@app.route("/api/admin/users/<int:uid>/deactivate", methods=["POST"])
@login_required
def api_deactivate_user(uid):
    role_err = check_role("admin")
    if role_err:
        return role_err
    user = db.session.get(User, uid)
    if not user:
        return err("User not found", 404)
    if user.role == "admin":
        return err("Cannot deactivate admin")
    user.is_active = False
    user.approved = False
    db.session.commit()
    clear_cache()
    return ok({"user": user_dict(user)}, "User deactivated")


@app.route("/api/admin/users/<int:uid>/reactivate", methods=["POST"])
@login_required
def api_reactivate_user(uid):
    role_err = check_role("admin")
    if role_err:
        return role_err
    user = db.session.get(User, uid)
    if not user:
        return err("User not found", 404)
    user.is_active = True
    user.approved = True
    db.session.commit()
    clear_cache()
    return ok({"user": user_dict(user)}, "User reactivated")


@app.route("/api/admin/drives")
@login_required
def api_admin_drives():
    role_err = check_role("admin")
    if role_err:
        return role_err

    query = Drive.query
    status = request.args.get("status", "")
    if status in ("Pending", "Approved", "Closed"):
        query = query.filter(Drive.status == status)

    drives = query.order_by(Drive.created_at.desc()).all()
    return ok({"drives": [drive_dict(d) for d in drives]})


@app.route("/api/admin/drives/<int:did>/approve", methods=["POST"])
@login_required
def api_approve_drive(did):
    role_err = check_role("admin")
    if role_err:
        return role_err
    drive = db.session.get(Drive, did)
    if not drive:
        return err("Drive not found", 404)
    drive.status = "Approved"
    db.session.commit()
    clear_cache()
    return ok({"drive": drive_dict(drive)}, "Drive approved")


@app.route("/api/admin/drives/<int:did>/reject", methods=["POST"])
@login_required
def api_reject_drive(did):
    role_err = check_role("admin")
    if role_err:
        return role_err
    drive = db.session.get(Drive, did)
    if not drive:
        return err("Drive not found", 404)
    drive.status = "Closed"
    db.session.commit()
    clear_cache()
    return ok({"drive": drive_dict(drive)}, "Drive rejected")


@app.route("/api/admin/applications")
@login_required
def api_admin_applications():
    role_err = check_role("admin")
    if role_err:
        return role_err

    query = Application.query
    status = request.args.get("status", "")
    search = request.args.get("q", "")
    if status in ("Applied", "Shortlisted", "Selected", "Rejected"):
        query = query.filter(Application.status == status)
    if search:
        query = (query.join(Student).join(Drive).join(Company)
                 .filter(or_(Student.name.ilike(f"%{search}%"),
                             Company.name.ilike(f"%{search}%"),
                             Drive.title.ilike(f"%{search}%"))))

    apps = query.order_by(Application.applied_at.desc()).all()
    return ok({"applications": [app_dict(a) for a in apps]})


@app.route("/api/admin/reports")
@login_required
def api_admin_reports():
    role_err = check_role("admin")
    if role_err:
        return role_err

    # Check cache
    try:
        cached = cache.get("admin_reports")
        if cached:
            return ok(cached)
    except Exception:
        pass

    total_drives = Drive.query.count()
    approved_drives = Drive.query.filter_by(status="Approved").count()
    pending_drives = Drive.query.filter_by(status="Pending").count()
    closed_drives = Drive.query.filter_by(status="Closed").count()

    total_apps = Application.query.count()
    applied = Application.query.filter_by(status="Applied").count()
    shortlisted = Application.query.filter_by(status="Shortlisted").count()
    selected = Application.query.filter_by(status="Selected").count()
    rejected = Application.query.filter_by(status="Rejected").count()
    placement_rate = round(selected / total_apps * 100, 1) if total_apps else 0

    companies = Company.query.all()
    company_stats = []
    for c in companies:
        c_drives = Drive.query.filter_by(company_id=c.id).count()
        c_apps = Application.query.join(Drive).filter(Drive.company_id == c.id).count()
        c_sel = Application.query.join(Drive).filter(
            Drive.company_id == c.id, Application.status == "Selected").count()
        company_stats.append({"name": c.name, "drives": c_drives,
                              "applications": c_apps, "selected": c_sel})

    result = {
        "drives": {"total": total_drives, "approved": approved_drives,
                   "pending": pending_drives, "closed": closed_drives},
        "applications": {"total": total_apps, "applied": applied,
                         "shortlisted": shortlisted, "selected": selected,
                         "rejected": rejected},
        "placement_rate": placement_rate,
        "company_stats": company_stats,
    }

    try:
        cache.set("admin_reports", result, timeout=60)
    except Exception:
        pass
    return ok(result)


# ── Company Routes ─────────────────────────────────────────────────────────────────

@app.route("/api/company/profile", methods=["GET", "PUT"])
@login_required
def api_company_profile():
    role_err = check_role("company")
    if role_err:
        return role_err
    company = Company.query.filter_by(user_id=current_user.id).first()
    if not company:
        return err("Company not found", 404)

    if request.method == "PUT":
        data = get_json()
        company.name = data.get("name", "").strip() or company.name
        company.hr_contact = data.get("hr_contact", "").strip() or company.hr_contact
        company.website = data.get("website", "").strip() or None
        company.industry = data.get("industry", "").strip() or None
        db.session.commit()

    return ok({"company": company_dict(company)})


@app.route("/api/company/drives", methods=["POST"])
@login_required
def api_create_drive():
    role_err = check_role("company")
    if role_err:
        return role_err
    company = Company.query.filter_by(user_id=current_user.id).first()
    if not company:
        return err("Company not found", 404)
    if not current_user.approved:
        return err("Company not approved yet", 403)

    data = get_json()
    title = data.get("title", "").strip()
    description = data.get("description", "").strip()
    if not all([title, description]):
        return err("Title and description required")

    # Parse optional fields
    deadline = None
    if data.get("deadline"):
        try:
            deadline = datetime.fromisoformat(data["deadline"].replace("Z", "+00:00"))
        except ValueError:
            return err("Invalid deadline format")

    cgpa = None
    year = None
    if data.get("eligibility_cgpa"):
        try:
            cgpa = float(data["eligibility_cgpa"])
        except ValueError:
            return err("CGPA must be a number")
    if data.get("eligibility_year"):
        try:
            year = int(data["eligibility_year"])
        except ValueError:
            return err("Year must be a number")

    drive = Drive(company_id=company.id, title=title, description=description,
                  eligibility_branch=data.get("eligibility_branch", "").strip() or None,
                  eligibility_cgpa=cgpa, eligibility_year=year,
                  location=data.get("location", "").strip() or None,
                  ctc=data.get("ctc", "").strip() or None,
                  deadline=deadline, status="Pending")
    db.session.add(drive)
    db.session.commit()
    clear_cache()
    return ok({"drive": drive_dict(drive)}, "Drive created. Pending approval.", 201)


@app.route("/api/company/drives/<int:did>/close", methods=["POST"])
@login_required
def api_close_drive(did):
    role_err = check_role("company")
    if role_err:
        return role_err
    company = Company.query.filter_by(user_id=current_user.id).first()
    drive = db.session.get(Drive, did)
    if not company or not drive or drive.company_id != company.id:
        return err("Drive not found", 404)
    drive.status = "Closed"
    db.session.commit()
    return ok({"drive": drive_dict(drive)}, "Drive closed")


@app.route("/api/company/drives/<int:did>/applications")
@login_required
def api_drive_applications(did):
    role_err = check_role("company")
    if role_err:
        return role_err
    company = Company.query.filter_by(user_id=current_user.id).first()
    drive = db.session.get(Drive, did)
    if not company or not drive or drive.company_id != company.id:
        return err("Drive not found", 404)

    apps = Application.query.filter_by(drive_id=did).order_by(Application.applied_at.desc()).all()
    return ok({"drive": drive_dict(drive), "applications": [app_dict(a) for a in apps]})


# ── Application Status Routes (Company + Admin) ───────────────────────────────

@app.route("/api/applications/<int:aid>/status", methods=["POST"])
@login_required
def api_update_app_status(aid):
    role_err = check_role("company", "admin")
    if role_err:
        return role_err

    application = db.session.get(Application, aid)
    if not application:
        return err("Application not found", 404)

    # Company can only update their own drive's applications
    if current_user.role == "company":
        company = Company.query.filter_by(user_id=current_user.id).first()
        if not company or application.drive.company_id != company.id:
            return err("Not authorized", 403)

    data = get_json()
    new_status = data.get("status", "")
    if new_status not in ("Applied", "Shortlisted", "Selected", "Rejected"):
        return err("Invalid status")

    application.status = new_status
    db.session.commit()
    clear_cache()
    return ok({"application": app_dict(application)}, "Status updated")


@app.route("/api/applications/<int:aid>/interview", methods=["POST"])
@login_required
def api_schedule_interview(aid):
    role_err = check_role("company", "admin")
    if role_err:
        return role_err

    application = db.session.get(Application, aid)
    if not application:
        return err("Application not found", 404)

    if current_user.role == "company":
        company = Company.query.filter_by(user_id=current_user.id).first()
        if not company or application.drive.company_id != company.id:
            return err("Not authorized", 403)

    data = get_json()
    if not data.get("interview_date"):
        return err("Interview date required")

    application.interview_date = data["interview_date"]
    application.interview_mode = data.get("interview_mode", "")
    if application.status == "Applied":
        application.status = "Shortlisted"
    db.session.commit()
    return ok({"application": app_dict(application)}, "Interview scheduled")


# ── Student Routes ─────────────────────────────────────────────────────────────

@app.route("/api/student/profile", methods=["GET", "PUT"])
@login_required
def api_student_profile():
    role_err = check_role("student")
    if role_err:
        return role_err
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        return err("Student not found", 404)

    if request.method == "PUT":
        data = get_json()
        student.name = data.get("name", "").strip() or student.name
        student.department = data.get("department", "").strip() or student.department
        student.branch = data.get("branch", "").strip() or student.branch
        student.phone = data.get("phone", "").strip() or student.phone
        if data.get("cgpa"):
            try:
                student.cgpa = float(data["cgpa"])
            except ValueError:
                pass
        if data.get("graduation_year"):
            try:
                student.graduation_year = int(data["graduation_year"])
            except ValueError:
                pass

        # Handle resume file upload
        resume_file = request.files.get("resume_file")
        if resume_file and resume_file.filename:
            filename = secure_filename(f"student_{student.id}_{int(time.time())}_{resume_file.filename}")
            resume_file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            student.resume = f"uploads/{filename}"

        db.session.commit()

    return ok({"student": student_dict(student)})


@app.route("/api/student/applications")
@login_required
def api_student_applications():
    role_err = check_role("student")
    if role_err:
        return role_err
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        return err("Student not found", 404)

    apps = Application.query.filter_by(student_id=student.id).order_by(Application.applied_at.desc()).all()
    return ok({"applications": [app_dict(a) for a in apps]})


# ── Shared Routes ──────────────────────────────────────────────────────────────

@app.route("/api/dashboard")
@login_required
def api_dashboard():
    # Admin dashboard
    if current_user.role == "admin":
        return ok({"role": "admin", "stats": get_stats()})

    # Company dashboard
    if current_user.role == "company":
        company = Company.query.filter_by(user_id=current_user.id).first()
        if not company:
            return err("Company not found", 404)
        drives = Drive.query.filter_by(company_id=company.id).order_by(Drive.created_at.desc()).all()
        drive_data = []
        for d in drives:
            dd = drive_dict(d)
            dd["app_count"] = Application.query.filter_by(drive_id=d.id).count()
            drive_data.append(dd)
        return ok({"role": "company", "company": company_dict(company), "drives": drive_data})

    # Student dashboard
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        return err("Student not found", 404)
    apps = Application.query.filter_by(student_id=student.id).order_by(Application.applied_at.desc()).all()
    return ok({"role": "student", "student": student_dict(student),
               "open_drives": get_approved_drives(),
               "applications": [app_dict(a) for a in apps]})


@app.route("/api/drives")
@login_required
def api_drives():
    query = Drive.query
    if current_user.role == "student":
        query = query.filter(Drive.status == "Approved")
    elif current_user.role == "company":
        company = Company.query.filter_by(user_id=current_user.id).first()
        if company:
            query = query.filter(Drive.company_id == company.id)

    search = request.args.get("q", "")
    status = request.args.get("status", "")
    branch = request.args.get("branch", "")
    cgpa = request.args.get("cgpa", "")
    year = request.args.get("year", "")

    if status in ("Pending", "Approved", "Closed"):
        query = query.filter(Drive.status == status)
    if search:
        query = query.filter(or_(Drive.title.ilike(f"%{search}%"),
                                 Drive.description.ilike(f"%{search}%")))
    if branch:
        query = query.filter(or_(Drive.eligibility_branch.is_(None),
                                 Drive.eligibility_branch.ilike(f"%{branch}%")))
    if cgpa:
        try:
            cgpa_val = float(cgpa)
            query = query.filter(or_(Drive.eligibility_cgpa.is_(None),
                                     Drive.eligibility_cgpa <= cgpa_val))
        except ValueError:
            pass
    if year:
        try:
            year_val = int(year)
            query = query.filter(or_(Drive.eligibility_year.is_(None),
                                     Drive.eligibility_year == year_val))
        except ValueError:
            pass

    drives = query.order_by(Drive.created_at.desc()).all()
    return ok({"drives": [drive_dict(d) for d in drives]})


@app.route("/api/drives/<int:did>/apply", methods=["POST"])
@login_required
def api_apply_drive(did):
    role_err = check_role("student")
    if role_err:
        return role_err
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        return err("Student not found", 404)

    drive = db.session.get(Drive, did)
    if not drive or drive.status != "Approved":
        return err("Drive not available")

    # Check deadline
    if drive.deadline:
        now = datetime.now(timezone.utc)
        dl = drive.deadline if drive.deadline.tzinfo else drive.deadline.replace(tzinfo=timezone.utc)
        if dl < now:
            return err("Deadline has passed")

    # Eligibility checks
    if drive.eligibility_branch and student.branch:
        if student.branch.lower() != drive.eligibility_branch.lower():
            return err(f"Branch must be {drive.eligibility_branch}")
    if drive.eligibility_cgpa is not None and student.cgpa is not None:
        if student.cgpa < drive.eligibility_cgpa:
            return err(f"Minimum CGPA required: {drive.eligibility_cgpa}")
    if drive.eligibility_year and student.graduation_year:
        if student.graduation_year != drive.eligibility_year:
            return err(f"Graduation year must be {drive.eligibility_year}")

    # Check duplicate application
    if Application.query.filter_by(student_id=student.id, drive_id=did).first():
        return err("Already applied to this drive")

    application = Application(student_id=student.id, drive_id=did)
    db.session.add(application)
    db.session.commit()
    clear_cache()
    return ok(msg="Application submitted", status=201)


@app.route("/api/search")
@login_required
def api_search():
    entity = request.args.get("entity", "drives")
    q = request.args.get("q", "")

    if entity == "students" and current_user.role == "admin":
        query = Student.query
        if q:
            query = query.filter(or_(Student.name.ilike(f"%{q}%"),
                                     Student.department.ilike(f"%{q}%")))
        return ok({"entity": "students", "results": [student_dict(s) for s in query.all()]})

    elif entity == "companies":
        query = Company.query
        if q:
            query = query.filter(or_(Company.name.ilike(f"%{q}%"),
                                     Company.industry.ilike(f"%{q}%")))
        return ok({"entity": "companies", "results": [company_dict(c) for c in query.all()]})

    else:
        query = Drive.query
        if current_user.role == "student":
            query = query.filter(Drive.status == "Approved")
        if q:
            query = query.filter(or_(Drive.title.ilike(f"%{q}%"),
                                     Drive.description.ilike(f"%{q}%")))
        return ok({"entity": "drives", "results": [drive_dict(d) for d in query.all()]})


# ── Email Helper ───────────────────────────────────────────────────────────────

def send_mail(subject, body, recipient, html=False):
    """Send an email. Returns False if mail is not configured."""
    server = os.environ.get("MAIL_SERVER", "")
    if not server or not recipient:
        print(f"[MAIL] Skipped: server={'(empty)' if not server else server}, recipient={recipient}")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("MAIL_USERNAME", "admin@institute.edu")
    msg["To"] = recipient
    if html:
        msg.set_content(body, subtype='html')
    else:
        msg.set_content(body)
    try:
        with smtplib.SMTP(server, int(os.environ.get("MAIL_PORT", 587))) as s:
            s.starttls()
            username = os.environ.get("MAIL_USERNAME", "")
            if username:
                s.login(username, os.environ.get("MAIL_PASSWORD", ""))
            s.send_message(msg)
        print(f"[MAIL] ✅ Sent '{subject}' to {recipient}")
        return True
    except Exception as e:
        print(f"[MAIL] ❌ Failed to send '{subject}' to {recipient}: {e}")
        return False


# ── Celery Background Tasks ───────────────────────────────────────────────────

@celery_app.task(name="run.send_reminders")
def send_reminders():
    """Daily job: email students about upcoming drive deadlines."""
    with app.app_context():
        drives = Drive.query.filter(Drive.status == "Approved", Drive.deadline.isnot(None)).all()
        if not drives:
            return {"sent": 0}
        students = Student.query.join(User).filter(User.is_active == True, User.approved == True).all()
        sent = 0
        for s in students:
            lines = [f"- {d.title} (Deadline: {d.deadline})" for d in drives]
            body = "Upcoming placement drives:\n" + "\n".join(lines)
            if send_mail("Placement Portal - Reminders", body, s.user.email):
                sent += 1
        return {"sent": sent}


@celery_app.task(name="run.monthly_report")
def monthly_report():
    """Monthly job: email admin a placement activity report."""
    with app.app_context():
        month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        drives = Drive.query.filter(Drive.created_at >= month_start).count()
        applied = Application.query.filter(Application.applied_at >= month_start).count()
        selected = Application.query.filter(
            Application.applied_at >= month_start, Application.status == "Selected").count()

        rate = round(selected / applied * 100, 1) if applied else 0
        report = (
            "<html><head><style>"
            "body{font-family:Arial,sans-serif;margin:40px;color:#333}"
            "h2{color:#2c3e50;border-bottom:2px solid #3498db;padding-bottom:10px}"
            "table{border-collapse:collapse;width:100%;margin-top:20px}"
            "th,td{border:1px solid #ddd;padding:12px;text-align:left}"
            "th{background:#3498db;color:#fff}"
            "</style></head><body>"
            "<h2>Monthly Placement Activity Report</h2>"
            f"<p><strong>Report Period:</strong> {month_start.strftime('%B %Y')}</p>"
            "<table><tr><th>Metric</th><th>Count</th></tr>"
            f"<tr><td>Drives Conducted</td><td>{drives}</td></tr>"
            f"<tr><td>Students Applied</td><td>{applied}</td></tr>"
            f"<tr><td>Students Selected</td><td>{selected}</td></tr>"
            f"<tr><td>Selection Rate</td><td>{rate}%</td></tr>"
            "</table>"
            "<p style='margin-top:20px;color:#666;font-size:12px'>"
            "Auto-generated by Placement Portal</p>"
            "</body></html>")
        send_mail("Monthly Placement Report", report,
                  os.environ.get("ADMIN_EMAIL", "admin@institute.edu"), html=True)
        return {"drives": drives, "applied": applied, "selected": selected}


@celery_app.task(name="run.export_csv_task")
def export_csv_task(student_id):
    """Async job: export a student's applications to CSV."""
    with app.app_context():
        student = db.session.get(Student, student_id)
        if not student:
            return {"ok": False, "error": "Student not found"}

        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Student ID", "Company Name", "Drive Title", "Status", "Applied Date"])
        for a in Application.query.filter_by(student_id=student_id).all():
            company_name = a.drive.company.name if a.drive and a.drive.company else ""
            drive_title = a.drive.title if a.drive else ""
            writer.writerow([student_id, company_name, drive_title, a.status,
                             a.applied_at.isoformat() if a.applied_at else ""])

        path = os.path.join(app.instance_path, f"export_{student_id}_{int(time.time())}.csv")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(buf.getvalue())
        return {"ok": True, "path": path}


# ── Admin Task Trigger Routes ─────────────────────────────────────────────────

@app.route("/api/admin/trigger/monthly-report", methods=["POST"])
@login_required
def api_trigger_monthly_report():
    """Admin can manually trigger the monthly report task."""
    role_err = check_role("admin")
    if role_err:
        return role_err
    try:
        result = monthly_report.delay()
        return ok({"queued": True, "task_id": result.id}, "Monthly report task queued via Celery")
    except Exception:
        res = monthly_report()
        return ok({"queued": False, "result": res}, "Monthly report executed (sync fallback)")


@app.route("/api/admin/trigger/reminders", methods=["POST"])
@login_required
def api_trigger_reminders():
    """Admin can manually trigger the daily reminders task."""
    role_err = check_role("admin")
    if role_err:
        return role_err
    try:
        result = send_reminders.delay()
        return ok({"queued": True, "task_id": result.id}, "Reminders task queued via Celery")
    except Exception:
        res = send_reminders()
        return ok({"queued": False, "result": res}, "Reminders executed (sync fallback)")


@app.route("/api/admin/mail-status")
@login_required
def api_mail_status():
    """Check if mail is configured properly."""
    role_err = check_role("admin")
    if role_err:
        return role_err
    server = os.environ.get("MAIL_SERVER", "")
    username = os.environ.get("MAIL_USERNAME", "")
    admin_email = os.environ.get("ADMIN_EMAIL", "")
    configured = bool(server and username)
    return ok({
        "configured": configured,
        "server": server or "(not set)",
        "username": username or "(not set)",
        "admin_email": admin_email or "(not set)",
    })


# ── Celery Trigger Routes ─────────────────────────────────────────────────────

@app.route("/api/student/export", methods=["POST"])
@login_required
def api_student_export():
    role_err = check_role("student")
    if role_err:
        return role_err
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        return err("Student not found", 404)
    try:
        result = export_csv_task.delay(student.id)
        return ok({"task_id": result.id, "queued": True,
                   "download_url": "/api/student/export/download"}, "Export started", 202)
    except Exception:
        res = export_csv_task(student.id)
        return ok({"queued": False, "result": res,
                   "download_url": "/api/student/export/download"}, "Export completed")


@app.route("/api/student/export/download")
@login_required
def api_student_export_download():
    role_err = check_role("student")
    if role_err:
        return role_err
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        return err("Student not found", 404)

    export_dir = app.instance_path
    files = sorted([f for f in os.listdir(export_dir)
                    if f.startswith(f"export_{student.id}_") and f.endswith(".csv")])
    if not files:
        return err("No export found. Please trigger an export first.", 404)

    latest = files[-1]
    return send_from_directory(export_dir, latest, as_attachment=True,
                               download_name="placement_applications.csv")






# ── Startup ────────────────────────────────────────────────────────────────────

with app.app_context():
    db.create_all()

    # Create default admin if none exists
    if not User.query.filter_by(role="admin").first():
        admin = User(email="admin@institute.edu",
                     password=generate_password_hash("admin12345"),
                     role="admin", name="Admin", approved=True)
        db.session.add(admin)
        db.session.commit()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
