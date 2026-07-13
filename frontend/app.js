const { createApp } = Vue;

createApp({
  data() {
    return {
      user: null,
      currentPage: 'dashboard',
      authTab: 'login',
      message: '',
      error: '',

      loginForm: { email: '', password: '' },
      studentRegForm: { name: '', department: '', branch: '', email: '', password: '', cgpa: '', graduation_year: '', phone: '' },
      companyRegForm: { name: '', hr_contact: '', website: '', industry: '', email: '', password: '' },

      // Admin
      adminStats: {},
      allUsers: [],
      userSearch: '',
      userRoleFilter: '',
      adminDrives: [],
      driveStatusFilter: '',
      adminApplications: [],
      appStatusFilter: '',

      // Company
      companyProfile: { name: '', hr_contact: '', website: '', industry: '' },
      driveForm: { title: '', description: '', eligibility_branch: '', eligibility_cgpa: '', eligibility_year: '', location: '', ctc: '', deadline: '' },
      companyDrives: [],
      companyApplications: [],
      interviewForms: {},

      // Student
      studentProfile: { name: '', department: '', branch: '', phone: '', cgpa: '', graduation_year: '' },
      openDrives: [],
      driveSearch: '',
      studentApplications: [],
    };
  },
  mounted() { this.checkLogin(); },
  methods: {

    // ── Helpers ──

    async api(path, opts = {}) {
      const o = { credentials: 'include', ...opts };
      if (o.body && !(o.body instanceof FormData)) {
        o.headers = { 'Content-Type': 'application/json', ...(o.headers || {}) };
      }
      return (await fetch(path, o)).json();
    },
    clear() { this.message = ''; this.error = ''; },
    formatDate(iso) { return iso ? new Date(iso).toLocaleDateString() : '-'; },
    badge(status) {
      const m = { Applied: 'badge bg-primary', Shortlisted: 'badge bg-warning text-dark',
                  Selected: 'badge bg-success', Rejected: 'badge bg-danger',
                  Pending: 'badge bg-secondary', Approved: 'badge bg-success', Closed: 'badge bg-danger' };
      return m[status] || 'badge bg-secondary';
    },
    goTo(page) { this.clear(); this.currentPage = page; this.loadPage(page); },

    async loadPage(page) {
      if (!this.user) return;
      const r = this.user.role;
      if (r === 'admin') {
        if (page === 'dashboard') await this.loadAdminDashboard();
        if (page === 'users') await this.loadAllUsers();
        if (page === 'drives') await this.loadAdminDrives();
        if (page === 'applications') await this.loadAdminApplications();
      } else if (r === 'company') {
        if (page === 'dashboard') await this.loadCompanyDashboard();
        if (page === 'drives') await this.loadCompanyDrives();
        if (page === 'applications') await this.loadCompanyApplications();
      } else if (r === 'student') {
        if (page === 'dashboard') await this.loadStudentDashboard();
        if (page === 'drives') await this.loadOpenDrives();
        if (page === 'applications') await this.loadStudentApplications();
      }
    },

    // ── Auth ──

    async checkLogin() {
      try {
        const res = await this.api('/api/me');
        if (res.ok) { this.user = res.data.user; await this.loadPage('dashboard'); }
      } catch (e) { /* not logged in */ }
    },
    async login() {
      this.clear();
      const res = await this.api('/api/auth/login', { method: 'POST', body: JSON.stringify(this.loginForm) });
      if (!res.ok) { this.error = res.error; return; }
      this.user = res.data.user; this.message = res.message; await this.loadPage('dashboard');
    },
    async logout() {
      await this.api('/api/auth/logout', { method: 'POST' });
      this.user = null; this.message = 'Logged out.';
      this.adminStats = {}; this.allUsers = []; this.adminDrives = []; this.adminApplications = [];
      this.companyDrives = []; this.companyApplications = []; this.openDrives = []; this.studentApplications = [];
    },
    async registerStudent() {
      this.clear();
      const res = await this.api('/api/auth/register/student', { method: 'POST', body: JSON.stringify(this.studentRegForm) });
      if (!res.ok) { this.error = res.error; return; }
      this.message = res.message;
    },
    async registerCompany() {
      this.clear();
      const res = await this.api('/api/auth/register/company', { method: 'POST', body: JSON.stringify(this.companyRegForm) });
      if (!res.ok) { this.error = res.error; return; }
      this.message = res.message;
    },

    // ── Admin ──

    async loadAdminDashboard() {
      const res = await this.api('/api/admin/summary');
      if (res.ok) this.adminStats = res.data.stats;
    },
    async loadAllUsers() {
      let url = '/api/admin/users?';
      if (this.userRoleFilter) url += 'role=' + this.userRoleFilter + '&';
      if (this.userSearch) url += 'q=' + encodeURIComponent(this.userSearch);
      const res = await this.api(url);
      if (res.ok) this.allUsers = res.data.users;
    },
    async approveUser(id) {
      this.clear();
      const res = await this.api('/api/admin/users/' + id + '/approve', { method: 'POST' });
      if (!res.ok) { this.error = res.error; return; }
      this.message = 'User approved.'; await this.loadAllUsers(); await this.loadAdminDashboard();
    },
    async deactivateUser(id) {
      this.clear();
      const res = await this.api('/api/admin/users/' + id + '/deactivate', { method: 'POST' });
      if (!res.ok) { this.error = res.error; return; }
      this.message = 'User deactivated.'; await this.loadAllUsers();
    },
    async reactivateUser(id) {
      this.clear();
      const res = await this.api('/api/admin/users/' + id + '/reactivate', { method: 'POST' });
      if (!res.ok) { this.error = res.error; return; }
      this.message = 'User reactivated.'; await this.loadAllUsers();
    },
    async loadAdminDrives() {
      let url = '/api/admin/drives?';
      if (this.driveStatusFilter) url += 'status=' + this.driveStatusFilter;
      const res = await this.api(url);
      if (res.ok) this.adminDrives = res.data.drives;
    },
    async approveDrive(id) {
      this.clear();
      const res = await this.api('/api/admin/drives/' + id + '/approve', { method: 'POST' });
      if (!res.ok) { this.error = res.error; return; }
      this.message = 'Drive approved.'; await this.loadAdminDrives();
    },
    async rejectDrive(id) {
      this.clear();
      const res = await this.api('/api/admin/drives/' + id + '/reject', { method: 'POST' });
      if (!res.ok) { this.error = res.error; return; }
      this.message = 'Drive rejected.'; await this.loadAdminDrives();
    },
    async loadAdminApplications() {
      let url = '/api/admin/applications?';
      if (this.appStatusFilter) url += 'status=' + this.appStatusFilter;
      const res = await this.api(url);
      if (res.ok) this.adminApplications = res.data.applications;
    },

    // ── Company ──

    async loadCompanyDashboard() {
      const res = await this.api('/api/dashboard');
      if (!res.ok) return;
      if (res.data.company) this.companyProfile = { ...res.data.company };
      this.companyDrives = res.data.drives || [];
    },
    async updateCompanyProfile() {
      this.clear();
      const res = await this.api('/api/company/profile', { method: 'PUT', body: JSON.stringify(this.companyProfile) });
      if (!res.ok) { this.error = res.error; return; }
      this.message = 'Profile updated.';
    },
    async loadCompanyDrives() {
      const res = await this.api('/api/dashboard');
      if (res.ok) this.companyDrives = res.data.drives || [];
    },
    async createDrive() {
      this.clear();
      const res = await this.api('/api/company/drives', { method: 'POST', body: JSON.stringify(this.driveForm) });
      if (!res.ok) { this.error = res.error; return; }
      this.message = res.message;
      this.driveForm = { title: '', description: '', eligibility_branch: '', eligibility_cgpa: '', eligibility_year: '', location: '', ctc: '', deadline: '' };
      await this.loadCompanyDrives();
    },
    async closeDrive(id) {
      this.clear();
      const res = await this.api('/api/company/drives/' + id + '/close', { method: 'POST' });
      if (!res.ok) { this.error = res.error; return; }
      this.message = res.message; await this.loadCompanyDrives();
    },
    async loadCompanyApplications() {
      if (!this.companyDrives.length) {
        const r = await this.api('/api/dashboard');
        if (r.ok) this.companyDrives = r.data.drives || [];
      }
      const results = await Promise.all(
        this.companyDrives.map(d => this.api('/api/company/drives/' + d.id + '/applications'))
      );
      const allApps = results.filter(r => r.ok && r.data && r.data.applications).flatMap(r => r.data.applications);
      const forms = {};
      allApps.forEach(a => { forms[a.id] = this.interviewForms[a.id] || { interview_date: '', interview_mode: '' }; });
      this.interviewForms = forms;
      this.companyApplications = allApps;
    },
    async updateAppStatus(id, status) {
      this.clear();
      const res = await this.api('/api/applications/' + id + '/status', { method: 'POST', body: JSON.stringify({ status }) });
      if (!res.ok) { this.error = res.error; return; }
      this.message = res.message; await this.loadCompanyApplications();
    },
    async scheduleInterview(id) {
      this.clear();
      const form = this.interviewForms[id] || {};
      const res = await this.api('/api/applications/' + id + '/interview', { method: 'POST', body: JSON.stringify(form) });
      if (!res.ok) { this.error = res.error; return; }
      this.message = res.message; await this.loadCompanyApplications();
    },

    // ── Student ──

    async loadStudentDashboard() {
      const res = await this.api('/api/student/profile');
      if (res.ok) this.studentProfile = { ...res.data.student };
    },
    async updateStudentProfile() {
      this.clear();
      const fd = new FormData();
      Object.entries(this.studentProfile).forEach(([k, v]) => fd.append(k, v ?? ''));
      const fi = this.$refs.resumeFile;
      if (fi && fi.files && fi.files[0]) fd.append('resume_file', fi.files[0]);
      const res = await this.api('/api/student/profile', { method: 'PUT', body: fd });
      if (!res.ok) { this.error = res.error; return; }
      this.message = 'Profile updated.'; this.studentProfile = { ...res.data.student };
    },
    async loadOpenDrives() {
      let url = '/api/drives?';
      if (this.driveSearch) url += 'q=' + encodeURIComponent(this.driveSearch);
      const res = await this.api(url);
      if (res.ok) this.openDrives = res.data.drives;
    },
    async applyDrive(id) {
      this.clear();
      const res = await this.api('/api/drives/' + id + '/apply', { method: 'POST' });
      if (!res.ok) { this.error = res.error; return; }
      this.message = res.message; await this.loadOpenDrives();
    },
    async loadStudentApplications() {
      const res = await this.api('/api/student/applications');
      if (res.ok) this.studentApplications = res.data.applications;
    },
    async exportApplications() {
      this.clear();
      const res = await this.api('/api/student/export', { method: 'POST' });
      if (!res.ok) { this.error = res.error; return; }
      this.message = res.data && !res.data.queued ? 'Export completed!' : 'Export started. You will be notified.';
    },
  }
}).mount('#app');
