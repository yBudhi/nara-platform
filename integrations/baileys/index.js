
import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason
} from '@whiskeysockets/baileys';

import pino from 'pino';
import qrcode from 'qrcode-terminal';
import { rm } from 'node:fs/promises';

const N8N_WEBHOOK_URL =
  process.env.N8N_WEBHOOK_URL ||
  'http://nara-n8n:5678/webhook/wa-agent';

const N8N_WEBHOOK_TOKEN =
  process.env.N8N_WEBHOOK_TOKEN;

const AUTH_DIR = './auth';
const RECONNECT_START_MS = 2000;
const RECONNECT_MAX_MS = 60000;

const normalizeJid = (jid) =>
  jid ? jid.replace(/:\d+@/, '@').toLowerCase() : '';

let reconnectDelay = RECONNECT_START_MS;
let reconnectTimer = null;
let restartingForPairing = false;

function clearReconnectTimer() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

function scheduleReconnect() {
  if (reconnectTimer || restartingForPairing) return;

  const delay = reconnectDelay;
  reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);

  console.log(`Menyambung ulang dalam ${delay / 1000} detik...`);

  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    startWhatsApp().catch((error) => {
      console.error('Gagal menyambung ulang:', error.message);
      scheduleReconnect();
    });
  }, delay);
}

async function restartForPairing() {
  if (restartingForPairing) return;

  restartingForPairing = true;
  clearReconnectTimer();
  reconnectDelay = RECONNECT_START_MS;

  try {
    await rm(AUTH_DIR, { recursive: true, force: true });
  } catch (error) {
    console.error('Gagal menghapus sesi lama:', error.message);
  }

  restartingForPairing = false;

  try {
    await startWhatsApp();
  } catch (error) {
    console.error('Gagal memulai ulang untuk pairing:', error.message);
    scheduleReconnect();
  }
}

function extractText(message) {
  if (!message) return '';

  return (
    message.conversation ??
    message.extendedTextMessage?.text ??
    message.imageMessage?.caption ??
    message.videoMessage?.caption ??
    ''
  );
}

const startWhatsApp = async () => {
  const { state, saveCreds } =
    await useMultiFileAuthState(AUTH_DIR);

  const sock = makeWASocket({
    auth: state,
    logger: pino({ level: 'silent' })
  });

  console.log('Identitas NARA:', sock.user);

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', ({
    connection,
    lastDisconnect,
    qr
  }) => {
    if (qr) {
      console.log('\nScan QR melalui WhatsApp di HP:\n');
      qrcode.generate(qr, { small: true });
    }

    if (connection === 'open') {
      console.log('WhatsApp terhubung.');
      reconnectDelay = RECONNECT_START_MS;
      clearReconnectTimer();
    }

    if (connection === 'close') {
      const statusCode =
        lastDisconnect?.error?.output?.statusCode;

      console.log('Koneksi terputus. Kode:', statusCode);

      if (statusCode === DisconnectReason.loggedOut) {
        console.log('Sesi logout. Menghapus sesi lama dan menampilkan QR baru.');
        restartForPairing();
        return;
      }

      scheduleReconnect();
    }
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;

    for (const msg of messages) {
      if (!msg.message || msg.key.fromMe) continue;

      const jid = msg.key.remoteJid;

      if (!jid || jid === 'status@broadcast') continue;

      const isGroup = jid.endsWith('@g.us');

      const text = extractText(msg.message).trim();
      if (!text) continue;

      // Ambil pesan yang sedang di-reply

      // Ambil konteks pesan (Reply dan Mention)
      const contextInfo =
        msg.message.extendedTextMessage?.contextInfo ??
        msg.message.imageMessage?.contextInfo ??
        msg.message.videoMessage?.contextInfo;

      const quotedMessage = contextInfo?.quotedMessage;
      const quotedText = extractText(quotedMessage).trim();

      // Identitas WhatsApp NARA
      const botJids = [
        sock.user?.id,
        sock.user?.lid
      ].filter(Boolean).map(normalizeJid);

      // Cek apakah pesan yang di-reply berasal dari NARA
      const quotedSender = normalizeJid(contextInfo?.participant);

      const isReplyToBot =
        Boolean(quotedMessage) &&
        botJids.includes(quotedSender);

      // Cek apakah NARA benar-benar di-mention
      const mentionedJids = contextInfo?.mentionedJid ?? [];

      const isMentioned = mentionedJids.some(
        mentionedJid => botJids.includes(normalizeJid(mentionedJid))
      );

      // Di grup: hanya proses Mention NARA atau Reply ke NARA
      // Di chat pribadi: semua pesan masuk diproses
      if (isGroup && !isMentioned && !isReplyToBot) continue;

      // Hapus teks mention dari pertanyaan
      let question = text;

      if (isGroup && isMentioned) {
        question = question.replace(/@\d+/g, '').trim();
      }

      if (!question) {
        await sock.sendMessage(jid, {
          text: 'Halo, saya NARA. Silakan tuliskan pertanyaan yang ingin disampaikan.'
        }, { quoted: msg });
        continue;
      }

      console.log('\nPertanyaan:', question);
      console.log('Mention NARA:', isMentioned);
      console.log('Reply ke NARA:', isReplyToBot);
      console.log('Pesan Reply:', quotedText || '(tidak ada)');

      try {
        const headers = {
          'Content-Type': 'application/json'
        };

        if (N8N_WEBHOOK_TOKEN) {
          headers.Authorization = `Bearer ${N8N_WEBHOOK_TOKEN}`;
        }

        const response = await fetch(N8N_WEBHOOK_URL, {
          method: 'POST',
          headers,
          body: JSON.stringify({
            text: question,
            replyContext: quotedText,
            chatId: jid,
            currentDateTime: new Intl.DateTimeFormat('id-ID', {
              weekday: 'long',
              year: 'numeric',
              month: 'long',
              day: 'numeric',
              hour: '2-digit',
              minute: '2-digit',
              second: '2-digit',
              timeZone: 'Asia/Jakarta'
            }).format(new Date())
          }),
          signal: AbortSignal.timeout(120000)
        });

        if (!response.ok) {
          throw new Error(
            `n8n HTTP ${response.status}: ${await response.text()}`
          );
        }

        const data = await response.json();
        const reply = data.reply;

        if (typeof reply !== 'string' || !reply.trim()) {
          throw new Error(
            'Respons n8n tidak memiliki field reply yang valid.'
          );
        }

        // Siapkan balasan NARA
        let finalReply = reply.trim();
        let mentions = [];

        // Mention seluruh anggota hanya saat diminta memperkenalkan diri di grup

        // Deteksi permintaan perkenalan NARA
        const isIntroRequest =
          isGroup &&
          /\b(perkenalkan|perkenalan|kenalan|kenalkan diri|kenalin|sapa)\b/i.test(question);

        if (isIntroRequest) {
          try {
            const groupMetadata = await sock.groupMetadata(jid);

            // Ambil anggota yang secara eksplisit di-mention oleh pengguna
            const selectedMembers = mentionedJids.filter(
              memberJid =>
                memberJid &&
                !botJids.includes(normalizeJid(memberJid))
            );

            // Cek apakah pengguna meminta NARA menyapa semua anggota
            const isMentionAll =
              /\b(semua anggota|seluruh anggota|semua orang|seluruh grup|semua di grup)\b/i.test(question);

            if (isMentionAll) {
              mentions = (groupMetadata.participants ?? [])
                .map(member => member.id)
                .filter(memberJid =>
                  memberJid &&
                  !botJids.includes(normalizeJid(memberJid))
                );
            } else {
              // Jika bukan semua anggota, hanya mention orang yang dipilih
              mentions = selectedMembers;
            }

            const mentionText = mentions
              .map(memberJid =>
                '@' + normalizeJid(memberJid).split('@')[0]
              )
              .join(' ');

            if (mentionText) {
              finalReply += '\n\n' + mentionText;
            }

            console.log('Mode mention:', isMentionAll ? 'Semua anggota' : 'Anggota tertentu');
            console.log('Jumlah anggota di-mention:', mentions.length);

          } catch (error) {
            console.error('Gagal mengambil anggota grup:', error.message);
            mentions = [];
          }
        }


        // Kirim balasan beserta mention WhatsApp asli
        await sock.sendMessage(jid, {
          text: finalReply,
          mentions
        }, { quoted: msg });

        console.log('Balasan terkirim ke WhatsApp.');


        console.log('Balasan terkirim ke WhatsApp.');

      } catch (error) {
        console.error('Gagal memproses pesan:', error.message);

        await sock.sendMessage(jid, {
          text: 'Maaf, AI belum bisa memproses pertanyaan saat ini. Coba lagi nanti.'
        }, { quoted: msg });
      }
    }
  });
};

startWhatsApp().catch((error) => {
  console.error('Gagal menjalankan konektor:', error);
  process.exit(1);
});
