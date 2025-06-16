import { useEffect, useState } from 'react';

export default function Financial() {
  const [data, setData] = useState([]);
  const [revenue, setRevenue] = useState('');
  const [expenses, setExpenses] = useState('');

  useEffect(() => {
    fetch('http://localhost:5000/api/finance')
      .then(r => r.json())
      .then(setData);
  }, []);

  const save = async () => {
    const res = await fetch('http://localhost:5000/api/finance', {
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
