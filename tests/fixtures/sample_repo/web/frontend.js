// Browser frontend: fetches users and renders them.
const api = require('./apiClient');

function renderUsers(users) {
  return users.map((u) => `<li>${u.name}</li>`).join('');
}

async function refresh() {
  const users = await api.fetchUsers();
  document.body.innerHTML = `<ul>${renderUsers(users)}</ul>`;
}

module.exports = { renderUsers, refresh };
