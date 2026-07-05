from flask import Flask, request, jsonify, send_from_directory
from flask_caching import Cache
from flask_cors import CORS
from celery import Celery
from dotenv import load_dotenv
import os
import time
import csv
import smtplib
from io import StringIO
from email.message import EmailMessage
from datetime import datetime, timezone
from models import db, User, Company, Student, Drive, Application
from flask_login import LoginManager, login_required, login_user, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import inspect, text, or_, func

load_dotenv()

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get('SECRET_KEY')
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get('SQLALCHEMY_DATABASE_URI')
app.config["UPLOAD_FOLDER"] = os.path.join(app.instance_path, "uploads", "resumes")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["CACHE_TYPE"] = os.environ.get("CACHE_TYPE", "RedisCache")
app.config["CACHE_DEFAULT_TIMEOUT"] = int(os.environ.get("CACHE_DEFAULT_TIMEOUT", "300"))
app.config["CACHE_REDIS_URL"] = os.environ.get("CACHE_REDIS_URL", os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
app.config["FRONTEND_FOLDER"] = os.path.join(os.path.dirname(__file__), "frontend")
app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", "587"))
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME", "")
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD", "")
app.config["MAIL_USE_TLS"] = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
app.config["MAIL_USE_SSL"] = os.environ.get("MAIL_USE_SSL", "false").lower() == "true"
app.config["ADMIN_EMAIL"] = os.environ.get("ADMIN_EMAIL", "admin@institute.edu")

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


db.init_app(app)
cache = Cache(app)
CORS(app, supports_credentials=True)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = None


@login_manager.unauthorized_handler
def unauthorized_api():
    return api_error("Authentication required.", 401)


def compute_admin_stats():
    return {
        "total_students": User.query.filter_by(role="student").count(),
        "total_companies": User.query.filter_by(role="company").count(),
        "total_drives": Drive.query.filter(Drive.is_deleted.is_(False)).count(),
        "total_applications": Application.query.count(),
    }


def cached_admin_stats():
    cache_key = "admin_stats"
    try:
        cached_stats = cache.cache.get(cache_key)
        if cached_stats is None:
            cached_stats = compute_admin_stats()
            cache.cache.set(cache_key, cached_stats, timeout=60)
        return cached_stats
    except Exception:
        return compute_admin_stats()


def invalidate_admin_stats_cache():
    try:
        cache.cache.delete("admin_stats")
    except Exception:
        pass


@app.route("/")
def home():
    return send_from_directory(app.config["FRONTEND_FOLDER"], "index.html")


@app.route("/frontend/<path:filename>")
def frontend_files(filename):
    return send_from_directory(app.config["FRONTEND_FOLDER"], filename)

celery_app = Celery(
    app.import_name,
    broker=os.environ.get("CELERY_BROKER_URL", os.environ.get("REDIS_URL", "redis://localhost:6379/0")),
    backend=os.environ.get("CELERY_RESULT_BACKEND", os.environ.get("REDIS_URL", "redis://localhost:6379/0")),
)
celery_app.conf.update(
    task_track_started=True,
    timezone="UTC",
    beat_schedule={
        "daily-deadline-reminders": {
            "task": "run.send_deadline_reminders",
            "schedule": 60 * 60 * 24,
        },
        "monthly-placement-report": {
            "task": "run.generate_monthly_report",
            "schedule": 60 * 60 * 24 * 30,
        },
    },
)


def get_current_company():
    return Company.query.filter_by(user_id=current_user.id).first()


def get_current_student():
    return Student.query.filter_by(user_id=current_user.id).first()


def allowed_resume_file(filename):
    allowed_ext = {"pdf", "doc", "docx"}
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_ext


def get_request_payload():
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        return payload
    return request.form.to_dict(flat=True)


def api_response(data=None, message=None, status=200, **extra):
    payload = {"ok": True}
    if message is not None:
        payload["message"] = message
    if data is not None:
        payload["data"] = data
    if extra:
        payload.update(extra)
    return jsonify(payload), status


def api_error(message, status=400, **extra):
    payload = {"ok": False, "error": message}
    if extra:
        payload.update(extra)
    return jsonify(payload), status


def api_require_role(*roles):
    if not current_user.is_authenticated:
        return api_error("Authentication required.", 401)

    if not current_user.is_active:
        return api_error("Your account has been deactivated.", 403)

    if current_user.role not in roles:
        return api_error("You are not authorized to access this resource.", 403)

    return None


def serialize_company(company):
    return {
        "id": company.id,
        "user_id": company.user_id,
        "name": company.name,
        "hr_contact": company.hr_contact,
        "website": company.website,
        "industry": company.industry,
        "approval_note": company.approval_note,
        "created_at": company.created_at.isoformat() if company.created_at else None,
    }


def serialize_student(student):
    return {
        "id": student.id,
        "user_id": student.user_id,
        "name": student.name,
        "department": student.department,
        "branch": student.branch,
        "cgpa": student.cgpa,
        "graduation_year": student.graduation_year,
        "phone": student.phone,
        "resume": student.resume,
        "created_at": student.created_at.isoformat() if student.created_at else None,
    }


def serialize_drive(drive):
    return {
        "id": drive.id,
        "company_id": drive.company_id,
        "title": drive.title,
        "description": drive.description,
        "eligibility": drive.eligibility,
        "eligibility_branch": drive.eligibility_branch,
        "eligibility_cgpa_min": drive.eligibility_cgpa_min,
        "eligibility_year": drive.eligibility_year,
        "job_location": drive.job_location,
        "ctc": drive.ctc,
        "application_deadline": drive.application_deadline.isoformat() if drive.application_deadline else None,
        "approval_status": drive.approval_status,
        "approved_at": drive.approved_at.isoformat() if drive.approved_at else None,
        "is_deleted": drive.is_deleted,
        "status": drive.status,
        "created_at": drive.created_at.isoformat() if drive.created_at else None,
    }


def serialize_application(application):
    return {
        "id": application.id,
        "student_id": application.student_id,
        "drive_id": application.drive_id,
        "remarks": application.remarks,
        "status": application.status,
        "applied_at": application.applied_at.isoformat() if application.applied_at else None,
        "shortlisted_at": application.shortlisted_at.isoformat() if application.shortlisted_at else None,
        "interview_at": application.interview_at.isoformat() if application.interview_at else None,
        "interview_mode": application.interview_mode,
        "interview_location": application.interview_location,
        "selected_at": application.selected_at.isoformat() if application.selected_at else None,
        "rejected_at": application.rejected_at.isoformat() if application.rejected_at else None,
        "offer_letter_url": application.offer_letter_url,
        "created_at": application.created_at.isoformat() if application.created_at else None,
    }


def serialize_application_detail(application):
    data = serialize_application(application)
    student = application.student
    drive = application.drive
    if student:
        data["student_name"] = student.name
        data["student_department"] = student.department
        data["student_branch"] = student.branch
        data["student_cgpa"] = student.cgpa
        data["student_resume"] = student.resume
    if drive:
        data["drive_title"] = drive.title
        if drive.company:
            data["company_name"] = drive.company.name
    return data


def serialize_drive_detail(drive):
    data = serialize_drive(drive)
    if drive.company:
        data["company_name"] = drive.company.name
    return data


def validate_student_eligibility(student, drive):
    if drive.eligibility_branch and student.branch and student.branch.lower() != drive.eligibility_branch.lower():
        return False, f"Branch must be {drive.eligibility_branch}."

    if drive.eligibility_cgpa_min is not None and student.cgpa is not None and student.cgpa < drive.eligibility_cgpa_min:
        return False, f"Minimum CGPA required is {drive.eligibility_cgpa_min}."

    if drive.eligibility_year and student.graduation_year and student.graduation_year != drive.eligibility_year:
        return False, f"Year of graduation must be {drive.eligibility_year}."

    return True, None


def send_email(subject, body_text, body_html=None, recipient=None):
    if not app.config["MAIL_SERVER"] or not recipient:
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = app.config["MAIL_USERNAME"] or app.config["ADMIN_EMAIL"]
    message["To"] = recipient
    message.set_content(body_text)
    if body_html:
        message.add_alternative(body_html, subtype="html")

    smtp_class = smtplib.SMTP_SSL if app.config["MAIL_USE_SSL"] else smtplib.SMTP
    with smtp_class(app.config["MAIL_SERVER"], app.config["MAIL_PORT"], timeout=10) as server:
        if app.config["MAIL_USE_TLS"] and not app.config["MAIL_USE_SSL"]:
            server.starttls()
        if app.config["MAIL_USERNAME"]:
            server.login(app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"])
        server.send_message(message)
    return True


def serialize_user(user):
    payload = {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "approved": user.approved,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "approved_at": user.approved_at.isoformat() if user.approved_at else None,
        "deactivated_at": user.deactivated_at.isoformat() if user.deactivated_at else None,
        "moderation_reason": user.moderation_reason,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }

    if user.full_name:
        payload["full_name"] = user.full_name

    if user.company:
        payload["company"] = serialize_company(user.company)

    if user.student:
        payload["student"] = serialize_student(user.student)

    return payload


def apply_student_to_drive(student, drive):
    if not drive or drive.is_deleted:
        return False, "Drive not found."

    if drive.approval_status != "Approved":
        return False, "This drive is not approved for students yet."

    if drive.status != "Open":
        return False, "This drive is not open for applications."

    if drive.application_deadline:
        now_utc = datetime.now(timezone.utc)
        compare_now = now_utc if drive.application_deadline.tzinfo else now_utc.replace(tzinfo=None)
        if drive.application_deadline < compare_now:
            return False, "Application deadline has passed for this drive."

    eligible, eligibility_message = validate_student_eligibility(student, drive)
    if not eligible:
        return False, eligibility_message

    existing_application = Application.query.filter_by(student_id=student.id, drive_id=drive.id).first()
    if existing_application:
        return False, "You have already applied for this drive."

    application = Application(student_id=student.id, drive_id=drive.id, status="Applied")
    db.session.add(application)
    db.session.commit()
    return True, "Application submitted successfully."


@celery_app.task(name="run.send_deadline_reminders")
def send_deadline_reminders():
    with app.app_context():
        now_utc = datetime.now(timezone.utc)
        upcoming_cutoff = now_utc.replace(hour=23, minute=59, second=59, microsecond=0)
        upcoming_drives = (
            Drive.query.filter(
                Drive.approval_status == "Approved",
                Drive.status == "Open",
                Drive.is_deleted.is_(False),
                Drive.application_deadline.isnot(None),
                Drive.application_deadline <= upcoming_cutoff,
            ).all()
        )
        sent = 0
        for student in Student.query.join(User, Student.user_id == User.id).filter(User.is_active.is_(True), User.approved.is_(True)).all():
            upcoming_lines = [f"- {drive.title}: {drive.application_deadline}" for drive in upcoming_drives]
            if not upcoming_lines:
                continue
            body_text = "Upcoming application deadlines:\n" + "\n".join(upcoming_lines)
            if send_email("Placement Portal reminder", body_text, None, student.user.email):
                sent += 1
        return {"reminders_due": len(upcoming_drives), "emails_sent": sent}


@celery_app.task(name="run.generate_monthly_report")
def generate_monthly_report():
    with app.app_context():
        month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        drive_count = Drive.query.filter(Drive.created_at >= month_start).count()
        applied_count = Application.query.filter(Application.created_at >= month_start).count()
        selected_count = Application.query.filter(Application.created_at >= month_start, Application.status == "Selected").count()
        report_html = f"""
        <html>
            <body>
                <h2>Monthly Placement Activity Report</h2>
                <p>Drives conducted: {drive_count}</p>
                <p>Students applied: {applied_count}</p>
                <p>Students selected: {selected_count}</p>
            </body>
        </html>
        """.strip()
        report_text = f"Monthly Placement Activity Report\nDrives conducted: {drive_count}\nStudents applied: {applied_count}\nStudents selected: {selected_count}"
        send_email("Monthly Placement Activity Report", report_text, report_html, app.config["ADMIN_EMAIL"])
        return {"drives": drive_count, "applied": applied_count, "selected": selected_count}


@celery_app.task(name="run.export_applications_csv_task")
def export_applications_csv_task(student_id):
    with app.app_context():
        student = db.session.get(Student, student_id)
        if not student:
            return {"ok": False, "error": "Student not found."}

        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Student ID", "Company Name", "Drive Title", "Application Status", "Dates"])
        applications = (
            Application.query.join(Drive, Application.drive_id == Drive.id)
            .join(Company, Drive.company_id == Company.id)
            .filter(Application.student_id == student.id)
            .order_by(Application.created_at.desc())
            .all()
        )
        for application in applications:
            drive = application.drive
            company = drive.company if drive else None
            writer.writerow([
                student.id,
                company.name if company else "",
                drive.title if drive else "",
                application.status,
                application.created_at.isoformat() if application.created_at else "",
            ])
        export_path = os.path.join(app.instance_path, f"exports_student_{student.id}_{int(time.time())}.csv")
        os.makedirs(os.path.dirname(export_path), exist_ok=True)
        with open(export_path, "w", encoding="utf-8", newline="") as file_handle:
            file_handle.write(buffer.getvalue())
        return {"ok": True, "path": export_path}


def run_task_or_fallback(task, *args, **kwargs):
    try:
        async_result = task.delay(*args, **kwargs)
        return {"task_id": async_result.id, "queued": True}
    except Exception:
        result = task(*args, **kwargs)
        return {"task_id": None, "queued": False, "result": result}


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    payload = get_request_payload()
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))

    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password, password):
        return api_error("Invalid credentials.", 401)

    if not user.is_active:
        return api_error("Your account has been deactivated.", 403)

    if not user.approved:
        return api_error("Your account is pending admin approval.", 403)

    login_user(user)
    user.last_login_at = datetime.now(timezone.utc)
    db.session.commit()
    return api_response({"user": serialize_user(user)}, message="Logged in successfully.")


@app.route("/api/auth/logout", methods=["POST"])
@login_required
def api_logout():
    logout_user()
    return api_response(message="Logged out successfully.")


@app.route("/api/auth/register/student", methods=["POST"])
def api_register_student():
    payload = get_request_payload()
    name = str(payload.get("name", "")).strip()
    department = str(payload.get("department", "")).strip()
    branch = str(payload.get("branch", "")).strip()
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))
    cgpa_raw = payload.get("cgpa")
    graduation_year_raw = payload.get("graduation_year")
    phone = str(payload.get("phone", "")).strip()

    if not all([name, department, email, password]):
        return api_error("Name, department, email and password are required.", 400)

    if len(password) < 8:
        return api_error("Password must be at least 8 characters long.", 400)

    if User.query.filter_by(email=email).first():
        return api_error("Email already registered.", 409)

    cgpa = None
    graduation_year = None
    if cgpa_raw not in (None, ""):
        try:
            cgpa = float(cgpa_raw)
        except ValueError:
            return api_error("CGPA must be a number.", 400)
    if graduation_year_raw not in (None, ""):
        try:
            graduation_year = int(graduation_year_raw)
        except ValueError:
            return api_error("Graduation year must be a number.", 400)

    user = User(email=email, password=generate_password_hash(password), role="student", full_name=name)
    student = Student(
        user=user,
        name=name,
        department=department,
        branch=branch or None,
        cgpa=cgpa,
        graduation_year=graduation_year,
        phone=phone or None,
    )

    db.session.add(user)
    db.session.add(student)
    db.session.commit()
    return api_response({"user": serialize_user(user)}, message="Student registered successfully.", status=201)


@app.route("/api/auth/register/company", methods=["POST"])
def api_register_company():
    payload = get_request_payload()
    name = str(payload.get("name", "")).strip()
    hr_contact = str(payload.get("hr_contact", "")).strip()
    website = str(payload.get("website", "")).strip()
    industry = str(payload.get("industry", "")).strip()
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))

    if not all([name, hr_contact, email, password]):
        return api_error("Company name, HR contact, email and password are required.", 400)

    if len(password) < 8:
        return api_error("Password must be at least 8 characters long.", 400)

    if User.query.filter_by(email=email).first():
        return api_error("Email already registered.", 409)

    user = User(email=email, password=generate_password_hash(password), role="company", full_name=name)
    company = Company(
        user=user,
        name=name,
        hr_contact=hr_contact,
        website=website or None,
        industry=industry or None,
    )

    db.session.add(user)
    db.session.add(company)
    db.session.commit()
    return api_response({"user": serialize_user(user)}, message="Company registered successfully. Awaiting admin approval.", status=201)


@app.route("/api/admin/summary")
@login_required
def api_admin_summary():
    unauthorized = api_require_role("admin")
    if unauthorized:
        return unauthorized

    return api_response({"stats": cached_admin_stats()})


@app.route("/api/admin/users/pending")
@login_required
def api_pending_users():
    unauthorized = api_require_role("admin")
    if unauthorized:
        return unauthorized

    pending_users = User.query.filter_by(approved=False, is_active=True).order_by(User.created_at.asc()).all()
    return api_response({"users": [serialize_user(user) for user in pending_users]})


@app.route("/api/admin/users/all")
@login_required
def api_admin_all_users():
    unauthorized = api_require_role("admin")
    if unauthorized:
        return unauthorized

    role_filter = request.args.get("role", "").strip()
    search = request.args.get("q", "").strip()
    query = User.query.filter(User.role != "admin")

    if role_filter in {"student", "company"}:
        query = query.filter(User.role == role_filter)
    if search:
        query = query.filter(or_(User.email.ilike(f"%{search}%"), User.full_name.ilike(f"%{search}%")))

    users = query.order_by(User.created_at.desc()).all()
    return api_response({"users": [serialize_user(user) for user in users]})


@app.route("/api/admin/users/<int:user_id>/approve", methods=["POST"])
@login_required
def api_approve_user(user_id):
    unauthorized = api_require_role("admin")
    if unauthorized:
        return unauthorized

    user = db.session.get(User, user_id)
    if not user:
        return api_error("User not found.", 404)

    payload = get_request_payload()
    user.approved = True
    user.approved_at = datetime.now(timezone.utc)
    user.moderation_reason = str(payload.get("reason", "")).strip() or None
    db.session.commit()
    invalidate_admin_stats_cache()
    return api_response({"user": serialize_user(user)}, message="User approved.")


@app.route("/api/admin/users/<int:user_id>/deactivate", methods=["POST"])
@login_required
def api_deactivate_user(user_id):
    unauthorized = api_require_role("admin")
    if unauthorized:
        return unauthorized

    user = db.session.get(User, user_id)
    if not user:
        return api_error("User not found.", 404)

    if user.role == "admin":
        return api_error("Admin cannot be deactivated here.", 400)

    payload = get_request_payload()
    reason = str(payload.get("reason", "")).strip() or "Deactivated by admin"
    user.approved = False
    user.is_active = False
    user.deactivated_at = datetime.now(timezone.utc)
    user.moderation_reason = reason
    db.session.commit()
    invalidate_admin_stats_cache()
    return api_response({"user": serialize_user(user)}, message="User deactivated.")


@app.route("/api/admin/users/<int:user_id>/reactivate", methods=["POST"])
@login_required
def api_reactivate_user(user_id):
    unauthorized = api_require_role("admin")
    if unauthorized:
        return unauthorized

    user = db.session.get(User, user_id)
    if not user:
        return api_error("User not found.", 404)

    user.is_active = True
    user.approved = True
    user.deactivated_at = None
    db.session.commit()
    invalidate_admin_stats_cache()
    return api_response({"user": serialize_user(user)}, message="User reactivated.")


@app.route("/api/admin/drives")
@login_required
def api_admin_drives():
    unauthorized = api_require_role("admin")
    if unauthorized:
        return unauthorized

    status_filter = request.args.get("status", "All").strip()
    approval_filter = request.args.get("approval", "All").strip()
    query = Drive.query.filter(Drive.is_deleted.is_(False))
    if status_filter in {"Pending", "Open", "Closed"}:
        query = query.filter(Drive.status == status_filter)
    if approval_filter in {"Pending", "Approved", "Rejected"}:
        query = query.filter(Drive.approval_status == approval_filter)
    drives = query.order_by(Drive.created_at.desc()).all()
    return api_response({"drives": [serialize_drive_detail(drive) for drive in drives]})


@app.route("/api/admin/applications")
@login_required
def api_admin_applications():
    unauthorized = api_require_role("admin")
    if unauthorized:
        return unauthorized

    query = Application.query.join(Drive, Application.drive_id == Drive.id).join(Student, Application.student_id == Student.id).join(Company, Drive.company_id == Company.id)
    status_filter = request.args.get("status", "").strip()
    search = request.args.get("q", "").strip()
    if status_filter in {"Applied", "Shortlisted", "Rejected", "Selected"}:
        query = query.filter(Application.status == status_filter)
    if search:
        query = query.filter(or_(Student.name.ilike(f"%{search}%"), Company.name.ilike(f"%{search}%"), Drive.title.ilike(f"%{search}%")))

    applications = query.order_by(Application.created_at.desc()).all()
    return api_response({"applications": [serialize_application_detail(application) for application in applications]})


@app.route("/api/admin/reports/monthly")
@login_required
def api_admin_monthly_report():
    unauthorized = api_require_role("admin")
    if unauthorized:
        return unauthorized

    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    drive_count = Drive.query.filter(Drive.created_at >= month_start).count()
    applied_count = Application.query.filter(Application.created_at >= month_start).count()
    selected_count = Application.query.filter(Application.created_at >= month_start, Application.status == "Selected").count()
    return api_response({"drives": drive_count, "applied": applied_count, "selected": selected_count})


@app.route("/api/admin/reports/monthly/send", methods=["POST"])
@login_required
def api_admin_send_monthly_report():
    unauthorized = api_require_role("admin")
    if unauthorized:
        return unauthorized

    payload = run_task_or_fallback(generate_monthly_report)
    return api_response(payload, message="Monthly report queued.", status=202)


@app.route("/api/admin/reminders/send", methods=["POST"])
@login_required
def api_admin_send_reminders():
    unauthorized = api_require_role("admin")
    if unauthorized:
        return unauthorized

    payload = run_task_or_fallback(send_deadline_reminders)
    return api_response(payload, message="Reminder job queued.", status=202)


@app.route("/api/admin/drives/<int:drive_id>/approve", methods=["POST"])
@login_required
def api_admin_approve_drive(drive_id):
    unauthorized = api_require_role("admin")
    if unauthorized:
        return unauthorized

    drive = db.session.get(Drive, drive_id)
    if not drive or drive.is_deleted:
        return api_error("Drive not found.", 404)

    drive.approval_status = "Approved"
    if drive.status == "Pending":
        drive.status = "Open"
    drive.approved_at = datetime.now(timezone.utc)
    db.session.commit()
    invalidate_admin_stats_cache()
    return api_response({"drive": serialize_drive(drive)}, message="Drive approved.")


@app.route("/api/admin/drives/<int:drive_id>/reject", methods=["POST"])
@login_required
def api_admin_reject_drive(drive_id):
    unauthorized = api_require_role("admin")
    if unauthorized:
        return unauthorized

    drive = db.session.get(Drive, drive_id)
    if not drive or drive.is_deleted:
        return api_error("Drive not found.", 404)

    drive.approval_status = "Rejected"
    drive.status = "Closed"
    db.session.commit()
    invalidate_admin_stats_cache()
    return api_response({"drive": serialize_drive(drive)}, message="Drive rejected.")


@app.route("/api/company/drives", methods=["POST"])
@login_required
def api_create_drive():
    unauthorized = api_require_role("company")
    if unauthorized:
        return unauthorized

    company = get_current_company()
    if not company:
        return api_error("Company profile not found.", 404)

    if not current_user.approved:
        return api_error("Your company is not approved yet.", 403)

    payload = get_request_payload()
    title = str(payload.get("title", "")).strip()
    description = str(payload.get("description", "")).strip()
    eligibility = str(payload.get("eligibility", "")).strip()
    eligibility_branch = str(payload.get("eligibility_branch", "")).strip()
    eligibility_cgpa_min = payload.get("eligibility_cgpa_min")
    eligibility_year = payload.get("eligibility_year")
    job_location = str(payload.get("job_location", "")).strip()
    ctc = str(payload.get("ctc", "")).strip()
    deadline_raw = str(payload.get("application_deadline", "")).strip()

    if not all([title, description]):
        return api_error("Title and description are required.", 400)

    deadline = None
    if deadline_raw:
        try:
            deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00"))
        except ValueError:
            return api_error("Deadline format is invalid.", 400)

    cgpa_value = None
    year_value = None
    if eligibility_cgpa_min not in (None, ""):
        try:
            cgpa_value = float(eligibility_cgpa_min)
        except ValueError:
            return api_error("Eligibility CGPA must be numeric.", 400)
    if eligibility_year not in (None, ""):
        try:
            year_value = int(eligibility_year)
        except ValueError:
            return api_error("Eligibility year must be numeric.", 400)

    drive = Drive(
        company_id=company.id,
        title=title,
        description=description,
        eligibility=eligibility or None,
        eligibility_branch=eligibility_branch or None,
        eligibility_cgpa_min=cgpa_value,
        eligibility_year=year_value,
        job_location=job_location or None,
        ctc=ctc or None,
        application_deadline=deadline,
        status="Pending",
        approval_status="Pending",
    )
    db.session.add(drive)
    db.session.commit()
    invalidate_admin_stats_cache()
    return api_response({"drive": serialize_drive(drive)}, message="Drive created and sent for admin approval.", status=201)


@app.route("/api/company/drives/<int:drive_id>/close", methods=["POST"])
@login_required
def api_company_close_drive(drive_id):
    unauthorized = api_require_role("company")
    if unauthorized:
        return unauthorized

    company = get_current_company()
    drive = db.session.get(Drive, drive_id)
    if not company or not drive or drive.company_id != company.id or drive.is_deleted:
        return api_error("Drive not found.", 404)

    drive.status = "Closed"
    db.session.commit()
    invalidate_admin_stats_cache()
    return api_response({"drive": serialize_drive(drive)}, message="Drive closed successfully.")


@app.route("/api/company/drives/<int:drive_id>", methods=["PUT", "DELETE"])
@login_required
def api_company_manage_drive(drive_id):
    unauthorized = api_require_role("company")
    if unauthorized:
        return unauthorized

    company = get_current_company()
    drive = db.session.get(Drive, drive_id)
    if not company or not drive or drive.company_id != company.id or drive.is_deleted:
        return api_error("Drive not found.", 404)

    if request.method == "DELETE":
        protected_count = (
            Application.query.filter(
                Application.drive_id == drive.id,
                Application.status.in_(["Shortlisted", "Selected"]),
            ).count()
        )
        if protected_count > 0:
            return api_error("Cannot remove drive with shortlisted/selected applications.", 400)
        drive.is_deleted = True
        drive.status = "Closed"
        db.session.commit()
        invalidate_admin_stats_cache()
        return api_response(message="Drive removed successfully.")

    payload = get_request_payload()
    drive.title = str(payload.get("title", "")).strip() or drive.title
    drive.description = str(payload.get("description", "")).strip() or drive.description
    drive.eligibility = str(payload.get("eligibility", "")).strip() or drive.eligibility
    drive.eligibility_branch = str(payload.get("eligibility_branch", "")).strip() or drive.eligibility_branch
    drive.job_location = str(payload.get("job_location", "")).strip() or drive.job_location
    drive.ctc = str(payload.get("ctc", "")).strip() or drive.ctc
    db.session.commit()
    return api_response({"drive": serialize_drive(drive)}, message="Drive updated successfully.")


@app.route("/api/company/drives/<int:drive_id>/applications")
@login_required
def api_company_drive_applications(drive_id):
    unauthorized = api_require_role("company")
    if unauthorized:
        return unauthorized

    company = get_current_company()
    drive = db.session.get(Drive, drive_id)
    if not company or not drive or drive.company_id != company.id:
        return api_error("Drive not found.", 404)

    applications = (
        Application.query.join(Student, Application.student_id == Student.id)
        .filter(Application.drive_id == drive.id)
        .order_by(Application.created_at.desc())
        .all()
    )
    return api_response({"drive": serialize_drive(drive), "applications": [serialize_application_detail(application) for application in applications]})


@app.route("/api/applications/<int:application_id>/status", methods=["POST"])
@login_required
def api_update_application_status(application_id):
    unauthorized = api_require_role("company", "admin")
    if unauthorized:
        return unauthorized

    application = db.session.get(Application, application_id)
    if not application:
        return api_error("Application not found.", 404)

    drive = db.session.get(Drive, application.drive_id)
    if current_user.role == "company":
        company = get_current_company()
        if not company or not drive or drive.company_id != company.id:
            return api_error("You are not authorized to update this application.", 403)

    payload = get_request_payload()
    new_status = str(payload.get("status", "")).strip()
    remarks = str(payload.get("remarks", "")).strip()
    allowed_statuses = {"Applied", "Shortlisted", "Rejected", "Selected"}
    if new_status not in allowed_statuses:
        return api_error("Invalid application status.", 400)

    application.status = new_status
    application.remarks = remarks or None
    now_utc = datetime.now(timezone.utc)
    if new_status == "Shortlisted":
        application.shortlisted_at = now_utc
    elif new_status == "Selected":
        application.selected_at = now_utc
    elif new_status == "Rejected":
        application.rejected_at = now_utc
    db.session.commit()
    invalidate_admin_stats_cache()
    return api_response({"application": serialize_application(application)}, message="Application status updated.")


@app.route("/api/applications/<int:application_id>/interview", methods=["POST"])
@login_required
def api_schedule_interview(application_id):
    unauthorized = api_require_role("company", "admin")
    if unauthorized:
        return unauthorized

    application = db.session.get(Application, application_id)
    if not application:
        return api_error("Application not found.", 404)

    drive = db.session.get(Drive, application.drive_id)
    if current_user.role == "company":
        company = get_current_company()
        if not company or not drive or drive.company_id != company.id:
            return api_error("You are not authorized to update this application.", 403)

    payload = get_request_payload()
    interview_raw = str(payload.get("interview_at", "")).strip()
    interview_mode = str(payload.get("interview_mode", "")).strip()
    interview_location = str(payload.get("interview_location", "")).strip()

    if not interview_raw:
        return api_error("Interview date and time is required.", 400)

    try:
        interview_at = datetime.fromisoformat(interview_raw.replace("Z", "+00:00"))
    except ValueError:
        return api_error("Interview date format is invalid.", 400)

    application.interview_at = interview_at
    application.interview_mode = interview_mode or None
    application.interview_location = interview_location or None
    application.status = "Shortlisted"
    application.shortlisted_at = application.shortlisted_at or datetime.now(timezone.utc)
    db.session.commit()
    invalidate_admin_stats_cache()
    return api_response({"application": serialize_application(application)}, message="Interview scheduled.")


@app.route("/api/student/me/profile", methods=["GET", "PUT"])
@login_required
def api_student_me_profile():
    unauthorized = api_require_role("student")
    if unauthorized:
        return unauthorized

    student = get_current_student()
    if not student:
        return api_error("Student profile not found.", 404)

    if request.method == "PUT":
        payload = get_request_payload()
        student.name = str(payload.get("name", "")).strip() or student.name
        student.department = str(payload.get("department", "")).strip() or student.department
        student.branch = str(payload.get("branch", "")).strip() or student.branch
        student.phone = str(payload.get("phone", "")).strip() or student.phone

        resume_file = request.files.get("resume_file")
        if resume_file and resume_file.filename:
            if not allowed_resume_file(resume_file.filename):
                return api_error("Invalid resume file type. Use PDF, DOC or DOCX.", 400)
            saved_path = save_resume_file(resume_file, student.id)
            if saved_path:
                student.resume = saved_path

        cgpa_raw = payload.get("cgpa")
        graduation_year_raw = payload.get("graduation_year")
        if cgpa_raw not in (None, ""):
            try:
                student.cgpa = float(cgpa_raw)
            except ValueError:
                return api_error("CGPA must be numeric.", 400)
        if graduation_year_raw not in (None, ""):
            try:
                student.graduation_year = int(graduation_year_raw)
            except ValueError:
                return api_error("Graduation year must be numeric.", 400)

        db.session.commit()

    return api_response({"student": serialize_student(student)})


@app.route("/api/company/me/profile", methods=["GET", "PUT"])
@login_required
def api_company_me_profile():
    unauthorized = api_require_role("company")
    if unauthorized:
        return unauthorized

    company = get_current_company()
    if not company:
        return api_error("Company profile not found.", 404)

    if request.method == "PUT":
        payload = get_request_payload()
        company.name = str(payload.get("name", "")).strip() or company.name
        company.hr_contact = str(payload.get("hr_contact", "")).strip() or company.hr_contact
        company.website = str(payload.get("website", "")).strip() or None
        company.industry = str(payload.get("industry", "")).strip() or None
        db.session.commit()

    return api_response({"company": serialize_company(company)})


@app.route("/api/health")
def api_health():
    return api_response({"status": "healthy"})


@app.route("/api/me")
@login_required
def api_me():
    return api_response({"user": serialize_user(current_user)})


@app.route("/api/dashboard")
@login_required
def api_dashboard():
    if current_user.role == "admin":
        return api_response({"role": "admin", "stats": cached_admin_stats()})

    if current_user.role == "company":
        company = get_current_company()
        if not company:
            return api_error("Company profile not found.", 404)

        drives = Drive.query.filter_by(company_id=company.id).filter(Drive.is_deleted.is_(False)).order_by(Drive.created_at.desc()).all()
        drive_data = []
        for drive in drives:
            d = serialize_drive(drive)
            d["applications_count"] = Application.query.filter_by(drive_id=drive.id).count()
            drive_data.append(d)
        applications_received = (
            Application.query.join(Drive, Application.drive_id == Drive.id)
            .filter(Drive.company_id == company.id)
            .count()
        )
        return api_response({
            "role": "company",
            "company": serialize_company(company),
            "applications_received": applications_received,
            "drives": drive_data,
        })

    student = get_current_student()
    if not student:
        return api_error("Student profile not found.", 404)

    open_drives = (
        Drive.query.filter(
            Drive.status == "Open",
            Drive.approval_status == "Approved",
            Drive.is_deleted.is_(False),
        )
        .order_by(Drive.created_at.desc())
        .all()
    )
    applications = (
        Application.query.join(Drive, Application.drive_id == Drive.id)
        .join(Company, Drive.company_id == Company.id)
        .filter(Application.student_id == student.id)
        .order_by(Application.created_at.desc())
        .all()
    )
    return api_response({
        "role": "student",
        "student": serialize_student(student),
        "open_drives": [serialize_drive_detail(drive) for drive in open_drives],
        "applications": [serialize_application_detail(application) for application in applications],
    })


@app.route("/api/drives")
@login_required
def api_drives():
    unauthorized = api_require_role("admin", "company", "student")
    if unauthorized:
        return unauthorized

    query = Drive.query.filter(Drive.is_deleted.is_(False))
    if current_user.role == "company":
        company = get_current_company()
        if not company:
            return api_error("Company profile not found.", 404)
        query = query.filter(Drive.company_id == company.id)
    elif current_user.role == "student":
        query = query.filter(Drive.approval_status == "Approved", Drive.status == "Open")

    status_filter = request.args.get("status", "").strip()
    search = request.args.get("q", "").strip()
    if status_filter in {"Pending", "Open", "Closed"}:
        query = query.filter(Drive.status == status_filter)
    if search:
        query = query.filter(or_(Drive.title.ilike(f"%{search}%"), Drive.description.ilike(f"%{search}%"), Drive.eligibility.ilike(f"%{search}%")))

    drives = query.order_by(Drive.created_at.desc()).all()
    return api_response({"drives": [serialize_drive_detail(drive) for drive in drives]})


@app.route("/api/drives/<int:drive_id>")
@login_required
def api_drive_detail(drive_id):
    drive = db.session.get(Drive, drive_id)
    if not drive or drive.is_deleted:
        return api_error("Drive not found.", 404)

    if current_user.role == "company":
        company = get_current_company()
        if not company or drive.company_id != company.id:
            return api_error("You are not authorized to view this drive.", 403)

    company = db.session.get(Company, drive.company_id)
    payload = serialize_drive(drive)
    payload["company"] = serialize_company(company) if company else None
    return api_response({"drive": payload})


@app.route("/api/drives/<int:drive_id>/apply", methods=["POST"])
@login_required
def api_apply_drive(drive_id):
    unauthorized = api_require_role("student")
    if unauthorized:
        return unauthorized

    student = get_current_student()
    if not student:
        return api_error("Student profile not found.", 404)

    drive = db.session.get(Drive, drive_id)
    success, message = apply_student_to_drive(student, drive)
    if not success:
        return api_error(message, 400)

    return api_response(message=message, status=201)


@app.route("/api/student/me/applications")
@login_required
def api_student_applications():
    unauthorized = api_require_role("student")
    if unauthorized:
        return unauthorized

    student = get_current_student()
    if not student:
        return api_error("Student profile not found.", 404)

    applications = (
        Application.query.join(Drive, Application.drive_id == Drive.id)
        .join(Company, Drive.company_id == Company.id)
        .filter(Application.student_id == student.id)
        .order_by(Application.created_at.desc())
        .all()
    )
    return api_response({"applications": [serialize_application_detail(application) for application in applications]})


@app.route("/api/student/me/export", methods=["POST"])
@login_required
def api_student_export_applications():
    unauthorized = api_require_role("student")
    if unauthorized:
        return unauthorized

    student = get_current_student()
    if not student:
        return api_error("Student profile not found.", 404)

    payload = run_task_or_fallback(export_applications_csv_task, student.id)
    return api_response(payload, message="Export started.", status=202)


@app.route("/api/search")
@login_required
def api_search():
    unauthorized = api_require_role("admin", "student", "company")
    if unauthorized:
        return unauthorized

    entity = request.args.get("entity", "drives").strip().lower()
    query_text = request.args.get("q", "").strip()
    results = []

    if entity == "students" and current_user.role == "admin":
        query = Student.query.join(User, Student.user_id == User.id)
        if query_text:
            query = query.filter(or_(Student.name.ilike(f"%{query_text}%"), Student.department.ilike(f"%{query_text}%"), User.email.ilike(f"%{query_text}%")))
        results = [serialize_student(student) for student in query.order_by(Student.created_at.desc()).all()]
    elif entity == "companies":
        query = Company.query.join(User, Company.user_id == User.id)
        if query_text:
            query = query.filter(or_(Company.name.ilike(f"%{query_text}%"), Company.hr_contact.ilike(f"%{query_text}%"), User.email.ilike(f"%{query_text}%")))
        results = [serialize_company(company) for company in query.order_by(Company.created_at.desc()).all()]
    else:
        query = Drive.query.filter(Drive.is_deleted.is_(False))
        if current_user.role == "student":
            query = query.filter(Drive.approval_status == "Approved", Drive.status == "Open")
        elif current_user.role == "company":
            company = get_current_company()
            if company:
                query = query.filter(Drive.company_id == company.id)
        if query_text:
            query = query.filter(or_(Drive.title.ilike(f"%{query_text}%"), Drive.description.ilike(f"%{query_text}%"), Drive.eligibility.ilike(f"%{query_text}%")))
        results = [serialize_drive(drive) for drive in query.order_by(Drive.created_at.desc()).all()]

    return api_response({"entity": entity, "results": results})


def save_resume_file(upload_file, student_id):
    original_name = secure_filename(upload_file.filename)
    if not original_name:
        return None

    filename = f"student_{student_id}_{int(time.time())}_{original_name}"
    full_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    upload_file.save(full_path)
    return f"uploads/resumes/{filename}"

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

with app.app_context():
    db.create_all()

    # Cleanup legacy schema: blacklisted is replaced by approved-only moderation.
    inspector = inspect(db.engine)
    user_columns = {col["name"] for col in inspector.get_columns("user")}
    if "blacklisted" in user_columns:
        try:
            db.session.execute(text("ALTER TABLE user DROP COLUMN blacklisted"))
            db.session.commit()
        except Exception:
            db.session.rollback()


    admin_exists = User.query.filter_by(role="admin").first()
    if not admin_exists:
        hashed_password = generate_password_hash('admin12345')
        admin_user = User(
            email='admin@institute.edu',
            password=hashed_password,
            approved=True,
            role='admin'
        )

        db.session.add(admin_user)
        db.session.commit() 

if __name__ == '__main__':
    app.run(debug=True, port=5000)
