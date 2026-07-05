from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timezone

db = SQLAlchemy()

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    full_name = db.Column(db.String(120))
    approved = db.Column(db.Boolean, default=False)
    approved_at = db.Column(db.DateTime(timezone=True))
    deactivated_at = db.Column(db.DateTime(timezone=True))
    moderation_reason = db.Column(db.String(255))
    last_login_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True),default=lambda: datetime.now(timezone.utc))
    is_active = db.Column(db.Boolean, default=True)
    company = db.relationship('Company', backref='user', uselist=False)
    student = db.relationship('Student', backref='user', uselist=False)

class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    name = db.Column(db.String(120), nullable=False)
    hr_contact = db.Column(db.String(120), nullable=False)
    website = db.Column(db.String(200))  
    industry = db.Column(db.String(120))
    approval_note = db.Column(db.String(255))
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    drives = db.relationship('Drive', backref='company')
    
class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    name = db.Column(db.String(120), nullable=False)
    department = db.Column(db.String(120), nullable=False)
    branch = db.Column(db.String(120))
    cgpa = db.Column(db.Float)
    graduation_year = db.Column(db.Integer)
    phone = db.Column(db.String(20))
    resume = db.Column(db.String(200))
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    applications = db.relationship('Application', backref='student')

class Drive(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False)
    eligibility = db.Column(db.String(200))
    eligibility_branch = db.Column(db.String(120))
    eligibility_cgpa_min = db.Column(db.Float)
    eligibility_year = db.Column(db.Integer)
    job_location = db.Column(db.String(120))
    ctc = db.Column(db.String(60))
    application_deadline = db.Column(db.DateTime(timezone=True))
    approval_status = db.Column(db.String(20), default="Pending")
    approved_at = db.Column(db.DateTime(timezone=True))
    is_deleted = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(20),default="Pending")
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    applications = db.relationship('Application', backref='drive')

class Application(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    drive_id = db.Column(db.Integer, db.ForeignKey('drive.id'), nullable=False)
    remarks = db.Column(db.String(200))
    status = db.Column(db.String(20), default="Applied")
    applied_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    shortlisted_at = db.Column(db.DateTime(timezone=True))
    interview_at = db.Column(db.DateTime(timezone=True))
    interview_mode = db.Column(db.String(30))
    interview_location = db.Column(db.String(120))
    selected_at = db.Column(db.DateTime(timezone=True))
    rejected_at = db.Column(db.DateTime(timezone=True))
    offer_letter_url = db.Column(db.String(255))
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
            db.UniqueConstraint('student_id', 'drive_id', name='unique_application'),
        )

