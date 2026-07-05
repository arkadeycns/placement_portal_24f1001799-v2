const { createApp } = Vue;

createApp({
  data() {
    return {
      user: null,
      dashboard: {},
      adminPendingUsers: [],
      adminPendingDrives: [],
      adminApplications: [],
      companyApplications: [],
      companyInterviewForms: {},
      companyProfile: { name: '', hr_contact: '', website: '', industry: '' },
      studentProfile: { name: '', department: '', branch: '', phone: '', cgpa: '', graduation_year: '' },
      driveForm: {
        title: '',
        description: '',
        eligibility: '',
        eligibility_branch: '',
        eligibility_cgpa_min: '',
        eligibility_year: '',
        job_location: '',
        ctc: '',
        application_deadline: ''
      },
      message: '',
      error: '',
      loginForm: { email: '', password: '' },
      studentForm: {
        name: '',
        department: '',
        branch: '',
        email: '',
        password: '',
        cgpa: '',
        graduation_year: ''
      },
      companyForm: {
        name: '',
        hr_contact: '',
        website: '',
        industry: '',
        email: '',
        password: ''
      }
    };
  },
  mounted() {
    this.bootstrap();
  },
  methods: {
    async api(path, options = {}) {
      const fetchOptions = { credentials: 'include', ...options };
      if (fetchOptions.body && !(fetchOptions.body instanceof FormData)) {
        fetchOptions.headers = { 'Content-Type': 'application/json', ...(fetchOptions.headers || {}) };
      }
      const response = await fetch(path, fetchOptions);
      return response.json();
    },
    async bootstrap() {
      try {
        const result = await this.api('/api/me');
        if (result.ok) {
          this.user = result.data.user;
          await this.loadRoleData();
        }
      } catch (error) {
        this.error = 'Could not load app state.';
      }
    },
    async login() {
      this.clear();
      const result = await this.api('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify(this.loginForm)
      });
      if (!result.ok) {
        this.error = result.error;
        return;
      }
      this.user = result.data.user;
      this.message = result.message;
      await this.loadRoleData();
    },
    async logout() {
      this.clear();
      await this.api('/api/auth/logout', { method: 'POST' });
      this.user = null;
      this.dashboard = {};
      this.adminPendingUsers = [];
      this.adminPendingDrives = [];
      this.adminApplications = [];
      this.companyApplications = [];
      this.companyInterviewForms = {};
      this.message = 'Logged out.';
    },
    async registerStudent() {
      this.clear();
      const result = await this.api('/api/auth/register/student', {
        method: 'POST',
        body: JSON.stringify(this.studentForm)
      });
      if (!result.ok) {
        this.error = result.error;
        return;
      }
      this.message = result.message;
    },
    async registerCompany() {
      this.clear();
      const result = await this.api('/api/auth/register/company', {
        method: 'POST',
        body: JSON.stringify(this.companyForm)
      });
      if (!result.ok) {
        this.error = result.error;
        return;
      }
      this.message = result.message;
    },
    async loadRoleData() {
      if (!this.user) return;
      const result = await this.api('/api/dashboard');
      if (!result.ok) {
        this.error = result.error;
        return;
      }
      this.dashboard = result.data;

      if (this.user.role === 'admin') {
        await this.loadAdminData();
      }

      if (this.user.role === 'company') {
        await this.loadCompanyData();
      }

      if (this.user.role === 'student') {
        await this.loadStudentData();
      }
    },
    async loadAdminData() {
      const [summary, pendingUsers, pendingDrives, applications] = await Promise.all([
        this.api('/api/admin/summary'),
        this.api('/api/admin/users/pending'),
        this.api('/api/admin/drives'),
        this.api('/api/admin/applications')
      ]);

      if (summary.ok) this.dashboard.stats = summary.data.stats;
      if (pendingUsers.ok) this.adminPendingUsers = pendingUsers.data.users.filter((user) => !user.approved && user.is_active);
      if (pendingDrives.ok) this.adminPendingDrives = pendingDrives.data.drives.filter((drive) => drive.approval_status === 'Pending');
      if (applications.ok) this.adminApplications = applications.data.applications;
    },
    async loadCompanyData() {
      const profile = await this.api('/api/company/me/profile');

      if (profile.ok) this.companyProfile = profile.data.company;
      if (this.dashboard.company) this.companyProfile = this.dashboard.company;

      if (!this.dashboard.drives.length) {
        this.companyApplications = [];
        return;
      }

      const applicationResults = await Promise.all(
        this.dashboard.drives.map((drive) => this.api(`/api/company/drives/${drive.id}/applications`))
      );
      this.companyApplications = applicationResults
        .filter((result) => result.ok && result.data && result.data.applications)
        .flatMap((result) => result.data.applications);
      this.companyApplications.forEach((application) => {
        if (!this.companyInterviewForms[application.id]) {
          this.companyInterviewForms[application.id] = {
            interview_at: '',
            interview_mode: '',
            interview_location: ''
          };
        }
      });
    },
    async loadStudentData() {
      const [profile, applications] = await Promise.all([
        this.api('/api/student/me/profile'),
        this.api('/api/student/me/applications')
      ]);

      if (profile.ok) this.studentProfile = profile.data.student;
      if (applications.ok) this.dashboard.applications = applications.data.applications;
    },
    async applyDrive(driveId) {
      this.clear();
      const result = await this.api(`/api/drives/${driveId}/apply`, { method: 'POST' });
      if (!result.ok) {
        this.error = result.error;
        return;
      }
      this.message = result.message;
      await this.loadRoleData();
    },
    async createDrive() {
      this.clear();
      const result = await this.api('/api/company/drives', {
        method: 'POST',
        body: JSON.stringify(this.driveForm)
      });
      if (!result.ok) {
        this.error = result.error;
        return;
      }
      this.message = result.message;
      await this.loadRoleData();
    },
    async closeDrive(driveId) {
      this.clear();
      const result = await this.api(`/api/company/drives/${driveId}/close`, { method: 'POST' });
      if (!result.ok) {
        this.error = result.error;
        return;
      }
      this.message = result.message;
      await this.loadRoleData();
    },
    async deleteDrive(driveId) {
      this.clear();
      const result = await this.api(`/api/company/drives/${driveId}`, { method: 'DELETE' });
      if (!result.ok) {
        this.error = result.error;
        return;
      }
      this.message = result.message;
      await this.loadRoleData();
    },
    async loadCompanyApplications() {
      await this.loadCompanyData();
    },
    async updateCompanyProfile() {
      this.clear();
      const result = await this.api('/api/company/me/profile', {
        method: 'PUT',
        body: JSON.stringify(this.companyProfile)
      });
      if (!result.ok) {
        this.error = result.error;
        return;
      }
      this.message = 'Company profile updated.';
      await this.loadRoleData();
    },
    async updateApplicationStatus(applicationId, status) {
      this.clear();
      const result = await this.api(`/api/applications/${applicationId}/status`, {
        method: 'POST',
        body: JSON.stringify({ status })
      });
      if (!result.ok) {
        this.error = result.error;
        return;
      }
      this.message = result.message;
      await this.loadRoleData();
    },
    async scheduleInterview(applicationId) {
      this.clear();
      const form = this.companyInterviewForms[applicationId] || {};
      const result = await this.api(`/api/applications/${applicationId}/interview`, {
        method: 'POST',
        body: JSON.stringify(form)
      });
      if (!result.ok) {
        this.error = result.error;
        return;
      }
      this.message = result.message;
      await this.loadRoleData();
    },
    async updateStudentProfile() {
      this.clear();
      const formData = new FormData();
      Object.entries(this.studentProfile).forEach(([key, value]) => formData.append(key, value ?? ''));
      const fileInput = this.$refs.resumeFile;
      if (fileInput && fileInput.files && fileInput.files[0]) {
        formData.append('resume_file', fileInput.files[0]);
      }

      const result = await this.api('/api/student/me/profile', {
        method: 'PUT',
        body: formData
      });
      if (!result.ok) {
        this.error = result.error;
        return;
      }
      this.message = 'Student profile updated.';
      await this.loadRoleData();
    },
    async exportStudentApplications() {
      this.clear();
      const result = await this.api('/api/student/me/export', { method: 'POST' });
      if (!result.ok) {
        this.error = result.error;
        return;
      }
      this.message = `Export started: ${result.data.task_id}`;
    },
    async approveUser(userId) {
      this.clear();
      const result = await this.api(`/api/admin/users/${userId}/approve`, { method: 'POST' });
      if (!result.ok) {
        this.error = result.error;
        return;
      }
      await this.loadRoleData();
    },
    async deactivateUser(userId) {
      this.clear();
      const result = await this.api(`/api/admin/users/${userId}/deactivate`, { method: 'POST' });
      if (!result.ok) {
        this.error = result.error;
        return;
      }
      await this.loadRoleData();
    },
    async approveDrive(driveId) {
      this.clear();
      const result = await this.api(`/api/admin/drives/${driveId}/approve`, { method: 'POST' });
      if (!result.ok) {
        this.error = result.error;
        return;
      }
      await this.loadRoleData();
    },
    async rejectDrive(driveId) {
      this.clear();
      const result = await this.api(`/api/admin/drives/${driveId}/reject`, { method: 'POST' });
      if (!result.ok) {
        this.error = result.error;
        return;
      }
      await this.loadRoleData();
    },
    clear() {
      this.message = '';
      this.error = '';
    }
  }
}).mount('#app');
