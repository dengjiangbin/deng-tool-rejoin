// PM2 ecosystem config for DENG Tool: Rejoin license panel bot.
// Usage: pm2 start ecosystem.config.js
// Or update existing: pm2 restart deng-rejoin-panel --update-env
module.exports = {
  apps: [
    {
      name: 'deng-rejoin-panel',
      interpreter: 'C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python313\\python.exe',
      script: '-m',
      args: 'bot.main',
      cwd: 'C:\\Users\\Administrator\\Desktop\\DENG Tool Rejoin',
      kill_timeout: 5000,       // wait up to 5s for clean exit before force-kill
      wait_ready: false,
      autorestart: true,
      watch: false,
      max_memory_restart: '300M',
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
    },
  ],
};
