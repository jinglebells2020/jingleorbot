import { useEffect, useState } from 'react';

export default function Resources() {
  const API_URL = process.env.NEXT_PUBLIC_API_URL;
  const [resources, setResources] = useState([]);
  const [title, setTitle] = useState('');
  const [url, setUrl] = useState('');

  useEffect(() => {
    fetch(`${API_URL}/api/resources`)
      .then(r => r.json())
      .then(setResources);
  }, []);

  const add = async () => {
    const res = await fetch(`${API_URL}/api/resources`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, url })
    });
    const item = await res.json();
    setResources([...resources, item]);
    setTitle('');
    setUrl('');
  };

  return (
    <div>
      <h1>Resources</h1>
      <input value={title} onChange={e => setTitle(e.target.value)} placeholder="Title" />
      <input value={url} onChange={e => setUrl(e.target.value)} placeholder="URL" />
      <button onClick={add}>Add</button>
      <ul>
        {resources.map(r => (
          <li key={r._id}><a href={r.url}>{r.title}</a></li>
        ))}
      </ul>
    </div>
  );
}
