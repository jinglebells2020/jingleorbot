import { useState } from 'react';
import { useRouter } from 'next/router';

export default function Register() {
  const API_URL = process.env.NEXT_PUBLIC_API_URL;
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const router = useRouter();
  const register = async () => {
    await fetch(`${API_URL}/api/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    }).then(r => r.json()).then(() => router.push('/'));
  };
  return (
    <div>
      <h1>Register</h1>
      <input placeholder="username" value={username} onChange={e => setUsername(e.target.value)} />
      <input placeholder="password" type="password" value={password} onChange={e => setPassword(e.target.value)} />
      <button onClick={register}>Register</button>
    </div>
  );
}
