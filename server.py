"""
Alfa Twilio Voice Agent - Backend Server
Connects Twilio phone calls to OpenAI Realtime API with Zoho CRM Integration
"""

import os
import json
# Unused: import base64
import asyncio
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import websockets.client
from dotenv import load_dotenv

load_dotenv()

DEBUG = os.getenv("DEBUG", "false").lower() == "true"

app = FastAPI(title="Alfa Twilio Voice Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store caller info from incoming webhook
caller_info_cache = {}

# ============================================
# AGENT CONFIGURATION
# ============================================

def get_system_prompt(caller_phone=None, prefetch_result=None):
    """Generate system prompt, optionally with pre-fetched caller data"""
    
    prefetch_context = ""
    if prefetch_result and prefetch_result.get("found"):
        prefetch_context = f"""
## PRE-FETCHED CALLER DATA (from Caller ID: {caller_phone})
A record was found matching the caller's phone number:
- First Name: {prefetch_result.get('first_name', 'Unknown')}
- Last Name: {prefetch_result.get('last_name', 'Unknown')}  
- Language (SECRET - for verification only): {prefetch_result.get('language', 'Unknown')}
- Status Message: {prefetch_result.get('message', '')}

Since we found a record from the caller's phone number, when they ask about their application:
1. Say: "I see we have a record from this phone number. To verify your identity, could you please confirm your first and last name?"
2. If name matches, ask: "And what language did you apply to interpret for?"
3. If language matches, share the status message
4. If language doesn't match, give them another chance: "Hmm, that doesn't seem to match. Could you double-check the language you applied for?"
"""
    
    return f"""You are Angela, a virtual AI assistant for Alfa Systems, a language services company that connects clients with professional interpreters.

## GREETING
When the conversation starts, say: "Hi, this is Angela, your virtual assistant with Alfa Systems. How may I help you today?"

## STYLE
- Speak naturally and conversationally
- Keep responses to 2-3 sentences
- Be warm but professional
- Use the caller's first name once you know it
{prefetch_context}
## CAPABILITIES
- Answer questions about interpreter services
- Help check application status
- Explain the assessment and training process

## APPLICATION STATUS LOOKUP FLOW

### Step 1: Collect Information
When someone asks about their application status, collect these details ONE AT A TIME:

a) "May I have your first and last name, please?"
b) "And what's the best phone number to reach you?"
c) "And your email address?"

Once you have all three, say: "Thank you! Let me look up your application now."

Then call lookup_application_status with ALL the information (phone, email, first_name, last_name).

### Step 2: Phone Number Handling
- US phone numbers may or may not include country code "1"
- If someone says "nine five one four four zero nine five six seven", that's 9514409567
- If they say "one nine five one...", include the 1: 19514409567

### Step 3: Handle Lookup Results

IF FOUND - Verify identity first:
- Ask: "Thanks [Name]. For security, can you tell me what language you applied to interpret for?"
- If they say the correct language, share their status
- If wrong, say: "Hmm, that doesn't seem to match what I have on file. Could you double-check the language you applied for? If you're unsure, I can have someone from our team reach out to help."

IF NOT FOUND:
"I wasn't able to locate your application with that information. Let me connect you with one of our team members who can help, or you can email us and we'll look into it right away."

## WHAT NOT TO SHARE
- Never share the full email address back to them
- Never share internal assessment details or scores
- Never share tier classifications
- Only share scheduling info from notes

## GENERAL QUESTIONS
For questions about Alfa Systems, training, requirements, pay, or policies:
- Say: "Let me look that up for you"
- Call the search_knowledge_base function
- If the answer isn't in the knowledge base, say: "I don't have that specific information, but I can have someone from our team follow up with you."

## AUDIO ISSUES
- "I'm sorry, I didn't catch that. Could you say that again?"
- After 2 attempts: "We're having some audio trouble. Let me transfer you to a team member."

## LANGUAGE
- Start in English
- If caller speaks Spanish, switch to Spanish"""


KNOWLEDGE_BASE_ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID", "asst_6k8qTQnQx8aWS0RdPC8JX609")

AGENT_TOOLS = [
    {
        "type": "function",
        "name": "lookup_application_status",
        "description": "Look up a candidate's application status in the CRM system. Use this when you don't have pre-fetched data.",
        "parameters": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "The caller's phone number"},
                "email": {"type": "string", "description": "The caller's email address"},
                "first_name": {"type": "string", "description": "The caller's first name"},
                "last_name": {"type": "string", "description": "The caller's last name"}
            },
            "required": []
        }
    },
    {
        "type": "function",
        "name": "search_knowledge_base",
        "description": "Search the Alfa Systems knowledge base for information about interpreter services, requirements, training, pay, policies.",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question to search for"}
            },
            "required": ["question"]
        }
    }
]

# ============================================
# ZOHO CRM INTEGRATION
# ============================================

ZOHO_TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"
ZOHO_API_BASE = "https://www.zohoapis.com/crm/v2"


def sanitize_coql_input(value: str) -> str:
    if not value:
        return value
    return value.replace("'", "''")


async def get_zoho_access_token():
    client_id = os.getenv("ZOHO_CLIENT_ID")
    client_secret = os.getenv("ZOHO_CLIENT_SECRET")
    refresh_token = os.getenv("ZOHO_REFRESH_TOKEN")
    
    if not all([client_id, client_secret, refresh_token]):
        return None
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            ZOHO_TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token"
            }
        )
        
        if response.status_code != 200:
            print(f"Zoho token error: {response.text}")
            return None
        
        data = response.json()
        return data.get("access_token")


async def search_by_phone(phone: str):
    token = await get_zoho_access_token()
    if not token:
        return None
    
    clean_phone = ''.join(filter(str.isdigit, phone))
    safe_phone = sanitize_coql_input(clean_phone[-10:])
    
    async with httpx.AsyncClient() as client:
        leads_query = f"""
        SELECT First_Name, Last_Name, Email, Phone, Mobile, Lead_Status, Training_Status, 
               Training_Start_Date, Training_End_Date, Language 
        FROM Leads 
        WHERE (Phone like '%{safe_phone}%' OR Mobile like '%{safe_phone}%')
        LIMIT 1
        """
        
        response = await client.post(
            f"{ZOHO_API_BASE}/coql",
            headers={
                "Authorization": f"Zoho-oauthtoken {token}",
                "Content-Type": "application/json"
            },
            json={"select_query": leads_query}
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("data") and len(data["data"]) > 0:
                result = data["data"][0]
                result["_module"] = "Leads"
                return result
        
        return None


async def search_by_email(email: str):
    token = await get_zoho_access_token()
    if not token:
        return None
    
    safe_email = sanitize_coql_input(email)
    
    async with httpx.AsyncClient() as client:
        leads_query = f"""
        SELECT First_Name, Last_Name, Email, Phone, Mobile, Lead_Status, Training_Status, 
               Training_Start_Date, Training_End_Date, Language 
        FROM Leads 
        WHERE Email = '{safe_email}'
        LIMIT 1
        """
        
        response = await client.post(
            f"{ZOHO_API_BASE}/coql",
            headers={
                "Authorization": f"Zoho-oauthtoken {token}",
                "Content-Type": "application/json"
            },
            json={"select_query": leads_query}
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("data") and len(data["data"]) > 0:
                result = data["data"][0]
                result["_module"] = "Leads"
                return result
        
        return None


async def search_by_name(first_name: str, last_name: str):
    token = await get_zoho_access_token()
    if not token:
        return None
    
    safe_first = sanitize_coql_input(first_name)
    safe_last = sanitize_coql_input(last_name)
    
    async with httpx.AsyncClient() as client:
        leads_query = f"""
        SELECT First_Name, Last_Name, Email, Phone, Mobile, Lead_Status, Training_Status, 
               Training_Start_Date, Training_End_Date, Language 
        FROM Leads 
        WHERE First_Name = '{safe_first}' AND Last_Name = '{safe_last}'
        LIMIT 5
        """
        
        response = await client.post(
            f"{ZOHO_API_BASE}/coql",
            headers={
                "Authorization": f"Zoho-oauthtoken {token}",
                "Content-Type": "application/json"
            },
            json={"select_query": leads_query}
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("data") and len(data["data"]) > 0:
                if len(data["data"]) > 1:
                    return {"multiple_matches": True, "count": len(data["data"])}
                result = data["data"][0]
                result["_module"] = "Leads"
                return result
        
        return None


async def lookup_application_status(phone=None, email=None, first_name=None, last_name=None):
    contact = None
    
    if phone:
        contact = await search_by_phone(phone)
    
    if not contact and email:
        contact = await search_by_email(email)
    
    if not contact and first_name and last_name:
        contact = await search_by_name(first_name, last_name)
        if contact and contact.get("multiple_matches"):
            return {"found": False, "message": "Multiple candidates found with that name. Please provide your phone number or email."}
    
    if not contact:
        return {"found": False, "message": "I couldn't find a record with that information."}
    
    status = contact.get("Lead_Status") or "Unknown"
    training_status = contact.get("Training_Status")
    language = contact.get("Language")
    
    status_messages = {
        "Not Contacted": "We have received your application and will contact you soon.",
        "Contacted": "We have reached out to you. Please check your email or phone.",
        "Pre-Qualified": "Your application is currently being reviewed.",
        "Qualified": "Congratulations! You have been qualified. We will reach out with next steps.",
        "Not Qualified": "Unfortunately, your application did not meet our requirements at this time.",
        "Invited for training": "You have been invited for training. Please check your email.",
        "Scheduled for Next training": "You are scheduled for our next training session.",
        "Training completed successfully": "Congratulations! You have completed your training successfully.",
    }
    
    message = status_messages.get(status, f"Your current status is: {status}")
    
    return {
        "found": True,
        "first_name": contact.get("First_Name", ""),
        "last_name": contact.get("Last_Name", ""),
        "status": status,
        "language": language,
        "training_status": training_status,
        "message": message
    }


async def search_knowledge_base(question: str):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {"found": False, "answer": "Knowledge base unavailable."}
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            thread_resp = await client.post(
                "https://api.openai.com/v1/threads",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "OpenAI-Beta": "assistants=v2"
                },
                json={}
            )
            
            if thread_resp.status_code != 200:
                return {"found": False, "answer": "I'm having trouble accessing the knowledge base."}
            
            thread_id = thread_resp.json().get("id")
            
            await client.post(
                f"https://api.openai.com/v1/threads/{thread_id}/messages",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "OpenAI-Beta": "assistants=v2"
                },
                json={"role": "user", "content": question}
            )
            
            run_resp = await client.post(
                f"https://api.openai.com/v1/threads/{thread_id}/runs",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "OpenAI-Beta": "assistants=v2"
                },
                json={"assistant_id": KNOWLEDGE_BASE_ASSISTANT_ID}
            )
            
            if run_resp.status_code != 200:
                return {"found": False, "answer": "I'm having trouble searching the knowledge base."}
            
            run_id = run_resp.json().get("id")
            
            for _ in range(30):
                await asyncio.sleep(1)
                status_resp = await client.get(
                    f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "OpenAI-Beta": "assistants=v2"
                    }
                )
                status = status_resp.json().get("status")
                if status == "completed":
                    break
                elif status in ["failed", "cancelled", "expired"]:
                    return {"found": False, "answer": "I couldn't find that information."}
            
            msgs_resp = await client.get(
                f"https://api.openai.com/v1/threads/{thread_id}/messages",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "OpenAI-Beta": "assistants=v2"
                }
            )
            
            messages = msgs_resp.json().get("data", [])
            for msg in messages:
                if msg.get("role") == "assistant":
                    content = msg.get("content", [])
                    if content:
                        answer = content[0].get("text", {}).get("value", "")
                        return {"found": True, "answer": answer}
            
            return {"found": False, "answer": "I couldn't find that information."}
            
    except Exception as e:
        print(f"Knowledge base error: {e}")
        return {"found": False, "answer": "I'm having trouble with the knowledge base."}


# ============================================
# TWILIO WEBHOOK ENDPOINTS
# ============================================

@app.get("/")
async def root():
    return {"status": "Alfa Twilio Voice Agent running", "endpoints": ["/incoming-call", "/media-stream"]}


@app.api_route("/incoming-call", methods=["GET", "POST"])
async def incoming_call(request: Request):
    """Handle incoming Twilio calls - returns TwiML to connect to media stream"""
    caller_phone = ""
    call_sid = ""
    
    # Debug: Log everything
    if DEBUG: print(f"📥 Request method: {request.method}")
    if DEBUG: print(f"📥 Query params: {dict(request.query_params)}")
    if DEBUG: print(f"📥 Headers: {dict(request.headers)}")
    
    try:
        # Try body first (for POST)
        body = await request.body()
        body_str = body.decode('utf-8')
        if DEBUG: print(f"📥 Raw body ({len(body_str)} chars): {body_str[:300]}")
        
        if body_str:
            from urllib.parse import parse_qs
            params = parse_qs(body_str)
            caller_phone = params.get("From", [""])[0]
            call_sid = params.get("CallSid", [""])[0]
        
        # Fallback to query params
        if not caller_phone:
            caller_phone = request.query_params.get("From", "")
        if not call_sid:
            call_sid = request.query_params.get("CallSid", "")
            
    except Exception as e:
        print(f"❌ Error parsing request: {e}")
        import traceback
        traceback.print_exc()
    
    print(f"📞 Incoming call from: {caller_phone}, CallSid: {call_sid}")
    
    # Pre-fetch caller data from CRM
    prefetch_result = None
    if caller_phone:
        print(f"🔍 Pre-fetching data for {caller_phone}...")
        prefetch_result = await lookup_application_status(phone=caller_phone)
        if prefetch_result and prefetch_result.get("found"):
            print(f"✅ Found record: {prefetch_result.get('first_name')} {prefetch_result.get('last_name')}")
        else:
            print(f"❌ No record found for {caller_phone}")
    
    # Store in cache for WebSocket to use
    if call_sid:
        caller_info_cache[call_sid] = {
            "phone": caller_phone,
            "prefetch_result": prefetch_result
        }
    
    host = request.headers.get("host", "localhost")
    ws_url = f"wss://{host}/media-stream"
    
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{ws_url}">
            <Parameter name="callSid" value="{call_sid}"/>
        </Stream>
    </Connect>
</Response>"""
    
    return HTMLResponse(content=twiml, media_type="application/xml")


@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    """WebSocket endpoint for Twilio Media Streams"""
    await websocket.accept()
    
    print("🔌 WebSocket accepted from Twilio")
    
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        print("❌ OPENAI_API_KEY not configured")
        await websocket.close()
        return
    
    stream_sid = None
    call_sid = None
    openai_ws = None
    caller_phone = None
    prefetch_result = None
    
    try:
        # Connect to OpenAI Realtime API
        print("🤖 Connecting to OpenAI Realtime API...")
        openai_ws = await websockets.client.connect(
            "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17",
            extra_headers={
                "Authorization": f"Bearer {openai_api_key}",
                "OpenAI-Beta": "realtime=v1"
            }
        )
        print("✅ Connected to OpenAI Realtime API")
        
        async def configure_session():
            """Configure OpenAI session with caller-specific prompt"""
            system_prompt = get_system_prompt(caller_phone, prefetch_result)
            
            session_config = {
                "type": "session.update",
                "session": {
                    "turn_detection": {"type": "server_vad"},
                    "input_audio_format": "g711_ulaw",
                    "input_audio_transcription": {"model": "whisper-1"},
                    "output_audio_format": "g711_ulaw",
                    "voice": "alloy",
                    "instructions": system_prompt,
                    "modalities": ["text", "audio"],
                    "temperature": 0.8,
                    "tools": AGENT_TOOLS
                }
            }
            await openai_ws.send(json.dumps(session_config))
            print("📤 Sent session config to OpenAI")
        
        async def receive_from_twilio():
            """Receive audio from Twilio and forward to OpenAI"""
            nonlocal stream_sid, call_sid, caller_phone, prefetch_result
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    event = data.get("event")
                    
                    if event == "connected":
                        print("📱 Twilio connected event received")
                        
                    elif event == "start":
                        stream_sid = data["start"]["streamSid"]
                        
                        # Get CallSid from custom parameters
                        custom_params = data["start"].get("customParameters", {})
                        call_sid = custom_params.get("callSid")
                        
                        print(f"📱 Twilio stream started: {stream_sid}, CallSid: {call_sid}")
                        
                        # Retrieve pre-fetched caller info
                        if call_sid and call_sid in caller_info_cache:
                            cached = caller_info_cache[call_sid]
                            caller_phone = cached.get("phone")
                            prefetch_result = cached.get("prefetch_result")
                            print(f"📋 Retrieved caller info: {caller_phone}, found={prefetch_result.get('found') if prefetch_result else False}")
                            # Clean up cache
                            del caller_info_cache[call_sid]
                        
                        # Configure session with caller-specific prompt
                        await configure_session()
                        
                        # Trigger initial greeting
                        await openai_ws.send(json.dumps({
                            "type": "response.create",
                            "response": {"modalities": ["text", "audio"]}
                        }))
                        print("📤 Triggered initial greeting")
                        
                    elif event == "media":
                        audio_payload = data["media"]["payload"]
                        await openai_ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": audio_payload
                        }))
                        
                    elif event == "stop":
                        print("📱 Twilio stream stopped")
                        break
                        
            except Exception as e:
                print(f"❌ Twilio receive error: {e}")
        
        async def receive_from_openai():
            """Receive responses from OpenAI and forward to Twilio"""
            nonlocal stream_sid
            try:
                async for message in openai_ws:
                    data = json.loads(message)
                    event_type = data.get("type", "")
                    
                    if event_type == "session.created":
                        print("✅ OpenAI session created")
                        
                    elif event_type == "session.updated":
                        print("✅ OpenAI session updated")
                    
                    elif event_type == "response.audio.delta":
                        if stream_sid:
                            audio_delta = data.get("delta", "")
                            await websocket.send_json({
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {"payload": audio_delta}
                            })
                    
                    elif event_type == "response.audio_transcript.delta":
                        transcript = data.get("delta", "")
                        if transcript:
                            print(f"🗣️ AI: {transcript}", end="", flush=True)
                    
                    elif event_type == "response.audio_transcript.done":
                        print()
                    
                    elif event_type == "input_audio_buffer.speech_started":
                        print("🎤 User speaking...")
                    
                    elif event_type == "input_audio_buffer.speech_stopped":
                        print("🎤 User stopped speaking")
                    
                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        transcript = data.get("transcript", "")
                        print(f"👤 User said: {transcript}")
                    
                    elif event_type == "response.function_call_arguments.done":
                        call_id = data.get("call_id")
                        name = data.get("name")
                        args_str = data.get("arguments", "{}")
                        
                        print(f"🔧 Function call: {name}")
                        
                        try:
                            args = json.loads(args_str)
                        except:
                            args = {}
                        
                        if name == "lookup_application_status":
                            result = await lookup_application_status(
                                phone=args.get("phone"),
                                email=args.get("email"),
                                first_name=args.get("first_name"),
                                last_name=args.get("last_name")
                            )
                        elif name == "search_knowledge_base":
                            result = await search_knowledge_base(args.get("question", ""))
                        else:
                            result = {"error": f"Unknown function: {name}"}
                        
                        print(f"📋 Function result: {json.dumps(result)[:200]}")
                        
                        await openai_ws.send(json.dumps({
                            "type": "conversation.item.create",
                            "item": {
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": json.dumps(result)
                            }
                        }))
                        
                        await openai_ws.send(json.dumps({"type": "response.create"}))
                    
                    elif event_type == "error":
                        print(f"❌ OpenAI error: {data.get('error', {})}")
                    
                    elif event_type == "response.done":
                        print("✅ Response complete")
                        
            except Exception as e:
                print(f"❌ OpenAI receive error: {e}")
        
        # Run both receive loops concurrently
        await asyncio.gather(
            receive_from_twilio(),
            receive_from_openai()
        )
            
    except Exception as e:
        print(f"❌ WebSocket error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if openai_ws:
            await openai_ws.close()
        print("📞 Call ended")


@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    print("\n📞 Alfa Twilio Voice Agent")
    print("   Configure Twilio webhook: https://your-domain/incoming-call\n")
    uvicorn.run(app, host="0.0.0.0", port=8005)
