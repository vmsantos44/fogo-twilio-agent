# Alfa Twilio Voice Agent

A voice agent that connects Twilio phone calls to OpenAI's Realtime API with Zoho CRM integration for application status lookup.

## Features

- **Real-time Voice AI** - Powered by OpenAI GPT-4o Realtime API
- **Caller ID Lookup** - Automatically fetches caller data from Zoho CRM
- **Application Status** - Candidates can check their application status
- **Knowledge Base** - Answers general questions using OpenAI Assistants
- **Identity Verification** - Verifies callers by name and language before sharing status

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Required:
- `OPENAI_API_KEY` - Your OpenAI API key
- `ZOHO_CLIENT_ID` - Zoho CRM OAuth client ID
- `ZOHO_CLIENT_SECRET` - Zoho CRM OAuth client secret
- `ZOHO_REFRESH_TOKEN` - Zoho CRM refresh token

Optional:
- `OPENAI_ASSISTANT_ID` - Assistant ID for knowledge base (file search)

### 3. Run the server

```bash
python server.py
```

Or with uvicorn:

```bash
uvicorn server:app --host 0.0.0.0 --port 8005
```

### 4. Configure Twilio

1. Go to Twilio Console → Phone Numbers → Your Number
2. Under "Voice Configuration":
   - Webhook URL: `https://your-domain.com/incoming-call`
   - HTTP Method: `HTTP GET`
3. Save

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check and status |
| `/incoming-call` | GET/POST | Twilio webhook - returns TwiML |
| `/media-stream` | WebSocket | Twilio Media Stream connection |
| `/health` | GET | Simple health check |

## How It Works

1. **Incoming Call** - Twilio sends webhook to `/incoming-call`
2. **Caller ID Lookup** - Server pre-fetches caller data from Zoho CRM
3. **Media Stream** - Twilio connects audio via WebSocket to `/media-stream`
4. **OpenAI Realtime** - Server bridges audio to OpenAI Realtime API
5. **Voice Interaction** - AI agent (Angela) handles the conversation
6. **Function Calls** - AI can look up application status or search knowledge base

## Voice Agent Capabilities

- Greet callers professionally
- Look up application status in Zoho CRM
- Verify caller identity (name + language)
- Answer questions from knowledge base
- Handle Spanish-speaking callers

## Zoho CRM Setup

1. Go to [Zoho API Console](https://api-console.zoho.com/)
2. Create a "Self Client" application
3. Generate refresh token with scope: `ZohoCRM.coql.READ`
4. Add credentials to `.env`

## Deployment

For production, use a process manager like systemd:

```ini
[Unit]
Description=Alfa Twilio Voice Agent
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/fogo-twilio-agent
ExecStart=/path/to/venv/bin/uvicorn server:app --host 127.0.0.1 --port 8005
Restart=always

[Install]
WantedBy=multi-user.target
```

## License

Proprietary - Alfa Systems
