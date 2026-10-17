// Thin HTTP client used by the frontend.
async function fetchUsers() {
  const response = await fetch('/api/users');
  return response.json();
}

module.exports = { fetchUsers };
