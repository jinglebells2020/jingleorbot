import { useEffect, useState } from 'react';

export default function Financial() {
  const API_URL = process.env.NEXT_PUBLIC_API_URL;
  const [data, setData] = useState([]);
  const [revenue, setRevenue] = useState('');
  const [expenses, setExpenses] = useState('');

  useEffect(() => {
    fetch(`${API_URL}/api/finance`)
      .then(r => r.json())
      .then(setData);
  }, []);

  const save = async () => {
    const res = await fetch(`${API_URL}/api/finance`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revenue, expenses })
    });
    const item = await res.json();
    setData([...data, item]);
    setRevenue('');
    setExpenses('');
  };

  return (
    <div>
      <h1>Financial Data</h1>
      <input value={revenue} onChange={e => setRevenue(e.target.value)} placeholder="Revenue" />
      <input value={expenses} onChange={e => setExpenses(e.target.value)} placeholder="Expenses" />
      <button onClick={save}>Save</button>
      <ul>
        {data.map(d => (
          <li key={d._id}>{d.revenue} / {d.expenses}</li>
        ))}
      </ul>
    </div>
  );
}
