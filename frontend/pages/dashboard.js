import { useEffect, useState } from 'react';

export default function Dashboard() {
  const [tasks, setTasks] = useState([]);
  const [title, setTitle] = useState('');

  useEffect(() => {
    fetch('http://localhost:5000/api/tasks')
      .then(r => r.json())
      .then(setTasks);
  }, []);

  const addTask = async () => {
    const res = await fetch('http://localhost:5000/api/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title })
    });
    const data = await res.json();
    setTasks([...tasks, data]);
    setTitle('');
  };

  return (
    <div>
      <h1>Dashboard</h1>
      <input value={title} onChange={e => setTitle(e.target.value)} placeholder="New task" />
      <button onClick={addTask}>Add</button>
      <ul>
        {tasks.map(t => <li key={t._id}>{t.title}</li>)}
      </ul>
    </div>
  );
}
