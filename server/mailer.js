'use strict';
// Sends mail via SMTP when SMTP_* env vars are set; otherwise logs the link
// to the server console so you can wire delivery later (e.g. relay via a Discord bot).
const nodemailer = require('nodemailer');

const HAS_SMTP = !!(process.env.SMTP_HOST && process.env.SMTP_USER && process.env.SMTP_PASS);
let tx = null;
if (HAS_SMTP) {
  tx = nodemailer.createTransport({
    host: process.env.SMTP_HOST,
    port: Number(process.env.SMTP_PORT || 587),
    secure: String(process.env.SMTP_SECURE || 'false') === 'true',
    auth: { user: process.env.SMTP_USER, pass: process.env.SMTP_PASS },
  });
}

async function sendResetMail(to, link) {
  const from = process.env.MAIL_FROM || 'DENG Rejoin <no-reply@deng.my.id>';
  const subject = 'Reset your DENG Rejoin password';
  const text = `Someone asked to reset your DENG Rejoin password.\n\nReset link (valid 30 min):\n${link}\n\nIf this wasn't you, ignore this email.`;
  if (!tx) {
    // Do not log the reset link/token (sensitive). Configure SMTP to deliver resets.
    console.log('[mailer] password reset requested but no SMTP configured — email not sent');
    return { delivered: false };
  }
  await tx.sendMail({ from, to, subject, text });
  return { delivered: true };
}

module.exports = { sendResetMail, HAS_SMTP };
