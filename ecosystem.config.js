module.exports = {
  apps: [{
    name: "ai-drama-api",
    script: "main.py",
    cwd: "/www/wwwroot/api.mzsh.top",
    instances: 1,
    exec_mode: "fork",
    max_restarts: 50,
    min_uptime: "30s",
    restart_delay: 3000,
    exp_backoff_restart_delay: 3000,
    max_restart_delay: 120000,
    kill_timeout: 15000,
    listen_timeout: 15000,
    watch: false,
    env: {
      PYTHONUNBUFFERED: "1",
      // [安全修复] 管理员 token 白名单（逗号分隔），持有这些 token 的请求可访问
      // 密钥配置 / 模型重置等敏感接口。轮换密钥时一并更换此值。
      ADMIN_TOKENS: "adm_28650bcb15f8097b8004aab3301624bcb791624c1af9d53472ff989a0759fcde",
    }
  }]
};
