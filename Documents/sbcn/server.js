const express = require('express');
const bcrypt = require('bcrypt');
const crypto = require('crypto');
const path = require('path');
const fs = require('fs');

const app = express();
const PORT = process.env.PORT || 8901;
const BCRYPT_ROUNDS = 10;
const SESSION_DAYS = 90;

app.use(express.json({ limit: '2mb' }));
app.use(express.static(path.join(__dirname)));

// ===== JSON FILE DB (no PostgreSQL needed) =====
const DB_FILE = path.join(__dirname, 'db.json');

function loadDB() {
  try {
    if (fs.existsSync(DB_FILE)) return JSON.parse(fs.readFileSync(DB_FILE, 'utf-8'));
  } catch (e) { console.error('DB read error:', e.message); }
  return { users: [], sessions: [], userData: [] };
}

function saveDB(db) {
  fs.writeFileSync(DB_FILE, JSON.stringify(db, null, 2));
}

let _saveTimer = null;
function saveDBDebounced(db) {
  if (_saveTimer) clearTimeout(_saveTimer);
  _saveTimer = setTimeout(() => saveDB(db), 300);
}

function findUser(db, username) {
  return db.users.find(u => u.username === username);
}

function findUserById(db, id) {
  return db.users.find(u => u.id === id);
}

function findSession(db, token) {
  return db.sessions.find(s => s.token === token && new Date(s.expiresAt) > new Date());
}

function getUserData(db, userId) {
  let d = db.userData.find(u => u.userId === userId);
  if (!d) {
    d = { userId, words: {}, quizCount: 0, history: [], settings: {}, hopperHighScore: 0 };
    db.userData.push(d);
    saveDB(db);
  }
  return d;
}

// Seed default user
async function seedDB() {
  const db = loadDB();
  if (!findUser(db, 'amirali')) {
    const hash = await bcrypt.hash('amirali2015', BCRYPT_ROUNDS);
    const id = Date.now();
    db.users.push({ id, username: 'amirali', displayName: 'amirali', passwordHash: hash });
    db.userData.push({ userId: id, words: {}, quizCount: 0, history: [], settings: {}, hopperHighScore: 0 });
    saveDB(db);
    console.log('Seeded default user: amirali');
  }
}

// ===== AUTH MIDDLEWARE =====
function requireAuth(req, res, next) {
  const auth = req.headers.authorization;
  if (!auth || !auth.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Not authenticated' });
  }
  const token = auth.slice(7);
  const db = loadDB();
  const session = findSession(db, token);
  if (!session) return res.status(401).json({ error: 'Session expired' });
  const user = findUserById(db, session.userId);
  if (!user) return res.status(401).json({ error: 'User not found' });
  req.user = { user_id: user.id, username: user.username, display_name: user.displayName };
  next();
}

// ===== AUTH ROUTES =====
app.post('/api/auth/signup', async (req, res) => {
  try {
    const { username, password, name } = req.body;
    if (!username || !password || !name) return res.status(400).json({ error: 'All fields required' });
    if (username.length < 3) return res.status(400).json({ error: 'Username must be at least 3 characters' });
    if (password.length < 4) return res.status(400).json({ error: 'Password must be at least 4 characters' });
    if (/[^a-z0-9_]/.test(username)) return res.status(400).json({ error: 'Username: only lowercase letters, numbers, underscore' });

    const db = loadDB();
    if (findUser(db, username)) return res.status(409).json({ error: 'Username already taken' });

    const hash = await bcrypt.hash(password, BCRYPT_ROUNDS);
    const id = Date.now();
    db.users.push({ id, username, displayName: name, passwordHash: hash });
    db.userData.push({ userId: id, words: {}, quizCount: 0, history: [], settings: {}, hopperHighScore: 0 });

    const token = crypto.randomBytes(32).toString('hex');
    const expiresAt = new Date(Date.now() + SESSION_DAYS * 24 * 60 * 60 * 1000).toISOString();
    db.sessions.push({ token, userId: id, expiresAt });
    saveDB(db);

    res.json({ token, username, name });
  } catch (err) {
    console.error('Signup error:', err);
    res.status(500).json({ error: 'Server error' });
  }
});

app.post('/api/auth/login', async (req, res) => {
  try {
    const { username, password } = req.body;
    if (!username || !password) return res.status(400).json({ error: 'All fields required' });

    const db = loadDB();
    const user = findUser(db, username.toLowerCase());
    if (!user) return res.status(401).json({ error: 'Invalid username or password' });

    const valid = await bcrypt.compare(password, user.passwordHash);
    if (!valid) return res.status(401).json({ error: 'Invalid username or password' });

    const token = crypto.randomBytes(32).toString('hex');
    const expiresAt = new Date(Date.now() + SESSION_DAYS * 24 * 60 * 60 * 1000).toISOString();
    db.sessions.push({ token, userId: user.id, expiresAt });
    saveDB(db);

    res.json({ token, username: user.username, name: user.displayName });
  } catch (err) {
    console.error('Login error:', err);
    res.status(500).json({ error: 'Server error' });
  }
});

app.post('/api/auth/logout', requireAuth, (req, res) => {
  const token = req.headers.authorization.slice(7);
  const db = loadDB();
  db.sessions = db.sessions.filter(s => s.token !== token);
  saveDB(db);
  res.json({ ok: true });
});

app.get('/api/auth/session', requireAuth, (req, res) => {
  res.json({ username: req.user.username, name: req.user.display_name });
});

// ===== DATA ROUTES =====
app.get('/api/data', requireAuth, (req, res) => {
  const db = loadDB();
  const d = getUserData(db, req.user.user_id);
  res.json({
    words: d.words || {},
    quizCount: d.quizCount || 0,
    history: d.history || [],
    settings: d.settings || {},
    hopperHighScore: d.hopperHighScore || 0
  });
});

app.put('/api/data/words', requireAuth, (req, res) => {
  const db = loadDB();
  const d = getUserData(db, req.user.user_id);
  d.words = req.body.words;
  saveDBDebounced(db);
  res.json({ ok: true });
});

app.put('/api/data/quiz-count', requireAuth, (req, res) => {
  const db = loadDB();
  const d = getUserData(db, req.user.user_id);
  d.quizCount = req.body.quizCount;
  saveDBDebounced(db);
  res.json({ ok: true });
});

app.put('/api/data/history', requireAuth, (req, res) => {
  const db = loadDB();
  const d = getUserData(db, req.user.user_id);
  d.history = req.body.history;
  saveDBDebounced(db);
  res.json({ ok: true });
});

app.put('/api/data/settings', requireAuth, (req, res) => {
  const db = loadDB();
  const d = getUserData(db, req.user.user_id);
  d.settings = req.body.settings;
  saveDBDebounced(db);
  res.json({ ok: true });
});

app.put('/api/data/hopper', requireAuth, (req, res) => {
  const db = loadDB();
  const d = getUserData(db, req.user.user_id);
  d.hopperHighScore = req.body.hopperHighScore;
  saveDBDebounced(db);
  res.json({ ok: true });
});

app.post('/api/data/reset', requireAuth, (req, res) => {
  const db = loadDB();
  const d = getUserData(db, req.user.user_id);
  d.words = {};
  d.quizCount = 0;
  d.history = [];
  d.settings = {};
  d.hopperHighScore = 0;
  saveDB(db);
  res.json({ ok: true });
});

app.post('/api/data/migrate', requireAuth, (req, res) => {
  const db = loadDB();
  const d = getUserData(db, req.user.user_id);
  if (d.words && Object.keys(d.words).length > 0) {
    return res.json({ ok: true, migrated: false, reason: 'Server data already exists' });
  }
  const { words, quizCount, history, settings, hopperHighScore } = req.body;
  d.words = words || {};
  d.quizCount = quizCount || 0;
  d.history = history || [];
  d.settings = settings || {};
  d.hopperHighScore = hopperHighScore || 0;
  saveDB(db);
  res.json({ ok: true, migrated: true });
});

// ===== SPA FALLBACK =====
app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, 'index.html'));
});

// ===== START =====
async function start() {
  await seedDB();
  app.listen(PORT, () => {
    console.log(`Server running on http://localhost:${PORT}`);
    console.log('Using JSON file database (db.json)');
  });
}

start();
