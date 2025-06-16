import { useState, useEffect } from 'react';

export default function Chat() {
  const API_URL = process.env.NEXT_PUBLIC_API_URL;
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');

  useEffect(() => {
    fetch(`${API_URL}/api/chat`)
      .then(r => r.json())
      .then(setMessages);
  }, []);

  const send = async () => {
    const res = await fetch(`${API_URL}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: input })
    });
    const data = await res.json();
    setMessages([...messages, ...data]);
    setInput('');
  };

  return (
    <div>
      <h2>Assistant</h2>
      <div>
        {messages.map((m, i) => (
          <p key={i}><strong>{m.fromAI ? 'AI:' : 'You:'}</strong> {m.message}</p>
        ))}
      </div>
      <input value={input} onChange={e => setInput(e.target.value)} />
      <button onClick={send}>Send</button>
    </div>
  );
}
