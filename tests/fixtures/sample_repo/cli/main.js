// Node CLI entry point.
const { fetchUsers } = require('../web/apiClient');

fetchUsers().then((users) => console.log(users));
