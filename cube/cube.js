module.exports = {
  dbType: "postgres",

  driverFactory: () => {
    const PostgresDriver = require("@cubejs-backend/postgres-driver");
    const cfg = {
      host:     process.env.CUBEJS_DB_HOST,
      port:     parseInt(process.env.CUBEJS_DB_PORT || "5432"),
      database: process.env.CUBEJS_DB_NAME,
      user:     process.env.CUBEJS_DB_USER,
      password: process.env.CUBEJS_DB_PASS,
      ssl:      process.env.CUBEJS_DB_SSL === "true" ? { rejectUnauthorized: false } : false,
    };

    if (process.env.CUBEJS_DB_SSH_TUNNEL === "true") {
      cfg.ssl = { rejectUnauthorized: false };
    }

    return new PostgresDriver(cfg);
  },
};
