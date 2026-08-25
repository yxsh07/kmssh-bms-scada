# BMS Telemetry Prototype

This is a monorepo for the BMS telemetry prototype, built with Node.js, Fastify, React Native (Expo), and Python for data parsing.

## Structure
- `/backend`: Node.js 20, Fastify, TypeScript, ES modules
- `/mobile`: Expo (React Native) + TypeScript
- `/data`: CSV parsing scripts (Python), sample export files
- `/docs`: Architecture docs
- `/shared`: TypeScript types shared between backend and mobile

## Setup Instructions

### Prerequisites
- Node.js 20+
- pnpm 8+ or 9+
- Python 3.10+
- Expo CLI

### 1. Install Dependencies

From the root directory, install dependencies across all workspaces:

```bash
pnpm install
```

### 2. Local Development

**Backend:**
```bash
cd backend
pnpm run dev
```

**Mobile:**
```bash
cd mobile
pnpm run start
```

**Data:**
You can use Python in the `/data` directory to process CSV files. Ensure you do not commit large raw CSV files (the `data/raw` directory is ignored).

### Linting and Formatting
```bash
pnpm run lint
```

### Type Checking
```bash
pnpm run typecheck
```
