const AuthPage = {
    formatPermissionLabel(permissionKey) {
        if (!permissionKey) return '';
        const parts = String(permissionKey).split('.');
        const action = parts.pop();
        const subject = parts
            .join(' ')
            .replaceAll('_', ' ')
            .split(' ')
            .filter(Boolean)
            .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
            .join(' ');
        const actionLabel = action
            ? action.charAt(0).toUpperCase() + action.slice(1).replaceAll('_', ' ')
            : '';
        return [actionLabel, subject].filter(Boolean).join(' ');
    },

    renderPermissionSummary(permissionKeys) {
        return (permissionKeys || []).map((permission) => escapeHtml(AuthPage.formatPermissionLabel(permission))).join(', ');
    },

    async render() {
        const state = App.authState && Object.prototype.hasOwnProperty.call(App.authState, 'authenticated')
            ? App.authState
            : await API.get('/auth/me');
        App.setAuthState(state);
        if (typeof App.syncAuthUI === 'function') App.syncAuthUI();
        if (typeof App.syncNavVisibility === 'function') App.syncNavVisibility();
        if (state.authenticated) {
            return `
                <div class="page-header">
                    <h2>Signed In</h2>
                </div>
                <div class="empty-state">
                    <p>You are already signed in as <strong>${escapeHtml(state.user.full_name)}</strong>.</p>
                    <p style="margin-top:8px;"><button class="btn btn-primary" onclick="App.navigate('#/')">Go to Dashboard</button></p>
                </div>`;
        }
        if (state.bootstrap_required) {
            return AuthPage.renderBootstrapForm();
        }
        return AuthPage.renderLoginForm();
    },

    renderLoginForm() {
        return `
            <div class="page-header">
                <h2>Sign In</h2>
            </div>
            <form onsubmit="AuthPage.login(event)">
                <div class="settings-section" style="max-width:420px; margin:0 auto;">
                    <div class="form-grid">
                        <div class="form-group full-width"><label>Email</label><input name="email" type="email" required></div>
                        <div class="form-group full-width"><label>Password</label><input name="password" type="password" required></div>
                    </div>
                    <div class="form-actions">
                        <button type="submit" class="btn btn-primary">Sign In</button>
                    </div>
                </div>
            </form>`;
    },

    renderBootstrapForm() {
        return `
            <div class="page-header">
                <h2>Create First Admin</h2>
            </div>
            <div class="settings-section" style="max-width:480px; margin:0 auto;">
                <div style="font-size:11px; color:var(--text-muted); margin-bottom:12px;">
                    No users exist yet. Create the first admin to activate protected payroll and admin features.
                </div>
                <form onsubmit="AuthPage.bootstrapAdmin(event)">
                    <div class="form-grid">
                        <div class="form-group full-width"><label>Full Name</label><input name="full_name" required></div>
                        <div class="form-group full-width"><label>Email</label><input name="email" type="email" required></div>
                        <div class="form-group full-width"><label>Password</label><input name="password" type="password" minlength="8" required></div>
                    </div>
                    <div class="form-actions">
                        <button type="submit" class="btn btn-primary">Create First Admin</button>
                    </div>
                </form>
            </div>`;
    },

    async login(e) {
        e.preventDefault();
        const form = new FormData(e.target);
        try {
            const response = await API.post('/auth/login', {
                email: form.get('email'),
                password: form.get('password'),
            });
            localStorage.setItem('slowbooks-auth-token', response.token);
            App.setAuthState({ authenticated: true, bootstrap_required: false, user: response.user });
            await App.loadSettings();
            if (typeof App.syncAuthUI === 'function') App.syncAuthUI();
            if (typeof App.syncNavVisibility === 'function') App.syncNavVisibility();
            App.navigate('#/');
        } catch (err) {
            toast(err.message, 'error');
        }
    },

    async bootstrapAdmin(e) {
        e.preventDefault();
        const form = new FormData(e.target);
        try {
            const response = await API.post('/auth/bootstrap-admin', {
                full_name: form.get('full_name'),
                email: form.get('email'),
                password: form.get('password'),
            });
            localStorage.setItem('slowbooks-auth-token', response.token);
            App.setAuthState({ authenticated: true, bootstrap_required: false, user: response.user });
            await App.loadSettings();
            if (typeof App.syncAuthUI === 'function') App.syncAuthUI();
            if (typeof App.syncNavVisibility === 'function') App.syncNavVisibility();
            App.navigate('#/');
        } catch (err) {
            toast(err.message, 'error');
        }
    },

    async logout() {
        try {
            await API.post('/auth/logout');
        } catch (_err) {
            // Ignore logout transport failures; local session should still clear.
        }
        localStorage.removeItem('slowbooks-auth-token');
        App.setAuthState({ authenticated: false, bootstrap_required: false, user: null });
        App.settings = {};
        if (typeof App.syncAuthUI === 'function') App.syncAuthUI();
        if (typeof App.syncNavVisibility === 'function') App.syncNavVisibility();
        App.navigate('#/login');
    },

    async renderUserManagement() {
        const [users, meta] = await Promise.all([
            API.get('/auth/users'),
            API.get('/auth/meta'),
        ]);
        AuthPage._usersCache = users;
        AuthPage._metaCache = meta;
        return `
            <div class="page-header">
                <h2>Users & Access</h2>
                <button class="btn btn-primary" onclick="AuthPage.showUserForm()">New User</button>
            </div>
            <div class="settings-section">
                <h3>Role Templates</h3>
                <div style="font-size:10px; color:var(--text-muted); margin-bottom:10px;">
                    Start from a role template, then use allow/deny overrides when responsibilities need finer separation than a coarse fixed role.
                </div>
                <div class="card-grid">
                    ${meta.roles.map(role => `
                        <div class="card">
                            <div class="card-header">${escapeHtml(role.label)}</div>
                            <div style="font-size:11px; margin-bottom:6px;">${escapeHtml(role.description)}</div>
                            <div style="font-size:10px; color:var(--text-muted);">${AuthPage.renderPermissionSummary(role.permissions) || 'No permissions by default'}</div>
                        </div>`).join('')}
                </div>
            </div>
            <div class="settings-section">
                <h3>Current Users</h3>
                <div class="table-container"><table>
                    <thead><tr><th>User</th><th>Role</th><th>Overrides</th><th>Status</th><th>Actions</th></tr></thead>
                    <tbody>
                        ${users.map((user, index) => `
                            <tr>
                                <td>
                                    <strong>${escapeHtml(user.full_name)}</strong><br>
                                    <span style="font-size:10px; color:var(--text-muted);">${escapeHtml(user.email)}</span>
                                </td>
                                <td>${escapeHtml(user.membership.role_key)}</td>
                                <td style="font-size:10px; color:var(--text-muted);">
                                    ${AuthPage.renderPermissionSummary(user.membership.allow_permissions.concat(user.membership.deny_permissions)) || 'None'}
                                </td>
                                <td>${user.is_active && user.membership.is_active ? 'Active' : 'Inactive'}</td>
                                <td class="actions"><button class="btn btn-sm btn-secondary" onclick="AuthPage.showUserForm(${index})">Edit</button></td>
                            </tr>`).join('')}
                    </tbody>
                </table></div>
            </div>`;
    },

    showUserForm(index = null) {
        const user = index === null ? null : AuthPage._usersCache[index];
        const meta = AuthPage._metaCache || { roles: [], permissions: [] };
        const allow = new Set(user?.membership?.allow_permissions || []);
        const deny = new Set(user?.membership?.deny_permissions || []);
        openModal(user ? `Edit ${user.full_name}` : 'New User', `
            <form onsubmit="AuthPage.saveUser(event, ${index === null ? 'null' : index})">
                <div class="form-grid">
                    <div class="form-group full-width"><label>Full Name</label><input name="full_name" required value="${escapeHtml(user?.full_name || '')}"></div>
                    <div class="form-group full-width"><label>Email</label><input name="email" type="email" ${user ? 'disabled' : 'required'} value="${escapeHtml(user?.email || '')}"></div>
                    <div class="form-group full-width"><label>Password ${user ? '(optional)' : ''}</label><input name="password" type="password" ${user ? '' : 'required'} minlength="8"></div>
                    <div class="form-group full-width"><label>Role Template</label>
                        <select name="role_key">${meta.roles.map(role => `<option value="${role.key}" ${user?.membership?.role_key === role.key ? 'selected' : ''}>${escapeHtml(role.label)}</option>`).join('')}</select>
                    </div>
                </div>
                <div class="settings-section">
                    <h3>Allow Overrides</h3>
                    <div class="card-grid">${meta.permissions.map(permission => `<label class="card" style="cursor:pointer;"><input type="checkbox" name="allow_permissions" value="${permission.key}" ${allow.has(permission.key) ? 'checked' : ''}> <strong>${escapeHtml(AuthPage.formatPermissionLabel(permission.key))}</strong><div style="font-size:10px; color:var(--text-muted); margin-top:4px;">${escapeHtml(permission.description)}</div></label>`).join('')}</div>
                </div>
                <div class="settings-section">
                    <h3>Deny Overrides</h3>
                    <div class="card-grid">${meta.permissions.map(permission => `<label class="card" style="cursor:pointer;"><input type="checkbox" name="deny_permissions" value="${permission.key}" ${deny.has(permission.key) ? 'checked' : ''}> <strong>${escapeHtml(AuthPage.formatPermissionLabel(permission.key))}</strong><div style="font-size:10px; color:var(--text-muted); margin-top:4px;">${escapeHtml(permission.description)}</div></label>`).join('')}</div>
                </div>
                <div class="form-grid">
                    <div class="form-group"><label><input type="checkbox" name="is_active" ${user ? (user.is_active ? 'checked' : '') : 'checked'}> User Active</label></div>
                    <div class="form-group"><label><input type="checkbox" name="membership_active" ${user ? (user.membership.is_active ? 'checked' : '') : 'checked'}> Membership Active</label></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${user ? 'Update User' : 'Create User'}</button>
                </div>
            </form>`);
    },

    async saveUser(e, index) {
        e.preventDefault();
        const form = e.target;
        const collect = (name) => Array.from(form.querySelectorAll(`input[name="${name}"]:checked`)).map((el) => el.value);
        const payload = {
            full_name: form.full_name.value,
            role_key: form.role_key.value,
            allow_permissions: collect('allow_permissions'),
            deny_permissions: collect('deny_permissions'),
            is_active: !!form.is_active.checked,
            membership_active: !!form.membership_active.checked,
        };
        if (form.password.value) payload.password = form.password.value;
        try {
            if (index === null) {
                payload.email = form.email.value;
                await API.post('/auth/users', payload);
                toast('User created');
            } else {
                const user = AuthPage._usersCache[index];
                await API.put(`/auth/users/${user.id}`, payload);
                toast('User updated');
            }
            closeModal();
            App.navigate('#/users-access');
        } catch (err) {
            toast(err.message, 'error');
        }
    },
};
