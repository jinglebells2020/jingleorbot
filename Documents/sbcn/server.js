const express = require('express');
const { Pool } = require('pg');
const bcrypt = require('bcrypt');
const crypto = require('crypto');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3000;
const BCRYPT_ROUNDS = 10;
const SESSION_DAYS = 90;

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: process.env.DATABASE_URL && !process.env.DATABASE_URL.includes('localhost')
    ? { rejectUnauthorized: false }
    : false
});

app.use(express.json({ limit: '2mb' }));
app.use(express.static(path.join(__dirname)));

// ===== DB INIT =====
async function initDB() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS users (
      id SERIAL PRIMARY KEY,
      username VARCHAR(50) UNIQUE NOT NULL,
      display_name VARCHAR(100) NOT NULL,
      password_hash TEXT NOT NULL,
      created_at TIMESTAMPTZ DEFAULT NOW()
    )
  `);
  await pool.query(`
    CREATE TABLE IF NOT EXISTS sessions (
      token VARCHAR(128) PRIMARY KEY,
      user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at TIMESTAMPTZ DEFAULT NOW(),
      expires_at TIMESTAMPTZ NOT NULL
    )
  `);
  await pool.query(`
    CREATE TABLE IF NOT EXISTS user_data (
      user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      words JSONB DEFAULT '{}'::jsonb,
      quiz_count INT DEFAULT 0,
      history JSONB DEFAULT '[]'::jsonb,
      settings JSONB DEFAULT '{}'::jsonb,
      hopper_high_score INT DEFAULT 0,
      updated_at TIMESTAMPTZ DEFAULT NOW()
    )
  `);
}

async function seedAmirali() {
  const exists = await pool.query('SELECT id FROM users WHERE username = $1', ['amirali']);
  if (exists.rows.length > 0) return;
  const hash = await bcrypt.hash('amirali2015', BCRYPT_ROUNDS);
  const res = await pool.query(
    'INSERT INTO users (username, display_name, password_hash) VALUES ($1, $2, $3) RETURNING id',
    ['amirali', 'amirali', hash]
  );
  await pool.query(
    'INSERT INTO user_data (user_id) VALUES ($1)',
    [res.rows[0].id]
  );
}

// ===== AUTH MIDDLEWARE =====
async function requireAuth(req, res, next) {
  const auth = req.headers.authorization;
  if (!auth || !auth.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Not authenticated' });
  }
  const token = auth.slice(7);
  const result = await pool.query(
    'SELECT s.user_id, u.username, u.display_name FROM sessions s JOIN users u ON s.user_id = u.id WHERE s.token = $1 AND s.expires_at > NOW()',
    [token]
  );
  if (result.rows.length === 0) {
    return res.status(401).json({ error: 'Session expired' });
  }
  req.user = result.rows[0];
  next();
}

function createToken() {
  return crypto.randomBytes(32).toString('hex');
}

// ===== AUTH ROUTES =====
app.post('/api/auth/signup', async (req, res) => {
  try {
    const { username, password, name } = req.body;
    if (!username || !password || !name) {
      return res.status(400).json({ error: 'All fields required' });
    }
    if (username.length < 3) {
      return res.status(400).json({ error: 'Username must be at least 3 characters' });
    }
    if (password.length < 4) {
      return res.status(400).json({ error: 'Password must be at least 4 characters' });
    }
    if (/[^a-z0-9_]/.test(username)) {
      return res.status(400).json({ error: 'Username: only lowercase letters, numbers, underscore' });
    }

    const existing = await pool.query('SELECT id FROM users WHERE username = $1', [username]);
    if (existing.rows.length > 0) {
      return res.status(409).json({ error: 'Username already taken' });
    }

    const hash = await bcrypt.hash(password, BCRYPT_ROUNDS);
    const userRes = await pool.query(
      'INSERT INTO users (username, display_name, password_hash) VALUES ($1, $2, $3) RETURNING id',
      [username, name, hash]
    );
    const userId = userRes.rows[0].id;

    await pool.query('INSERT INTO user_data (user_id) VALUES ($1)', [userId]);

    const token = createToken();
    const expiresAt = new Date(Date.now() + SESSION_DAYS * 24 * 60 * 60 * 1000);
    await pool.query(
      'INSERT INTO sessions (token, user_id, expires_at) VALUES ($1, $2, $3)',
      [token, userId, expiresAt]
    );

    res.json({ token, username, name });
  } catch (err) {
    console.error('Signup error:', err);
    res.status(500).json({ error: 'Server error' });
  }
});

app.post('/api/auth/login', async (req, res) => {
  try {
    const { username, password } = req.body;
    if (!username || !password) {
      return res.status(400).json({ error: 'All fields required' });
    }

    const userRes = await pool.query(
      'SELECT id, username, display_name, password_hash FROM users WHERE username = $1',
      [username.toLowerCase()]
    );
    if (userRes.rows.length === 0) {
      return res.status(401).json({ error: 'Invalid username or password' });
    }

    const user = userRes.rows[0];
    const valid = await bcrypt.compare(password, user.password_hash);
    if (!valid) {
      return res.status(401).json({ error: 'Invalid username or password' });
    }

    const token = createToken();
    const expiresAt = new Date(Date.now() + SESSION_DAYS * 24 * 60 * 60 * 1000);
    await pool.query(
      'INSERT INTO sessions (token, user_id, expires_at) VALUES ($1, $2, $3)',
      [token, user.id, expiresAt]
    );

    res.json({ token, username: user.username, name: user.display_name });
  } catch (err) {
    console.error('Login error:', err);
    res.status(500).json({ error: 'Server error' });
  }
});

app.post('/api/auth/logout', requireAuth, async (req, res) => {
  try {
    const token = req.headers.authorization.slice(7);
    await pool.query('DELETE FROM sessions WHERE token = $1', [token]);
    res.json({ ok: true });
  } catch (err) {
    console.error('Logout error:', err);
    res.status(500).json({ error: 'Server error' });
  }
});

app.get('/api/auth/session', requireAuth, async (req, res) => {
  res.json({ username: req.user.username, name: req.user.display_name });
});

// ===== DATA ROUTES =====
app.get('/api/data', requireAuth, async (req, res) => {
  try {
    const result = await pool.query(
      'SELECT words, quiz_count, history, settings, hopper_high_score FROM user_data WHERE user_id = $1',
      [req.user.user_id]
    );
    if (result.rows.length === 0) {
      return res.json({ words: {}, quizCount: 0, history: [], settings: {}, hopperHighScore: 0 });
    }
    const d = result.rows[0];
    res.json({
      words: d.words || {},
      quizCount: d.quiz_count || 0,
      history: d.history || [],
      settings: d.settings || {},
      hopperHighScore: d.hopper_high_score || 0
    });
  } catch (err) {
    console.error('Get data error:', err);
    res.status(500).json({ error: 'Server error' });
  }
});

app.put('/api/data/words', requireAuth, async (req, res) => {
  try {
    await pool.query(
      'UPDATE user_data SET words = $1, updated_at = NOW() WHERE user_id = $2',
      [JSON.stringify(req.body.words), req.user.user_id]
    );
    res.json({ ok: true });
  } catch (err) {
    console.error('Save words error:', err);
    res.status(500).json({ error: 'Server error' });
  }
});

app.put('/api/data/quiz-count', requireAuth, async (req, res) => {
  try {
    await pool.query(
      'UPDATE user_data SET quiz_count = $1, updated_at = NOW() WHERE user_id = $2',
      [req.body.quizCount, req.user.user_id]
    );
    res.json({ ok: true });
  } catch (err) {
    console.error('Save quiz count error:', err);
    res.status(500).json({ error: 'Server error' });
  }
});

app.put('/api/data/history', requireAuth, async (req, res) => {
  try {
    await pool.query(
      'UPDATE user_data SET history = $1, updated_at = NOW() WHERE user_id = $2',
      [JSON.stringify(req.body.history), req.user.user_id]
    );
    res.json({ ok: true });
  } catch (err) {
    console.error('Save history error:', err);
    res.status(500).json({ error: 'Server error' });
  }
});

app.put('/api/data/settings', requireAuth, async (req, res) => {
  try {
    await pool.query(
      'UPDATE user_data SET settings = $1, updated_at = NOW() WHERE user_id = $2',
      [JSON.stringify(req.body.settings), req.user.user_id]
    );
    res.json({ ok: true });
  } catch (err) {
    console.error('Save settings error:', err);
    res.status(500).json({ error: 'Server error' });
  }
});

app.put('/api/data/hopper', requireAuth, async (req, res) => {
  try {
    await pool.query(
      'UPDATE user_data SET hopper_high_score = $1, updated_at = NOW() WHERE user_id = $2',
      [req.body.hopperHighScore, req.user.user_id]
    );
    res.json({ ok: true });
  } catch (err) {
    console.error('Save hopper error:', err);
    res.status(500).json({ error: 'Server error' });
  }
});

app.post('/api/data/reset', requireAuth, async (req, res) => {
  try {
    await pool.query(
      `UPDATE user_data SET words = '{}'::jsonb, quiz_count = 0, history = '[]'::jsonb,
       settings = '{}'::jsonb, hopper_high_score = 0, updated_at = NOW() WHERE user_id = $1`,
      [req.user.user_id]
    );
    res.json({ ok: true });
  } catch (err) {
    console.error('Reset error:', err);
    res.status(500).json({ error: 'Server error' });
  }
});

app.post('/api/data/migrate', requireAuth, async (req, res) => {
  try {
    const current = await pool.query(
      'SELECT words FROM user_data WHERE user_id = $1',
      [req.user.user_id]
    );
    if (current.rows.length === 0) {
      return res.status(404).json({ error: 'No user data row' });
    }
    const existingWords = current.rows[0].words;
    if (existingWords && Object.keys(existingWords).length > 0) {
      return res.json({ ok: true, migrated: false, reason: 'Server data already exists' });
    }

    const { words, quizCount, history, settings, hopperHighScore } = req.body;
    await pool.query(
      `UPDATE user_data SET
        words = $1, quiz_count = $2, history = $3, settings = $4,
        hopper_high_score = $5, updated_at = NOW()
       WHERE user_id = $6`,
      [
        JSON.stringify(words || {}),
        quizCount || 0,
        JSON.stringify(history || []),
        JSON.stringify(settings || {}),
        hopperHighScore || 0,
        req.user.user_id
      ]
    );
    res.json({ ok: true, migrated: true });
  } catch (err) {
    console.error('Migrate error:', err);
    res.status(500).json({ error: 'Server error' });
  }
});

// ===== SPA FALLBACK =====
app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, 'index.html'));
});

// ===== START =====
async function start() {
  try {
    await initDB();
    console.log('Database tables initialized');
    await seedAmirali();
    console.log('Seed data checked');
    app.listen(PORT, () => {
      console.log(`Server running on port ${PORT}`);
    });
  } catch (err) {
    console.error('Startup error:', err);
    process.exit(1);
  }
}

start();
