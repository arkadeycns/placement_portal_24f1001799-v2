from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timezone

db = SQLAlchemy()


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)       # admin / company / student
    name = db.Column(db.String(120))
    approved = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    company = db.relationship('Company', backref='user', uselist=False)
    student = db.relationship('Student', backref='user', uselist=False)


class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    hr_contact = db.Column(db.String(120))
    website = db.Column(db.String(200))
    industry = db.Column(db.String(120))
    drives = db.relationship('Drive', backref='company')


class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    department = db.Column(db.String(120), nullable=False)
    branch = db.Column(db.String(120))
    cgpa = db.Column(db.Float)
    graduation_year = db.Column(db.Integer)
    phone = db.Column(db.String(20))
    resume = db.Column(db.String(200))
    applications = db.relationship('Application', backref='student')


class Drive(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False)
    eligibility_branch = db.Column(db.String(120))
    eligibility_cgpa = db.Column(db.Float)
    eligibility_year = db.Column(db.Integer)
    location = db.Column(db.String(120))
    ctc = db.Column(db.String(60))
    deadline = db.Column(db.DateTime)
    status = db.Column(db.String(20), default='Pending')  # Pending / Approved / Closed
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    applications = db.relationship('Application', backref='drive')


class Application(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    drive_id = db.Column(db.Integer, db.ForeignKey('drive.id'), nullable=False)
    status = db.Column(db.String(20), default='Applied')  # Applied / Shortlisted / Selected / Rejected
    interview_date = db.Column(db.String(50))
    interview_mode = db.Column(db.String(30))
    applied_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint('student_id', 'drive_id'),)
