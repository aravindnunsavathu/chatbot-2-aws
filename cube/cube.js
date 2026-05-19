module.exports = {
  dbType: "postgres",
  driverFactory: () => require("@cubejs-backend/postgres-driver"),
  queryRewrite: (query) => query,
};
