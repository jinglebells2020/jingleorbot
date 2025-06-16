# OrbitaAI

OrbitaAI is a prototype web application that provides AI-powered consulting and project management for startup founders. It includes a Node.js Express backend with MongoDB and a Next.js frontend.

## Features

- **User Authentication**: Register and log in with JWT-based authentication.
- **Project Dashboard**: Manage tasks using a simple Kanban-style interface.
- **AI Assistant**: Chat with an OpenAI-powered assistant and store conversation history.
- **Financial Analytics**: Submit revenue and expense data and view stored records.
- **Resources**: Save and list useful articles or links.

## Development

```
cd backend && npm install
cp .env.example .env
npm run dev
```

In a separate terminal:

```
cd frontend && npm install
 npm run dev
```

Create a `frontend/.env.local` file (or copy from `frontend/.env.local.example`) with:

```
NEXT_PUBLIC_API_URL=http://localhost:5000
```

The frontend runs on `http://localhost:3000` and expects the backend at `http://localhost:5000`.
